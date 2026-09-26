"""Every link in an outgoing email points at the web app and at a real record.

These emails had a button whose ``href`` was ``settings.DOMAIN_NAME``, which
names the **API** host, with no path on it at all: the assignee got a "View
Lead" button that opened the Django host's root. `backend/.env` sets
``DOMAIN_NAME=""``, so in development the attribute was the empty string and
the rendered ``href=""`` reloaded whatever page the reader was on. Two other
templates appended a frontend path (``/goals``, ``/opportunities?rotten=true``)
to that backend base, and the second of those is not even a route the frontend
serves; it is ``/pipeline``.

``common.links.frontend_url`` already existed for exactly this, built when the
invoice and estimate portal links had the same defect. The rule these tests
pin: an email link is built from ``FRONTEND_URL`` and carries the path of the
thing the mail is about.

They assert on the rendered HTML rather than the context dict, because the
context key is not the contract, the anchor is: ``leads_assigned.html`` read
``{{ url|default:lead_detail_url }}`` and its two senders each filled a
different one of those names.
"""

from __future__ import annotations

import re

import pytest
from crum import impersonate
from django.core import mail

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from leads.models import Lead
from opportunity.models import Opportunity

FRONTEND = "https://app.example.com"


def _hrefs(message):
    """Every anchor target in the message body."""
    return re.findall(r'href="([^"]*)"', message.body)


def _button_href(message):
    """The single link the reader is meant to click.

    The templates carry one anchor; if that ever stops being true this raises
    rather than silently checking the wrong one.
    """
    hrefs = [h for h in _hrefs(message) if not h.startswith("mailto:")]
    assert len(hrefs) == 1, f"expected one link, found {hrefs}"
    return hrefs[0]


@pytest.fixture(autouse=True)
def _outbox_and_frontend(settings):
    """A known `FRONTEND_URL` and an empty outbox for every test here.

    `django.test.override_settings` cannot decorate a plain pytest class, so
    this is the pytest-django `settings` fixture instead.
    """
    settings.FRONTEND_URL = FRONTEND
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.mark.django_db
class TestAssignmentEmailsLinkToTheRecord:
    def test_lead_assignment_links_to_the_lead(self, org_a, admin_user, admin_profile):
        from leads.tasks import send_email_to_assigned_user

        with impersonate(admin_user):
            lead = Lead.objects.create(title="Roof job", org=org_a, status="assigned")

        send_email_to_assigned_user([admin_profile.id], lead.id, str(org_a.id))

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/leads/{lead.id}"

    def test_contact_assignment_links_to_the_contact(
        self, org_a, admin_user, admin_profile
    ):
        from contacts.tasks import send_email_to_assigned_user

        with impersonate(admin_user):
            contact = Contact.objects.create(first_name="Dana", org=org_a)

        send_email_to_assigned_user([admin_profile.id], contact.id, str(org_a.id))

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/contacts/{contact.id}"

    def test_account_assignment_links_to_the_account(
        self, org_a, admin_user, admin_profile
    ):
        from accounts.tasks import send_email_to_assigned_user

        with impersonate(admin_user):
            account = Account.objects.create(name="Northwind", org=org_a)

        send_email_to_assigned_user([admin_profile.id], account.id, str(org_a.id))

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/accounts/{account.id}"

    def test_case_assignment_links_to_the_ticket_path(
        self, org_a, admin_user, admin_profile
    ):
        """`/tickets/<id>`, not `/cases/<id>`.

        The model says case and every client routes it at `/tickets`, the same
        mismatch `cases.notifications.case_link` exists to absorb. This calls
        that function rather than re-deriving the path.
        """
        from cases.tasks import send_email_to_assigned_user

        with impersonate(admin_user):
            case = Case.objects.create(
                name="Boiler down", org=org_a, status="New", priority="Normal"
            )

        send_email_to_assigned_user([admin_profile.id], case.id, str(org_a.id))

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/tickets/{case.id}"

    def test_opportunity_assignment_links_to_the_pipeline_path(
        self, org_a, admin_user, admin_profile
    ):
        """`/pipeline/<id>`: the frontend has no `/opportunities` route."""
        from opportunity.tasks import send_email_to_assigned_user

        with impersonate(admin_user):
            deal = Opportunity.objects.create(
                name="Retrofit", org=org_a, stage="QUALIFICATION"
            )

        send_email_to_assigned_user([admin_profile.id], deal.id, str(org_a.id))

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/pipeline/{deal.id}"


