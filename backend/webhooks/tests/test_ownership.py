"""A webhook is paused when its creator stops being an admin of its org.

Every path that can end that standing is driven here: the role and status
endpoints, removal from the org, the user row (the Django admin's path), a
queryset update that no receiver sees, and deleting the user outright. Each
pause is audited, a paused endpoint sends nothing, and an admin can turn it
back on, which makes them its creator.
"""

from unittest import mock

import pytest
from django.db import connection

from common.audit_log import SecurityAuditLog
from common.models import Org, Profile, User
from common.testing import _make_authenticated_client
from conftest import clear_rls_context, rls_org
from webhooks import ownership
from webhooks.emit import envelope, queue_delivery
from webhooks.models import FAILED, WebhookEndpoint
from webhooks.tasks import attempt_delivery

BASE = "/api/webhooks/"


@pytest.fixture(autouse=True)
def dns_and_broker(public_dns):
    with mock.patch("webhooks.emit.deliver_webhook.delay"):
        yield


@pytest.fixture
def creator(org_a):
    """A second admin of org A, who adds the webhook. `admin_client` is the
    colleague who then demotes, deactivates or removes them."""
    user = User.objects.create_user(email="creator@test.com", password="x")
    return Profile.objects.create(user=user, org=org_a, role="ADMIN", is_active=True)


@pytest.fixture
def hook(org_a, creator):
    return WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created"],
        created_by=creator.user,
    )


def _paused_rows(endpoint):
    return SecurityAuditLog.objects.filter(
        event_type="WEBHOOK_PAUSED", metadata__endpoint_id=str(endpoint.id)
    )


def _assert_paused(endpoint, reason):
    endpoint.refresh_from_db()
    assert endpoint.is_active is False
    assert endpoint.disabled_reason == reason
    row = _paused_rows(endpoint).get()
    assert row.org_id == endpoint.org_id
    assert row.metadata["pause_reason"] == reason
    return row


class TestPauseTriggers:
    def test_demoted_through_the_user_endpoint(self, admin_client, creator, hook):
        response = admin_client.patch(
            f"/api/user/{creator.user.id}/", {"role": "USER"}, format="json"
        )
        assert response.status_code == 200
        row = _assert_paused(hook, ownership.NOT_ADMIN)
        assert row.user_id == creator.user_id
        assert row.metadata["creator_id"] == str(creator.user_id)

    def test_deactivated_through_the_status_endpoint(self, admin_client, creator, hook):
        response = admin_client.post(
            f"/api/user/{creator.user.id}/status/", {"status": "Inactive"}
        )
        assert response.status_code == 200
        _assert_paused(hook, ownership.DEACTIVATED)

    @mock.patch("common.views.user_views.send_email_user_delete")
    def test_removed_from_the_org(self, _email, admin_client, creator, hook):
        response = admin_client.delete(f"/api/user/{creator.user.id}/")
        assert response.status_code == 200
        assert not Profile.objects.filter(pk=creator.pk).exists()
        _assert_paused(hook, ownership.LEFT)

    def test_user_deactivated_pauses_in_every_org(self, creator, hook, org_b):
        """The Django admin's path: the user row, no request, no org context."""
        Profile.objects.create(user=creator.user, org=org_b, role="ADMIN")
        other = WebhookEndpoint.objects.create(
            org=org_b,
            url="https://hooks.example.com/b",
            events=["lead.created"],
            created_by=creator.user,
        )
        creator.user.is_active = False
        creator.user.save()
        _assert_paused(hook, ownership.DEACTIVATED)
        _assert_paused(other, ownership.DEACTIVATED)

    def test_superuser_flag_removed_from_a_user_role_creator(self, org_a):
        user = User.objects.create_user(
            email="root@test.com", password="x", is_superuser=True
        )
        Profile.objects.create(user=user, org=org_a, role="USER")
        endpoint = WebhookEndpoint.objects.create(
            org=org_a,
            url="https://hooks.example.com/in",
            events=["lead.created"],
            created_by=user,
        )
        user.is_superuser = False
        user.save(update_fields=["is_superuser"])
        _assert_paused(endpoint, ownership.NOT_ADMIN)

    def test_user_deleted(self, creator, hook):
        user_id = creator.user_id
        creator.user.delete()
        hook.refresh_from_db()
        assert hook.created_by_id is None
        row = _assert_paused(hook, ownership.DELETED_USER)
        # The user row is gone, so the audit row names them by id.
        assert row.user_id is None
        assert row.metadata["creator_id"] == str(user_id)

    def test_deleting_the_org_takes_its_endpoints_without_error(self, creator, hook):
        org_id = hook.org_id
        Org.objects.filter(pk=org_id).delete()
        assert not WebhookEndpoint.objects.filter(pk=hook.pk).exists()
        assert not _paused_rows(hook).exists()

    def test_a_second_pause_writes_no_second_audit_row(self, creator, hook):
        ownership.pause([hook], ownership.NOT_ADMIN)
        ownership.pause([hook], ownership.NOT_ADMIN)
        assert _paused_rows(hook).count() == 1


