"""A mailbox's ``default_assignee`` only counts while that member is active.

Every ticket raised by inbound mail was handed to the stored default even after
that member had been deactivated, so new customer mail landed on somebody who
can no longer sign in to answer it. An inactive (or foreign) default is now
treated as no default: the ticket is left to routing, as it always was for a
mailbox with none. The admin API also refuses to save an inactive one.
"""

import pytest

from cases.inbound.parser import parse_raw_email
from cases.inbound.pipeline import ingest
from cases.tests.test_inbound_email import MAILBOXES_URL, _make_mailbox, _raw_email
from common.models import Profile, User


@pytest.fixture
def inactive_profile(org_a):
    user = User.objects.create_user(email="gone@test.com", password="testpass123")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=False)


def _ingest(mailbox):
    return ingest(parse_raw_email(_raw_email()), mailbox)


@pytest.mark.django_db
class TestInboundDefaultAssignee:
    def test_an_inactive_default_is_not_assigned(self, org_a, inactive_profile):
        mailbox = _make_mailbox(org_a, default_assignee=inactive_profile)

        case = _ingest(mailbox).case

        assert case.assigned_to.count() == 0

    def test_a_default_from_another_org_is_not_assigned(self, org_a, profile_b):
        mailbox = _make_mailbox(org_a, default_assignee=profile_b)

        case = _ingest(mailbox).case

        assert case.assigned_to.count() == 0

    def test_an_active_default_is_still_assigned(self, org_a, user_profile):
        mailbox = _make_mailbox(org_a, default_assignee=user_profile)

        case = _ingest(mailbox).case

        assert list(case.assigned_to.all()) == [user_profile]


@pytest.mark.django_db
class TestMailboxApiDefaultAssignee:
    def test_create_refuses_an_inactive_default(self, admin_client, inactive_profile):
        response = admin_client.post(
            MAILBOXES_URL,
            {
                "address": "help@acme.com",
                "provider": "ses",
                "default_assignee_id": str(inactive_profile.id),
            },
            format="json",
        )

        assert response.status_code == 400
        assert "default_assignee_id" in response.data["errors"]

    def test_update_refuses_an_inactive_default(
        self, admin_client, org_a, user_profile, inactive_profile
    ):
        mailbox = _make_mailbox(org_a, default_assignee=user_profile)

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"default_assignee_id": str(inactive_profile.id)},
            format="json",
        )

        assert response.status_code == 400
        assert "default_assignee_id" in response.data["errors"]
        mailbox.refresh_from_db()
        assert mailbox.default_assignee == user_profile

    def test_an_active_default_is_accepted(self, admin_client, user_profile):
        response = admin_client.post(
            MAILBOXES_URL,
            {
                "address": "help@acme.com",
                "provider": "ses",
                "default_assignee_id": str(user_profile.id),
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        assert response.data["default_assignee"]["id"] == str(user_profile.id)

    def test_clearing_the_default_is_accepted(self, admin_client, org_a, user_profile):
        mailbox = _make_mailbox(org_a, default_assignee=user_profile)

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"default_assignee_id": None},
            format="json",
        )

        assert response.status_code == 200, response.data
        mailbox.refresh_from_db()
        assert mailbox.default_assignee is None


@pytest.mark.django_db
class TestMailboxApiKeepsAStoredInactiveDefault:
    """Clients resend the stored `default_assignee_id` on every save, so a
    mailbox whose default was deactivated after being chosen must still save.
    Keeping that value is harmless (inbound mail ignores it); choosing a new
    inactive or foreign one is still refused."""

    @pytest.fixture
    def mailbox(self, org_a, inactive_profile):
        return _make_mailbox(org_a, default_assignee=inactive_profile)

    def _put(self, client, mailbox, assignee_id):
        return client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"default_priority": "High", "default_assignee_id": assignee_id},
            format="json",
        )

    def test_resending_the_stored_inactive_default_saves(
        self, admin_client, mailbox, inactive_profile
    ):
        response = self._put(admin_client, mailbox, str(inactive_profile.id))

        assert response.status_code == 200, response.data
        mailbox.refresh_from_db()
        assert mailbox.default_priority == "High"
        assert mailbox.default_assignee == inactive_profile

    def test_changing_to_a_different_inactive_member_is_refused(
        self, admin_client, mailbox, org_a, inactive_profile
    ):
        user = User.objects.create_user(email="gone2@test.com", password="pw123456")
        other = Profile.objects.create(
            user=user, org=org_a, role="USER", is_active=False
        )

        response = self._put(admin_client, mailbox, str(other.id))

        assert response.status_code == 400
        assert "default_assignee_id" in response.data["errors"]
        mailbox.refresh_from_db()
        assert mailbox.default_assignee == inactive_profile

    def test_changing_to_another_orgs_profile_is_refused(
        self, admin_client, mailbox, profile_b, inactive_profile
    ):
        response = self._put(admin_client, mailbox, str(profile_b.id))

        assert response.status_code == 400
        assert "default_assignee_id" in response.data["errors"]
        mailbox.refresh_from_db()
        assert mailbox.default_assignee == inactive_profile

    def test_changing_to_an_active_member_saves(
        self, admin_client, mailbox, user_profile
    ):
        response = self._put(admin_client, mailbox, str(user_profile.id))

        assert response.status_code == 200, response.data
        mailbox.refresh_from_db()
        assert mailbox.default_assignee == user_profile
