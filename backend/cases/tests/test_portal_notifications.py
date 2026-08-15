"""Who gets told about a case update, and who does not.

The portal is only useful if something brings the customer back to it, and it
is only safe if that something does not carry a credential in the email.
"""

from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail

from cases.models import Case
from cases.tasks import notify_portal_contacts
from common.models import Comment
from contacts.models import Contact


@pytest.fixture
def shared_case(org_a):
    case = Case.objects.create(
        org=org_a, name="Shared ticket", status="New", priority="Normal"
    )
    pat = Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )
    jo = Contact.objects.create(
        org=org_a, first_name="Jo", last_name="Blake", email="jo@example.com"
    )
    silent = Contact.objects.create(org=org_a, first_name="No", last_name="Email")
    case.contacts.add(pat, jo, silent)
    return case, pat, jo, silent


def _recipients():
    return {address for message in mail.outbox for address in message.to}


@pytest.mark.django_db
def test_notifies_every_contact_with_an_email(shared_case, org_a):
    case, _, _, _ = shared_case
    mail.outbox.clear()
    notify_portal_contacts(str(case.id), str(org_a.id), "reply")
    assert _recipients() == {"pat@example.com", "jo@example.com"}


@pytest.mark.django_db
def test_does_not_notify_the_contact_who_caused_it(shared_case, org_a):
    """Nobody is emailed about their own reply."""
    case, pat, _, _ = shared_case
    mail.outbox.clear()
    notify_portal_contacts(
        str(case.id), str(org_a.id), "reply", actor_contact_id=str(pat.id)
    )
    assert _recipients() == {"jo@example.com"}


@pytest.mark.django_db
def test_skips_inactive_contacts(shared_case, org_a):
    case, pat, _, _ = shared_case
    pat.is_active = False
    pat.save()
    mail.outbox.clear()
    notify_portal_contacts(str(case.id), str(org_a.id), "status")
    assert _recipients() == {"jo@example.com"}


@pytest.mark.django_db
def test_no_credential_travels_in_the_email(shared_case, org_a):
    """The link goes to a page that requires signing in, and carries no token."""
    case, _, _, _ = shared_case
    mail.outbox.clear()
    notify_portal_contacts(str(case.id), str(org_a.id), "status")
    body = mail.outbox[0].body
    assert "token=" not in body
    assert "code=" not in body
    assert f"/portal/cases/{case.id}" in body


@pytest.mark.django_db
def test_the_link_carries_the_org_so_signing_in_is_possible(shared_case, org_a):
    """Without this the recipient lands on the staff login, which they cannot use.

    Most people open this email on a phone, which is usually not the device
    they last signed in on, so there is no portal cookie to fall back to and
    the org id in the URL is the only thing that names their sign-in page.
    """
    case, _, _, _ = shared_case
    mail.outbox.clear()
    notify_portal_contacts(str(case.id), str(org_a.id), "reply")
    assert f"/portal/cases/{case.id}?org={org_a.id}" in mail.outbox[0].body


@pytest.mark.django_db
def test_unknown_case_is_a_no_op(org_a):
    import uuid

    mail.outbox.clear()
    assert notify_portal_contacts(str(uuid.uuid4()), str(org_a.id), "reply") is None
    assert mail.outbox == []


@pytest.mark.django_db
class TestSignalWiring:
    """The signal's job is to enqueue, and that is what these assert.

    Nothing in this suite runs Celery eagerly, so asserting on mail.outbox here
    would test the broker rather than the wiring. The sending itself is covered
    above by calling the task directly.
    """

    @pytest.fixture(autouse=True)
    def enqueued(self):
        with patch("cases.tasks.notify_portal_contacts.delay") as delay:
            yield delay

    def test_agent_public_reply_enqueues(
        self, shared_case, org_a, admin_profile, enqueued
    ):
        case, _, _, _ = shared_case
        Comment.objects.create(
            org=org_a,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment="We are on it.",
            commented_by=admin_profile,
            is_internal=False,
        )
        enqueued.assert_called_once_with(
            str(case.id), str(org_a.id), "reply", actor_contact_id=None
        )

    def test_internal_note_enqueues_nothing(
        self, shared_case, org_a, admin_profile, enqueued
    ):
        """The whole point of is_internal. A leak here would be the worst case."""
        case, _, _, _ = shared_case
        Comment.objects.create(
            org=org_a,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment="Deprioritise this one.",
            commented_by=admin_profile,
            is_internal=True,
        )
        enqueued.assert_not_called()

    def test_customer_reply_names_the_customer_as_actor(
        self, shared_case, org_a, enqueued
    ):
        """So the task can exclude them and nobody is mailed about their own reply."""
        case, pat, _, _ = shared_case
        Comment.objects.create(
            org=org_a,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment="Any news?",
            commented_by_contact=pat,
            is_internal=False,
        )
        enqueued.assert_called_once_with(
            str(case.id), str(org_a.id), "reply", actor_contact_id=str(pat.id)
        )

    def test_status_change_enqueues(self, shared_case, org_a, enqueued):
        case, _, _, _ = shared_case
        case.status = "Closed"
        case.save()
        enqueued.assert_called_once_with(str(case.id), str(org_a.id), "status")

    def test_a_save_that_changes_no_status_enqueues_nothing(
        self, shared_case, org_a, enqueued
    ):
        case, _, _, _ = shared_case
        case.priority = "High"
        case.save()
        enqueued.assert_not_called()