class TestNotATrigger:
    def test_another_admin_demoted_leaves_the_endpoint_alone(
        self, admin_client, org_a, creator, hook
    ):
        bystander = Profile.objects.create(
            user=User.objects.create_user(email="other@test.com", password="x"),
            org=org_a,
            role="ADMIN",
        )
        response = admin_client.patch(
            f"/api/user/{bystander.user.id}/", {"role": "USER"}, format="json"
        )
        assert response.status_code == 200
        hook.refresh_from_db()
        assert hook.is_active is True
        assert not _paused_rows(hook).exists()

    def test_the_creator_edited_but_still_an_admin(self, admin_client, creator, hook):
        response = admin_client.patch(
            f"/api/user/{creator.user.id}/", {"phone": "+14155550100"}, format="json"
        )
        assert response.status_code == 200
        hook.refresh_from_db()
        assert hook.is_active is True

    def test_problem_answers_both_ways(self, creator, hook, user_profile):
        assert ownership.creator_problem(hook) is None
        hook.created_by = user_profile.user
        assert ownership.creator_problem(hook) == ownership.NOT_ADMIN
        hook.created_by = None
        assert ownership.creator_problem(hook) == ownership.DELETED_USER
        hook.created_by = User.objects.create_user(email="out@test.com", password="x")
        assert ownership.creator_problem(hook) == ownership.NOT_MEMBER


class TestPausedSendsNothing:
    def test_a_queued_retry_for_a_paused_endpoint_is_not_sent(
        self, admin_client, creator, hook
    ):
        delivery = queue_delivery(
            hook, "lead.created", envelope(hook.org_id, "lead.created", {})
        )
        admin_client.post(
            f"/api/user/{creator.user.id}/status/", {"status": "Inactive"}
        )
        with mock.patch("webhooks.tasks.ssrf.post") as post:
            result = attempt_delivery(delivery.pk, hook.org_id)
        post.assert_not_called()
        assert result.status == FAILED

    def test_a_change_no_receiver_saw_is_caught_before_sending(self, creator, hook):
        delivery = queue_delivery(
            hook, "lead.created", envelope(hook.org_id, "lead.created", {})
        )
        # A queryset update sends no signal.
        Profile.objects.filter(pk=creator.pk).update(role="USER")
        hook.refresh_from_db()
        assert hook.is_active is True
        with mock.patch("webhooks.tasks.ssrf.post") as post:
            result = attempt_delivery(delivery.pk, hook.org_id)
        post.assert_not_called()
        assert result.status == FAILED
        _assert_paused(hook, ownership.NOT_ADMIN)

    def test_a_qualified_creator_still_sends(self, hook):
        delivery = queue_delivery(
            hook, "lead.created", envelope(hook.org_id, "lead.created", {})
        )
        with mock.patch("webhooks.tasks.ssrf.post", return_value=200) as post:
            attempt_delivery(delivery.pk, hook.org_id)
        post.assert_called_once()