@pytest.mark.django_db
class TestTheSecondLeadMailerAgreesWithTheFirst:
    """Two tasks send "you were assigned a lead" from the same template.

    They filled different context keys, so the template hedged with
    ``{{ url|default:lead_detail_url }}``. Both now fill ``url`` with the same
    value, which is what lets the template say ``{{ url }}`` like its five
    siblings.
    """

    def test_the_webhook_mailer_links_to_the_lead(
        self, org_a, admin_user, admin_profile
    ):
        """This one hands the rendered HTML to a second task to send.

        `send_email.delay(...)` needs a broker, and the test settings point
        Celery at `memory://` without eager mode, so the mail never leaves the
        queue. The assertion is on what this task renders and dispatches,
        which is the part under test.
        """
        from unittest.mock import patch

        from leads.tasks import send_lead_assigned_emails

        with impersonate(admin_user):
            lead = Lead.objects.create(title="Web form", org=org_a, status="assigned")

        with patch("leads.tasks.send_email.delay") as dispatched:
            send_lead_assigned_emails(lead.id, [admin_profile.id], str(org_a.id))

        assert dispatched.call_count == 1
        html = dispatched.call_args.kwargs["html_content"]
        assert f'href="{FRONTEND}/leads/{lead.id}"' in html
        # The render itself is the other half: this template used to raise
        # `VariableDoesNotExist` for whichever sender filled the other key.
        assert "Web form" in html

    def test_it_no_longer_takes_a_host_supplied_base(self):
        """The dropped argument was ``request.META["HTTP_HOST"]``.

        Its one caller is the website-lead webhook, so the base of a link in
        mail this system sends to its own staff came off the wire. Pinning the
        signature keeps a future caller from reintroducing it.
        """
        import inspect

        from leads.tasks import send_lead_assigned_emails

        params = list(inspect.signature(send_lead_assigned_emails.run).parameters)
        assert params == ["lead_id", "new_assigned_to_list", "org_id"]


@pytest.mark.django_db
class TestAlertEmailsLinkToTheirPage:
    def test_stale_deal_alert_links_to_the_rotten_filter(
        self, org_a, admin_user, admin_profile
    ):
        from opportunity.tasks import send_stale_deals_alert

        with impersonate(admin_user):
            deal = Opportunity.objects.create(
                name="Stalled", org=org_a, stage="QUALIFICATION"
            )

        send_stale_deals_alert(org_a, [(deal, "Qualification", 40, 14)])

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/pipeline?rotten=true"

    def test_goal_milestone_links_to_goals(self, org_a, admin_user, admin_profile):
        from opportunity.models import SalesGoal
        from opportunity.tasks import _send_goal_milestone_email

        with impersonate(admin_user):
            goal = SalesGoal.objects.create(
                name="Q3 revenue",
                org=org_a,
                target_value=10000,
                period_start="2026-07-01",
                period_end="2026-09-30",
            )

        _send_goal_milestone_email(admin_profile, goal, "50%", 50, 5000)

        assert len(mail.outbox) == 1
        assert _button_href(mail.outbox[0]) == f"{FRONTEND}/goals"


@pytest.mark.django_db
def test_a_trailing_slash_on_the_setting_does_not_double_up(
    settings, org_a, admin_user, admin_profile
):
    """An operator who sets `FRONTEND_URL=https://app.example.com/` is not wrong.

    `frontend_url` strips it, and this is the assertion that keeps the callers
    from concatenating by hand again.
    """
    settings.FRONTEND_URL = f"{FRONTEND}/"

    from leads.tasks import send_email_to_assigned_user

    with impersonate(admin_user):
        lead = Lead.objects.create(title="Slash", org=org_a, status="assigned")

    send_email_to_assigned_user([admin_profile.id], lead.id, str(org_a.id))

    assert _button_href(mail.outbox[0]) == f"{FRONTEND}/leads/{lead.id}"
