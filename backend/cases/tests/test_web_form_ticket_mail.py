"""A public web form ticket is not mailed until an agent has answered it.

Anybody can post a ticket form with any address, and the service attaches a
contact for that address. Status and CSAT emails to it would let a stranger
make the org's sender write to anyone, under a subject the stranger chose. An
agent's public reply is the deliberate decision to engage; from then on the
ticket is mailed like any other.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.utils import timezone

from cases.models import Case, CsatSurvey
from cases.tasks import notify_portal_contacts, send_csat_survey
from common.models import Comment
from contacts.models import Contact
from webforms.models import WebForm
from webforms.service import submit_form

VICTIM = "victim@example.com"


@pytest.fixture
def web_form_ticket(org_a):
    form = WebForm.objects.create(
        name="Support request",
        org=org_a,
        target=WebForm.TARGET_TICKET,
        is_published=True,
    )
    submission = submit_form(
        form, {"email": VICTIM, "name": "You won a prize, click here"}
    )
    return submission.case


@pytest.fixture
def agent_ticket(org_a):
    case = Case.objects.create(
        org=org_a, name="Printer on fire", status="New", priority="Normal"
    )
    case.contacts.add(
        Contact.objects.create(org=org_a, first_name="Pat", email="pat@example.com")
    )
    return case


def _comment(case, **author):
    return Comment.objects.create(
        org=case.org,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment="Thanks, looking now.",
        is_internal=author.pop("is_internal", False),
        **author,
    )


def _close(case):
    case.status = "Closed"
    case.closed_on = timezone.localdate()
    case.save()


def _status_and_csat(case):
    """Run the two tasks a close queues, and return who was mailed."""
    mail.outbox.clear()
    notify_portal_contacts(str(case.id), str(case.org_id), "status")
    send_csat_survey(str(case.id), str(case.org_id))
    return [address for message in mail.outbox for address in message.to]


@pytest.mark.django_db
class TestUnansweredWebFormTicket:
    def test_closing_a_spam_ticket_mails_nobody(self, web_form_ticket):
        assert list(web_form_ticket.contacts.values_list("email", flat=True)) == [
            VICTIM
        ]
        _close(web_form_ticket)
        assert _status_and_csat(web_form_ticket) == []
        assert not CsatSurvey.objects.filter(case=web_form_ticket).exists()

    def test_an_internal_note_is_not_an_answer(self, web_form_ticket, admin_profile):
        _comment(web_form_ticket, commented_by=admin_profile, is_internal=True)
        _close(web_form_ticket)
        assert _status_and_csat(web_form_ticket) == []

    def test_the_submitters_own_comment_is_not_an_answer(self, web_form_ticket):
        contact = web_form_ticket.contacts.get()
        _comment(web_form_ticket, commented_by_contact=contact)
        _close(web_form_ticket)
        assert _status_and_csat(web_form_ticket) == []


@pytest.mark.django_db
class TestAnsweredWebFormTicket:
    def test_the_agents_reply_is_emailed(self, web_form_ticket, admin_profile):
        comment = _comment(web_form_ticket, commented_by=admin_profile)
        mail.outbox.clear()
        sent = notify_portal_contacts(
            str(web_form_ticket.id),
            str(web_form_ticket.org_id),
            "reply",
            comment_id=str(comment.id),
        )
        assert sent == 1
        assert mail.outbox[0].to == [VICTIM]

    def test_after_an_agent_reply_status_and_csat_go_out(
        self, web_form_ticket, admin_profile
    ):
        _comment(web_form_ticket, commented_by=admin_profile)
        web_form_ticket.refresh_from_db()
        assert web_form_ticket.first_response_at is not None
        _close(web_form_ticket)
        assert _status_and_csat(web_form_ticket) == [VICTIM, VICTIM]
        assert CsatSurvey.objects.filter(case=web_form_ticket).exists()


@pytest.mark.django_db
class TestOtherTicketsUnaffected:
    def test_an_agent_created_ticket_is_mailed_without_a_reply(self, agent_ticket):
        _close(agent_ticket)
        assert _status_and_csat(agent_ticket) == [
            "pat@example.com",
            "pat@example.com",
        ]

    def test_an_email_born_ticket_is_mailed_without_a_reply(self, agent_ticket):
        agent_ticket.external_thread_id = "root@customer.example"
        agent_ticket.save()
        _close(agent_ticket)
        assert _status_and_csat(agent_ticket) == [
            "pat@example.com",
            "pat@example.com",
        ]