class TestReEnable:
    def _pause(self, admin_client, creator):
        admin_client.patch(
            f"/api/user/{creator.user.id}/", {"role": "USER"}, format="json"
        )

    def test_an_admin_turns_it_back_on_and_becomes_its_creator(
        self, admin_client, admin_user, creator, hook
    ):
        self._pause(admin_client, creator)
        response = admin_client.patch(
            f"{BASE}{hook.id}/", {"is_active": True}, format="json"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["is_active"] is True
        assert body["disabled_reason"] == ""
        assert body["created_by"]["email"] == admin_user.email
        hook.refresh_from_db()
        assert hook.created_by_id == admin_user.id
        row = SecurityAuditLog.objects.get(event_type="WEBHOOK_REENABLED")
        assert row.user_id == admin_user.id
        assert row.org_id == hook.org_id
        assert row.metadata == {
            "endpoint_id": str(hook.id),
            "previous_creator_id": str(creator.user_id),
            "changed": ["is_active"],
        }

    def test_a_member_cannot_turn_it_back_on(
        self, admin_client, user_client, creator, hook
    ):
        self._pause(admin_client, creator)
        response = user_client.patch(
            f"{BASE}{hook.id}/", {"is_active": True}, format="json"
        )
        assert response.status_code == 403
        hook.refresh_from_db()
        assert hook.is_active is False
        assert hook.created_by_id == creator.user_id
        assert not SecurityAuditLog.objects.filter(
            event_type="WEBHOOK_REENABLED"
        ).exists()

    def test_a_description_edit_does_not_move_the_creator(
        self, admin_client, creator, hook
    ):
        response = admin_client.patch(
            f"{BASE}{hook.id}/", {"description": "renamed"}, format="json"
        )
        assert response.status_code == 200
        hook.refresh_from_db()
        assert hook.created_by_id == creator.user_id
        assert not SecurityAuditLog.objects.filter(
            event_type__in=["WEBHOOK_CHANGED", "WEBHOOK_REENABLED"]
        ).exists()

    def test_resending_the_same_values_moves_nothing(self, admin_client, creator, hook):
        admin_client.patch(
            f"{BASE}{hook.id}/",
            {"url": hook.url, "events": hook.events, "format": hook.format},
            format="json",
        )
        hook.refresh_from_db()
        assert hook.created_by_id == creator.user_id

    @pytest.mark.parametrize(
        "change",
        [
            {"url": "https://attacker.example.com/in"},
            {"events": ["lead.created", "deal.won"]},
            {"format": "slack"},
        ],
    )
    def test_changing_what_or_where_moves_the_creator(
        self, admin_client, admin_user, creator, hook, change
    ):
        response = admin_client.patch(f"{BASE}{hook.id}/", change, format="json")
        assert response.status_code == 200
        hook.refresh_from_db()
        assert hook.created_by_id == admin_user.id
        row = SecurityAuditLog.objects.get(event_type="WEBHOOK_CHANGED")
        assert row.user_id == admin_user.id
        assert row.metadata["changed"] == list(change)
        assert row.metadata["previous_creator_id"] == str(creator.user_id)

    def test_rotating_the_secret_moves_the_creator(
        self, admin_client, admin_user, creator, hook
    ):
        response = admin_client.post(f"{BASE}{hook.id}/rotate-secret/")
        assert response.status_code == 200
        hook.refresh_from_db()
        assert hook.created_by_id == admin_user.id
        row = SecurityAuditLog.objects.get(event_type="WEBHOOK_CHANGED")
        assert row.metadata["changed"] == ["secret"]

    def test_re_pointing_then_being_demoted_pauses_the_endpoint(
        self, org_a, admin_client, admin_user, creator, hook
    ):
        """The attack the move closes. Admin B (`admin_client`) points A's
        endpoint at B's own server, then A demotes B. The endpoint is B's
        now, so it pauses instead of sending the org's records to B."""
        admin_client.patch(
            f"{BASE}{hook.id}/", {"url": "https://b.example.com/in"}, format="json"
        )
        as_a = _make_authenticated_client(creator.user, org_a, creator)
        response = as_a.patch(
            f"/api/user/{admin_user.id}/", {"role": "USER"}, format="json"
        )
        assert response.status_code == 200
        _assert_paused(hook, ownership.NOT_ADMIN)

    def test_created_by_cannot_be_set_from_the_body(
        self, admin_client, creator, hook, user_profile
    ):
        admin_client.patch(
            f"{BASE}{hook.id}/",
            {"created_by": str(user_profile.user_id), "description": "x"},
            format="json",
        )
        hook.refresh_from_db()
        assert hook.created_by_id == creator.user_id

    def test_the_list_shows_the_creator(self, admin_client, creator, hook):
        endpoint = admin_client.get(BASE).json()["endpoints"][0]
        assert endpoint["created_by"] == {
            "id": str(creator.user_id),
            "name": creator.user.name,
            "email": creator.user.email,
        }


@pytest.mark.postgres_only
def test_deactivating_a_user_with_no_org_context_reaches_every_org(creator, org_b):
    """The Django admin runs with no `app.current_org`, under which the policy
    hides every endpoint. Each org's endpoints are looked up as that org, and
    the empty context is put back afterwards. Only meaningful under the CI
    role that RLS binds."""
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    Profile.objects.create(user=creator.user, org=org_b, role="ADMIN")
    ours = WebhookEndpoint.objects.create(
        org=creator.org,
        url="https://h.example.com/a",
        events=["lead.created"],
        created_by=creator.user,
    )
    with rls_org(org_b):
        theirs = WebhookEndpoint.objects.create(
            org=org_b,
            url="https://h.example.com/b",
            events=["lead.created"],
            created_by=creator.user,
        )
    clear_rls_context()

    creator.user.is_active = False
    creator.user.save()

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_org', true)")
        assert (cursor.fetchone()[0] or "") == ""
    for org, endpoint in ((creator.org, ours), (org_b, theirs)):
        with rls_org(org):
            endpoint.refresh_from_db()
        assert endpoint.is_active is False
        assert endpoint.disabled_reason == ownership.DEACTIVATED
