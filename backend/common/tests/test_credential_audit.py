"""Credential lifecycle events in the security audit log.

A calendar feed URL and a personal access token are both standing credentials,
so turning one on, replacing it and taking it away each write one row an org
admin can read at ``/api/org/audit-log/``:

* ``CALENDAR_FEED_ENABLED``, ``CALENDAR_FEED_REGENERATED``,
  ``CALENDAR_FEED_DISABLED`` from ``/api/profile/calendar-feed/``;
* ``API_TOKEN_CREATED`` from ``/api/profile/tokens/`` and ``API_TOKEN_REVOKED``
  from ``/api/profile/tokens/<id>/`` (the owner) or ``/api/org/tokens/<id>/``
  (an admin), naming who acted and whose token it was.

No row ever holds a raw token or its hash, a no-op (disabling a feed that is
off, revoking a revoked token) writes nothing, and a failed audit write never
fails the action it records.
"""

from unittest import mock

import pytest
from django.core.cache import cache

from common.audit_log import SecurityAuditLog
from common.models import CalendarFeedToken, PersonalAccessToken

FEED = "/api/profile/calendar-feed/"
TOKENS = "/api/profile/tokens/"
VIEWER = "/api/org/audit-log/"


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _rows(event_type):
    return list(SecurityAuditLog.objects.filter(event_type=event_type))


def _only(event_type):
    rows = _rows(event_type)
    assert len(rows) == 1, rows
    return rows[0]


def _assert_no_secret(row, *secrets):
    text = str(SecurityAuditLog.objects.filter(pk=row.pk).values().get())
    for secret in secrets:
        assert secret not in text


def _feed_secrets(url):
    raw = url.rsplit("/", 1)[1].removesuffix(".ics")
    return raw, CalendarFeedToken.hash_token(raw)


class TestCalendarFeed:
    def test_enable_writes_one_enabled_row(
        self, user_client, user_profile, regular_user, org_a
    ):
        resp = user_client.post(FEED)
        assert resp.status_code == 201
        row = _only("CALENDAR_FEED_ENABLED")
        assert row.org_id == org_a.id
        assert row.user_id == regular_user.id
        assert row.success is True
        assert row.ip_address == "127.0.0.1"
        assert row.request_path == FEED
        assert row.metadata == {}
        _assert_no_secret(row, *_feed_secrets(resp.json()["url"]))
        assert SecurityAuditLog.objects.count() == 1

    def test_regenerate_writes_one_regenerated_row(
        self, user_client, user_profile, regular_user, org_a
    ):
        first = user_client.post(FEED).json()["url"]
        second = user_client.post(FEED).json()["url"]
        row = _only("CALENDAR_FEED_REGENERATED")
        assert (row.org_id, row.user_id) == (org_a.id, regular_user.id)
        assert len(_rows("CALENDAR_FEED_ENABLED")) == 1
        _assert_no_secret(row, *_feed_secrets(first))
        _assert_no_secret(row, *_feed_secrets(second))

    def test_disable_writes_one_disabled_row(self, user_client, regular_user, org_a):
        user_client.post(FEED)
        assert user_client.delete(FEED).status_code == 200
        row = _only("CALENDAR_FEED_DISABLED")
        assert (row.org_id, row.user_id) == (org_a.id, regular_user.id)

    def test_disabling_a_feed_that_is_off_writes_nothing(self, user_client):
        assert user_client.delete(FEED).status_code == 200
        assert SecurityAuditLog.objects.count() == 0

    def test_a_failed_audit_write_does_not_fail_the_action(
        self, user_client, user_profile
    ):
        with mock.patch.object(
            SecurityAuditLog.objects, "create", side_effect=RuntimeError("db down")
        ):
            assert user_client.post(FEED).status_code == 201
            assert user_client.post(FEED).status_code == 201
            assert CalendarFeedToken.objects.filter(profile=user_profile).exists()
            assert user_client.delete(FEED).status_code == 200
        assert not CalendarFeedToken.objects.filter(profile=user_profile).exists()
        assert SecurityAuditLog.objects.count() == 0


class TestOwnApiTokens:
    def test_create_writes_one_row_naming_the_token(
        self, user_client, regular_user, org_a
    ):
        resp = user_client.post(
            TOKENS, {"name": "CI script", "scopes": ["*:read"]}, format="json"
        )
        assert resp.status_code == 201, resp.content
        raw = resp.json()["token"]
        pat = PersonalAccessToken.objects.get()
        row = _only("API_TOKEN_CREATED")
        assert (row.org_id, row.user_id) == (org_a.id, regular_user.id)
        assert row.ip_address == "127.0.0.1"
        assert row.metadata == {
            "token_id": str(pat.id),
            "token_prefix": pat.token_prefix,
            "token_name": "CI script",
            "scopes": ["*:read"],
            "owner_id": str(regular_user.id),
            "owner_name": regular_user.name or regular_user.email,
        }
        _assert_no_secret(row, raw, pat.token_hash)

    def test_a_refused_create_writes_nothing(self, user_client):
        assert user_client.post(TOKENS, {"name": " "}, format="json").status_code == 400
        assert SecurityAuditLog.objects.count() == 0

    def test_revoke_writes_one_row_and_a_second_revoke_writes_none(
        self, user_client, user_profile, regular_user, org_a
    ):
        raw, pat = PersonalAccessToken.generate(profile=user_profile, name="cli")
        assert user_client.delete(f"{TOKENS}{pat.id}/").status_code == 200
        assert user_client.delete(f"{TOKENS}{pat.id}/").status_code == 200
        row = _only("API_TOKEN_REVOKED")
        assert (row.org_id, row.user_id) == (org_a.id, regular_user.id)
        assert row.metadata["owner_id"] == str(regular_user.id)
        assert row.metadata["token_id"] == str(pat.id)
        _assert_no_secret(row, raw, pat.token_hash)
        pat.refresh_from_db()
        assert pat.revoked_at is not None

    def test_another_members_token_is_404_and_writes_nothing(
        self, user_client, admin_profile
    ):
        _, pat = PersonalAccessToken.generate(profile=admin_profile, name="theirs")
        assert user_client.delete(f"{TOKENS}{pat.id}/").status_code == 404
        assert SecurityAuditLog.objects.count() == 0
        pat.refresh_from_db()
        assert pat.revoked_at is None

    def test_a_failed_audit_write_does_not_fail_create_or_revoke(self, user_client):
        with mock.patch.object(
            SecurityAuditLog.objects, "create", side_effect=RuntimeError("db down")
        ):
            resp = user_client.post(TOKENS, {"name": "cli"}, format="json")
            assert resp.status_code == 201
            pat = PersonalAccessToken.objects.get()
            assert user_client.delete(f"{TOKENS}{pat.id}/").status_code == 200
        pat.refresh_from_db()
        assert pat.revoked_at is not None
        assert SecurityAuditLog.objects.count() == 0


