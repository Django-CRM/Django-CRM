"""Who gets told about a case update, and who does not.

The portal is only useful if something brings the customer back to it, and it
is only safe if that something does not carry a credential in the email.
"""

from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import transaction

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


def _public_reply(case, org, **author):
    return Comment.objects.create(
        org=org,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment="We are on it.",
        is_internal=False,
        **author,
    )


@pytest.mark.django_db
def test_notifies_every_contact_with_an_email(shared_case, org_a, admin_profile):
    case, _, _, _ = shared_case
    comment = _public_reply(case, org_a, commented_by=admin_profile)
    mail.outbox.clear()
    notify_portal_contacts(
        str(case.id), str(org_a.id), "reply", comment_id=str(comment.id)
    )
    assert _recipients() == {"pat@example.com", "jo@example.com"}


@pytest.mark.django_db
def test_does_not_notify_the_contact_who_caused_it(shared_case, org_a):
    """Nobody is emailed about their own reply."""
    case, pat, _, _ = shared_case
    comment = _public_reply(case, org_a, commented_by_contact=pat)
    mail.outbox.clear()
    notify_portal_contacts(
        str(case.id),
        str(org_a.id),
        "reply",
        actor_contact_id=str(pat.id),
        comment_id=str(comment.id),
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
def test_the_link_carries_the_org_so_signing_in_is_possible(
    shared_case, org_a, admin_profile
):
    """Without this the recipient lands on the staff login, which they cannot use.

    Most people open this email on a phone, which is usually not the device
    they last signed in on, so there is no portal cookie to fall back to and
    the org id in the URL is the only thing that names their sign-in page.
    """
    case, _, _, _ = shared_case
    comment = _public_reply(case, org_a, commented_by=admin_profile)
    mail.outbox.clear()
    notify_portal_contacts(
        str(case.id), str(org_a.id), "reply", comment_id=str(comment.id)
    )
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

    The enqueue waits for the commit (the task re-reads the rows the request
    wrote), so each write here runs its commit callbacks.
    """

    @pytest.fixture(autouse=True)
    def enqueued(self):
        with patch("cases.tasks.notify_portal_contacts.delay") as delay:
            yield delay

    def test_agent_public_reply_enqueues(
        self,
        shared_case,
        org_a,
        admin_profile,
        enqueued,
        django_capture_on_commit_callbacks,
    ):
        case, _, _, _ = shared_case
        with django_capture_on_commit_callbacks(execute=True):
            comment = _public_reply(case, org_a, commented_by=admin_profile)
        enqueued.assert_called_once_with(
            str(case.id),
            str(org_a.id),
            "reply",
            actor_contact_id=None,
            comment_id=str(comment.id),
        )

    def test_internal_note_enqueues_nothing(
        self,
        shared_case,
        org_a,
        admin_profile,
        enqueued,
        django_capture_on_commit_callbacks,
    ):
        """The whole point of is_internal. A leak here would be the worst case."""
        case, _, _, _ = shared_case
        with django_capture_on_commit_callbacks(execute=True):
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
        self, shared_case, org_a, enqueued, django_capture_on_commit_callbacks
    ):
        """So the task can exclude them and nobody is mailed about their own reply."""
        case, pat, _, _ = shared_case
        with django_capture_on_commit_callbacks(execute=True):
            comment = _public_reply(case, org_a, commented_by_contact=pat)
        enqueued.assert_called_once_with(
            str(case.id),
            str(org_a.id),
            "reply",
            actor_contact_id=str(pat.id),
            comment_id=str(comment.id),
        )

    def test_status_change_enqueues(
        self, shared_case, org_a, enqueued, django_capture_on_commit_callbacks
    ):
        case, _, _, _ = shared_case
        case.status = "Closed"
        with django_capture_on_commit_callbacks(execute=True):
            case.save()
        enqueued.assert_called_once_with(str(case.id), str(org_a.id), "status")

    def test_a_save_that_changes_no_status_enqueues_nothing(
        self, shared_case, org_a, enqueued, django_capture_on_commit_callbacks
    ):
        case, _, _, _ = shared_case
        case.priority = "High"
        with django_capture_on_commit_callbacks(execute=True):
            case.save()
        enqueued.assert_not_called()


@pytest.mark.django_db
class TestEnqueuedAfterCommit:
    """The task re-reads what the request wrote. Queued before the commit, a
    worker could run first, find no public comment, and drop the email."""

    def test_reply_inside_a_transaction_waits_for_the_commit(
        self, shared_case, org_a, admin_profile, django_capture_on_commit_callbacks
    ):
        case, _, _, _ = shared_case
        with patch("cases.tasks.notify_portal_contacts.delay") as delay:
            with django_capture_on_commit_callbacks() as callbacks:
                with transaction.atomic():
                    _public_reply(case, org_a, commented_by=admin_profile)
                    delay.assert_not_called()
            for callback in callbacks:
                callback()
        delay.assert_called_once()

    def test_the_reply_email_goes_out_once_committed(
        self, shared_case, org_a, admin_profile, django_capture_on_commit_callbacks
    ):
        """End to end, with the task run where the broker would run it."""
        case, _, _, _ = shared_case
        mail.outbox.clear()
        with patch(
            "cases.tasks.notify_portal_contacts.delay",
            side_effect=lambda *a, **k: notify_portal_contacts(*a, **k),
        ):
            with django_capture_on_commit_callbacks(execute=True):
                with transaction.atomic():
                    _public_reply(case, org_a, commented_by=admin_profile)
        assert _recipients() == {"pat@example.com", "jo@example.com"}
        assert "We are on it." in mail.outbox[0].body

    def test_status_change_inside_a_transaction_waits_for_the_commit(
        self, shared_case, django_capture_on_commit_callbacks
    ):
        case, _, _, _ = shared_case
        with patch("cases.tasks.notify_portal_contacts.delay") as delay:
            with django_capture_on_commit_callbacks() as callbacks:
                with transaction.atomic():
                    case.status = "Pending"
                    case.save()
                    delay.assert_not_called()
            for callback in callbacks:
                callback()
        delay.assert_called_once_with(str(case.id), str(case.org_id), "status")

    def test_a_rolled_back_reply_sends_nothing(
        self, shared_case, org_a, admin_profile, django_capture_on_commit_callbacks
    ):
        case, _, _, _ = shared_case
        with patch("cases.tasks.notify_portal_contacts.delay") as delay:
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                with pytest.raises(RuntimeError):
                    with transaction.atomic():
                        _public_reply(case, org_a, commented_by=admin_profile)
                        raise RuntimeError("the request failed after the write")
        assert callbacks == []
        delay.assert_not_called()

    def test_a_broker_outage_does_not_break_the_request(
        self,
        shared_case,
        org_a,
        admin_profile,
        django_capture_on_commit_callbacks,
        caplog,
    ):
        case, _, _, _ = shared_case
        with patch(
            "cases.tasks.notify_portal_contacts.delay",
            side_effect=ConnectionError("broker down"),
        ):
            with django_capture_on_commit_callbacks(execute=True):
                comment = _public_reply(case, org_a, commented_by=admin_profile)
        assert Comment.objects.filter(pk=comment.pk).exists()
        assert "portal reply notification failed" in caplog.text
