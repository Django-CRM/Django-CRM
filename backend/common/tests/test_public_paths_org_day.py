"""Anonymous endpoints and Celery tasks keep the org's day, not the server's.

No one is signed in on a public link, a web form or an inbound-mail webhook,
so the middleware finds no org and the server's UTC day is active. Each of
these paths resolves its org from the token, form or mailbox and now activates
that org's timezone as well; a Celery task does the same around its day math.

The clock is frozen at 23:30 UTC on 31 December, which is already 1 January in
Asia/Kolkata. Every test pairs the Kolkata org with a UTC org given the same
data, in that order, so a day computed in UTC fails the first half and a
Kolkata zone left active fails the second.
"""

import datetime
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from cases.inbound.parser import parse_raw_email
from cases.inbound.pipeline import ingest
from cases.models import Case
from cases.tests.test_inbound_email import SNS_TOPIC, _make_mailbox, _notification
from cases.tests.test_inbound_email import _raw_email as raw_email
from common.portal_auth import mint_portal_token
from conftest import rls_org
from contacts.models import Contact
from invoices.models import Estimate, Invoice
from invoices.tasks import send_payment_reminder
from webforms.models import WebForm, WebFormDailyStat

pytestmark = pytest.mark.django_db

NEW_YEARS_EVE_2330_UTC = datetime.datetime(
    2026, 12, 31, 23, 30, tzinfo=datetime.timezone.utc
)
NEW_YEARS_EVE = datetime.date(2026, 12, 31)
NEW_YEARS_DAY = datetime.date(2027, 1, 1)


@pytest.fixture
def frozen():
    """`localdate()` reads the module-level `now`, so this moves both."""
    with patch("django.utils.timezone.now", return_value=NEW_YEARS_EVE_2330_UTC):
        yield


@pytest.fixture
def kolkata(org_a):
    org_a.timezone = "Asia/Kolkata"
    org_a.save(update_fields=["timezone"])
    return org_a


@pytest.fixture
def utc(org_b):
    assert org_b.timezone == "UTC"
    return org_b


def test_the_premise_the_two_days_differ(frozen):
    assert timezone.now().date() == NEW_YEARS_EVE
    with timezone.override("Asia/Kolkata"):
        assert timezone.localdate() == NEW_YEARS_DAY


class TestPublicEstimateAccept:
    """`is_expired` compares the expiry with the day; accepting past it is refused."""

    def _estimate(self, org):
        with rls_org(org):
            return Estimate.objects.create(
                title="Fit-out",
                client_name="Dana",
                client_email="dana@buyer.example",
                status="Sent",
                issue_date=datetime.date(2026, 12, 1),
                expiry_date=NEW_YEARS_EVE,
                org=org,
            )

    def _accept(self, estimate):
        return APIClient().post(
            f"/api/public/estimate/{estimate.public_token}/accept/",
            {"name": "Dana", "email": "dana@buyer.example"},
            format="json",
        )

    def test_expired_in_kolkata_still_valid_in_utc(self, kolkata, utc, frozen):
        in_kolkata = self._estimate(kolkata)
        in_utc = self._estimate(utc)

        refused = self._accept(in_kolkata)
        accepted = self._accept(in_utc)

        assert refused.status_code == 400
        assert "expired" in refused.json()["message"]
        assert accepted.status_code == 200, accepted.content
        with rls_org(utc):
            in_utc.refresh_from_db()
        assert in_utc.status == "Accepted"
        assert timezone.get_current_timezone_name() == "UTC"


class TestWebFormDailyViews:
    def _form(self, org):
        with rls_org(org):
            return WebForm.objects.create(name="Contact", org=org, is_published=True)

    def test_a_view_is_counted_on_the_org_day(self, kolkata, utc, frozen):
        in_kolkata = self._form(kolkata)
        in_utc = self._form(utc)
        client = APIClient()

        first = client.get(f"/api/public/forms/{kolkata.id}/{in_kolkata.id}/embed/")
        second = client.get(f"/api/public/forms/{utc.id}/{in_utc.id}/embed/")

        assert (first.status_code, second.status_code) == (200, 200)
        with rls_org(kolkata):
            assert WebFormDailyStat.objects.get(form=in_kolkata).date == NEW_YEARS_DAY
        with rls_org(utc):
            assert WebFormDailyStat.objects.get(form=in_utc).date == NEW_YEARS_EVE


