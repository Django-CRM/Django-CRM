"""D44: every row in the Today queue offers an action its caller can take.

D40 narrowed each Today source by its read rule. For tickets that admits
watchers, so a ticket a member only watches landed in "awaiting first reply"
with a "Reply" button, and the reply endpoint (``CaseDetailView.post``, the
write rule in ``cases/access.py``) refused it. The ticket source now uses the
write rule. Deals and tasks offer "Open", invoices "Send a reminder", and each
of those endpoints checks the same rule the source is narrowed by, so those
three were already actionable; the last test pins the invoice one.
"""

import datetime

import pytest
from django.utils import timezone
from rest_framework import status

from cases.models import Case, CaseWatcher
from invoices.models import Invoice

pytestmark = pytest.mark.django_db

URL = "/api/dashboard/today/"


def _stamp(model, obj, user):
    model.objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj


@pytest.fixture
def unanswered_case(org_a, admin_user):
    """A colleague's ticket awaiting a first reply."""
    case = Case.objects.create(
        name="Awaiting reply",
        status="New",
        priority="High",
        org=org_a,
        first_response_at=None,
    )
    return _stamp(Case, case, admin_user)


def _today(client):
    response = client.get(URL)
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.data


def _row(data, case):
    return next((r for r in data["queue"] if r["id"] == f"case-{case.id}"), None)


def _ticket_count(data):
    return sum(
        s["count"] for s in data["summary"]["sources"] if s["href"] == "/tickets"
    )


class TestTicketsAreOfferedOnlyToThoseWhoCanReply:
    def test_watcher_only_ticket_is_not_offered(
        self, user_profile, user_client, unanswered_case
    ):
        CaseWatcher.objects.create(
            case=unanswered_case, profile=user_profile, org=unanswered_case.org
        )
        data = _today(user_client)
        assert _row(data, unanswered_case) is None
        # The header count is of rows that want the caller, so it agrees.
        assert _ticket_count(data) == 0
        assert data["summary"]["count"] == 0

    def test_assignees_ticket_is_offered_with_reply(
        self, user_profile, user_client, unanswered_case
    ):
        unanswered_case.assigned_to.add(user_profile)
        data = _today(user_client)
        row = _row(data, unanswered_case)
        assert row is not None
        assert row["action"] == "Reply"
        assert _ticket_count(data) == 1

    def test_the_reply_it_offers_is_accepted_and_the_one_it_hides_is_not(
        self, user_profile, user_client, unanswered_case
    ):
        """The queue's choice is the reply endpoint's own answer."""
        CaseWatcher.objects.create(
            case=unanswered_case, profile=user_profile, org=unanswered_case.org
        )
        url = f"/api/cases/{unanswered_case.pk}/"
        body = {"comment": "On it"}
        assert (
            user_client.post(url, body, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )
        unanswered_case.assigned_to.add(user_profile)
        assert (
            user_client.post(url, body, format="json").status_code
            != status.HTTP_403_FORBIDDEN
        )

    def test_admin_is_offered_a_ticket_they_neither_opened_nor_hold(
        self, admin_client, regular_user, unanswered_case
    ):
        _stamp(Case, unanswered_case, regular_user)
        assert _row(_today(admin_client), unanswered_case) is not None


class TestInvoicesAreActionableByWhoeverSeesThem:
    def test_assigned_invoice_is_offered_and_its_send_is_accepted(
        self, org_a, admin_user, user_profile, user_client
    ):
        inv = Invoice.objects.create(
            invoice_title="Overdue",
            org=org_a,
            currency="USD",
            total_amount=100,
            status="Sent",
            due_date=timezone.localdate() - datetime.timedelta(days=3),
        )
        _stamp(Invoice, inv, admin_user)
        assert f"invoice-{inv.id}" not in {
            r["id"] for r in _today(user_client)["queue"]
        }
        inv.assigned_to.add(user_profile)
        row = next(
            r for r in _today(user_client)["queue"] if r["id"] == f"invoice-{inv.id}"
        )
        assert row["action"] == "Send a reminder"
        send = user_client.post(f"/api/invoices/{inv.id}/send/", {}, format="json")
        assert send.status_code == status.HTTP_200_OK