class TestAdminRevoke:
    def test_records_the_admin_as_actor_and_the_member_as_owner(
        self, admin_client, admin_user, user_profile, regular_user, org_a
    ):
        raw, pat = PersonalAccessToken.generate(profile=user_profile, name="laptop")
        assert admin_client.delete(f"/api/org/tokens/{pat.id}/").status_code == 200
        row = _only("API_TOKEN_REVOKED")
        assert (row.org_id, row.user_id) == (org_a.id, admin_user.id)
        assert row.metadata["owner_id"] == str(regular_user.id)
        assert row.metadata["token_name"] == "laptop"
        _assert_no_secret(row, raw, pat.token_hash)

        assert admin_client.delete(f"/api/org/tokens/{pat.id}/").status_code == 200
        assert len(_rows("API_TOKEN_REVOKED")) == 1

    def test_a_member_is_refused_and_nothing_is_written(
        self, user_client, admin_profile
    ):
        _, pat = PersonalAccessToken.generate(profile=admin_profile, name="x")
        assert user_client.delete(f"/api/org/tokens/{pat.id}/").status_code == 403
        assert SecurityAuditLog.objects.count() == 0

    def test_another_orgs_token_is_404_and_writes_nothing(
        self, admin_client, profile_b
    ):
        _, pat = PersonalAccessToken.generate(profile=profile_b, name="x")
        assert admin_client.delete(f"/api/org/tokens/{pat.id}/").status_code == 404
        assert SecurityAuditLog.objects.count() == 0


class TestTheViewer:
    @pytest.fixture
    def logged(self, user_client, user_profile):
        """One of each new event, all written by a member of org A."""
        user_client.post(FEED)
        user_client.post(FEED)
        user_client.delete(FEED)
        user_client.post(TOKENS, {"name": "CI"}, format="json")
        pat = PersonalAccessToken.objects.get()
        user_client.delete(f"{TOKENS}{pat.id}/")
        return pat

    NEW = (
        "CALENDAR_FEED_ENABLED",
        "CALENDAR_FEED_REGENERATED",
        "CALENDAR_FEED_DISABLED",
        "API_TOKEN_CREATED",
        "API_TOKEN_REVOKED",
    )

    def test_an_admin_reads_them_and_the_filter_returns_each(
        self, admin_client, logged
    ):
        body = admin_client.get(VIEWER).json()
        assert {e["event_type"] for e in body["results"]} == set(self.NEW)
        offered = {t["value"] for t in body["event_types"]}
        assert offered >= set(self.NEW)
        for event_type in self.NEW:
            results = admin_client.get(f"{VIEWER}?event_type={event_type}").json()[
                "results"
            ]
            assert [e["event_type"] for e in results] == [event_type]

    def test_a_token_row_shows_its_details_and_a_feed_row_shows_none(
        self, admin_client, regular_user, logged
    ):
        created = admin_client.get(f"{VIEWER}?event_type=API_TOKEN_CREATED").json()[
            "results"
        ][0]
        assert created["event_label"] == "API Token Created"
        assert created["details"] == {
            "token_id": str(logged.id),
            "token_prefix": logged.token_prefix,
            "token_name": "CI",
            "scopes": [],
            "owner_id": str(regular_user.id),
            "owner_name": regular_user.name or regular_user.email,
        }
        assert logged.token_hash not in str(created)
        feed = admin_client.get(
            f"{VIEWER}?event_type=CALENDAR_FEED_REGENERATED"
        ).json()["results"][0]
        assert feed["event_label"] == "Calendar Feed Regenerated"
        assert feed["details"] == {}

    def test_token_keys_are_not_let_through_on_any_other_event(
        self, admin_client, org_a
    ):
        SecurityAuditLog.objects.create(
            org=org_a,
            event_type="SUSPICIOUS_ACTIVITY",
            metadata={"token_name": "typed by a caller", "owner_name": "x"},
        )
        assert admin_client.get(VIEWER).json()["results"][0]["details"] == {}

    def test_a_member_cannot_read_them(self, user_client, logged):
        assert user_client.get(VIEWER).status_code == 403

    def test_another_orgs_admin_never_sees_them(self, org_b_client, logged):
        body = org_b_client.get(VIEWER).json()
        assert body["count"] == 0
        assert (
            org_b_client.get(f"{VIEWER}?event_type=API_TOKEN_CREATED").json()["count"]
            == 0
        )