class TestReopenWindowOnTheOrgDay:
    """Closed on 24 December with the default 7-day window.

    On 31 December that is 7 days, inside the window; on 1 January it is 8,
    outside it. So the same reply reopens the UTC org's ticket and not the
    Kolkata org's.
    """

    CLOSED_ON = datetime.date(2026, 12, 24)

    def _closed_case(self, org, address):
        mailbox = _make_mailbox(org, address=address)
        with rls_org(org):
            case = ingest(
                parse_raw_email(
                    raw_email(message_id=f"<first.{org.id}@example.com>", to=address)
                ),
                mailbox,
            ).case
            case.status = "Closed"
            case.closed_on = self.CLOSED_ON
            case.save()
        return mailbox, case

    def _status(self, org, case):
        with rls_org(org):
            return Case.objects.get(pk=case.pk).status

    def test_an_inbound_reply(self, kolkata, utc, frozen, settings):
        # `RequireOrgContext` does not exempt `/api/cases/inbound/`, so today an
        # anonymous SNS post is refused 403 before the view runs (every other
        # webhook test signs in, which hides that). Dropping it here runs the
        # view the way it would run once exempt; `GetProfileAndOrg`, which
        # deactivates the zone after the request, stays in.
        settings.MIDDLEWARE = [
            m
            for m in settings.MIDDLEWARE
            if m != "common.middleware.rls_context.RequireOrgContext"
        ]
        kolkata_box, kolkata_case = self._closed_case(kolkata, "help@kolkata.example")
        utc_box, utc_case = self._closed_case(utc, "help@utc.example")
        client = APIClient()

        with patch("cases.inbound_views.verify_sns_message"):
            for mailbox in (kolkata_box, utc_box):
                reply = raw_email(
                    message_id=f"<reply.{mailbox.org_id}@example.com>",
                    in_reply_to=f"<first.{mailbox.org_id}@example.com>",
                    to=mailbox.address,
                )
                response = client.post(
                    f"/api/cases/inbound/{mailbox.id}/",
                    _notification(SNS_TOPIC, reply),
                    format="json",
                )
                assert response.status_code == 200, response.content

        assert self._status(kolkata, kolkata_case) == "Closed"
        assert self._status(utc, utc_case) == "Pending"

    def test_a_portal_reply(self, kolkata, utc, frozen):
        """Portal requests carry a token, so the middleware sets the org day."""
        cases = {}
        for org in (kolkata, utc):
            with rls_org(org):
                contact = Contact.objects.create(
                    org=org, first_name="Pat", email=f"pat@{org.id}.example"
                )
                case = Case.objects.create(
                    org=org,
                    name="Login",
                    status="Closed",
                    priority="High",
                    closed_on=self.CLOSED_ON,
                )
                case.contacts.add(contact)
            cases[org] = (contact, case)

        for org in (kolkata, utc):
            contact, case = cases[org]
            client = APIClient()
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {mint_portal_token(contact)}"
            )
            response = client.post(
                f"/api/portal/cases/{case.id}/comment/",
                {"comment": "Still broken"},
                format="json",
            )
            assert response.status_code == 201, response.content

        assert self._status(kolkata, cases[kolkata][1]) == "Closed"
        assert self._status(utc, cases[utc][1]) == "Pending"


class TestPaymentReminderTask:
    """A worker runs no middleware; the task sets the org day for its count."""

    def _invoice(self, org, number):
        with rls_org(org):
            return Invoice.objects.create(
                invoice_number=number,
                invoice_title="Hosting",
                client_name="Dana",
                client_email="dana@buyer.example",
                status="Sent",
                issue_date=datetime.date(2026, 12, 1),
                due_date=datetime.date(2026, 12, 30),
                total_amount=100,
                org=org,
            )

    def test_days_overdue_on_the_org_day_and_no_zone_left_behind(
        self, kolkata, utc, frozen
    ):
        in_kolkata = self._invoice(kolkata, "INV-K-1")
        in_utc = self._invoice(utc, "INV-U-1")

        send_payment_reminder(str(in_kolkata.id), str(kolkata.id))
        assert timezone.get_current_timezone_name() == "UTC"
        send_payment_reminder(str(in_utc.id), str(utc.id))

        kolkata_mail, utc_mail = mail.outbox
        assert "2 days overdue" in kolkata_mail.body
        assert "1 days overdue" in utc_mail.body
        assert timezone.get_current_timezone_name() == "UTC"
