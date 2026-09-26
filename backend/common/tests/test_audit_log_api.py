"""GET /api/org/audit-log/: an admin reads their own org's security audit log.

`security_audit_log` has no RLS policy, so the view's explicit org filter is
the only thing keeping one org's rows from another. These tests plant rows in
two orgs and with no org at all, and check only the caller's come back.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from common.audit_log import SecurityAuditLog
from common.models import PersonalAccessToken, Profile, User
from common.testing import _make_authenticated_client

URL = "/api/org/audit-log/"


def _row(org, event_type="LOGIN_SUCCESS", user=None, days_ago=0, **extra):
    row = SecurityAuditLog.objects.create(
        org=org, event_type=event_type, user=user, **extra
    )
    if days_ago:
        SecurityAuditLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
    return row


def _ids(response):
    return [entry["id"] for entry in response.json()["results"]]


class TestWhoMayRead:
    def test_an_admin_reads_their_own_org_only(
        self, admin_client, admin_user, org_a, org_b
    ):
        ours = _row(org_a, user=admin_user)
        _row(org_b, user=admin_user)
        _row(None, event_type="LOGIN_FAILURE", success=False)
        response = admin_client.get(URL)
        assert response.status_code == 200
        assert _ids(response) == [str(ours.id)]
        assert response.json()["count"] == 1

    def test_another_orgs_admin_never_sees_them(self, org_b_client, org_a, org_b):
        _row(org_a)
        theirs = _row(org_b)
        assert _ids(org_b_client.get(URL)) == [str(theirs.id)]

    def test_a_member_is_refused(self, user_client, org_a):
        _row(org_a)
        assert user_client.get(URL).status_code == 403

    def test_a_superuser_holding_the_user_role_is_an_admin(self, org_a):
        user = User.objects.create_user(
            email="root@test.com", password="x", is_superuser=True
        )
        profile = Profile.objects.create(user=user, org=org_a, role="USER")
        client = _make_authenticated_client(user, org_a, profile)
        assert client.get(URL).status_code == 200

    def test_an_admins_api_token_is_refused(self, admin_profile, org_a):
        _row(org_a)
        raw, _ = PersonalAccessToken.generate(profile=admin_profile, name="cli")
        auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        client = Client()
        assert client.get(URL, **auth).status_code == 403
        # The same token still reaches an ordinary endpoint.
        assert client.get("/api/leads/", **auth).status_code == 200

    def test_the_org_api_key_is_refused(self, admin_profile, org_a):
        _row(org_a)
        client = Client()
        assert client.get(URL, HTTP_TOKEN=org_a.api_key).status_code == 403
        # The key itself works: it still reads an ordinary endpoint.
        assert client.get("/api/leads/", HTTP_TOKEN=org_a.api_key).status_code == 200

    def test_anonymous_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(URL).status_code in (401, 403)


class TestFilters:
    def test_newest_first_and_paginated(self, admin_client, org_a):
        old = _row(org_a, days_ago=3)
        new = _row(org_a)
        body = admin_client.get(f"{URL}?limit=1").json()
        assert body["count"] == 2
        assert [e["id"] for e in body["results"]] == [str(new.id)]
        body = admin_client.get(f"{URL}?limit=1&offset=1").json()
        assert [e["id"] for e in body["results"]] == [str(old.id)]

    def test_limit_is_capped(self, admin_client, org_a):
        for _ in range(3):
            _row(org_a)
        assert len(admin_client.get(f"{URL}?limit=100000").json()["results"]) == 3

    def test_by_event_type(self, admin_client, org_a):
        _row(org_a, event_type="LOGIN_SUCCESS")
        switch = _row(org_a, event_type="ORG_SWITCH")
        assert _ids(admin_client.get(f"{URL}?event_type=ORG_SWITCH")) == [
            str(switch.id)
        ]

    def test_by_actor(self, admin_client, admin_user, regular_user, org_a):
        _row(org_a, user=admin_user)
        theirs = _row(org_a, user=regular_user)
        assert _ids(admin_client.get(f"{URL}?actor={regular_user.id}")) == [
            str(theirs.id)
        ]

    def test_by_date_range(self, admin_client, org_a):
        _row(org_a, days_ago=10)
        middle = _row(org_a, days_ago=5)
        _row(org_a)
        today = timezone.localdate()
        start = (today - timedelta(days=6)).isoformat()
        end = (today - timedelta(days=4)).isoformat()
        assert _ids(admin_client.get(f"{URL}?from={start}&to={end}")) == [
            str(middle.id)
        ]

    @pytest.mark.parametrize(
        "query",
        [
            "event_type=NOT_A_TYPE",
            "actor=not-a-uuid",
            "from=yesterday",
            "to=2026-02-30",
            "from=2026-09-10&to=2026-09-01",
        ],
    )
    def test_a_malformed_filter_is_400(self, admin_client, org_a, query):
        assert admin_client.get(f"{URL}?{query}").status_code == 400

    def test_the_filter_catalogue_comes_with_the_page(self, admin_client):
        types = admin_client.get(URL).json()["event_types"]
        assert {"value": "WEBHOOK_PAUSED", "label": "Webhook Paused"} in types


class TestWhatIsShown:
    def test_an_entry_carries_only_allow_listed_fields(
        self, admin_client, admin_user, org_a
    ):
        _row(
            org_a,
            event_type="SUSPICIOUS_ACTIVITY",
            user=admin_user,
            description="Switched from Other Tenant Ltd to here",
            metadata={
                "details": "Bearer bcrm_pat_secret",
                "api_key_prefix": "abcd",
                "email": "someone@else.com",
                "action": "export",
                "endpoint_id": "e1",
            },
            ip_address="203.0.113.9",
            request_method="GET",
            request_path="/api/leads/",
            success=False,
        )
        entry = admin_client.get(URL).json()["results"][0]
        assert set(entry) == {
            "id",
            "event_type",
            "event_label",
            "created_at",
            "success",
            "actor",
            "ip_address",
            "user_agent",
            "request_method",
            "request_path",
            "details",
        }
        assert entry["details"] == {"action": "export", "endpoint_id": "e1"}
        assert entry["actor"]["email"] == admin_user.email
        assert entry["event_label"] == "Suspicious Activity"
        assert "Other Tenant" not in str(entry)
        assert "bcrm_pat_secret" not in str(entry)

    def test_a_public_path_is_cut_to_its_prefix(self, admin_client, org_a):
        _row(org_a, request_path="/api/public/csat/tok_abc123/")
        entry = admin_client.get(URL).json()["results"][0]
        assert entry["request_path"] == "/api/public/"


class TestTheRecordedIp:
    """The viewer shows `ip_address` as fact, so the caller cannot choose it.

    A client can send any `X-Forwarded-For`; only the entries the proxies we
    run appended (`REST_FRAMEWORK["NUM_PROXIES"]`, counted from the right) are
    believed, and with no proxy configured the header is ignored.
    """

    def _logged_ip(self, org, **meta):
        from django.test import RequestFactory

        from common.audit_log import audit_log

        request = RequestFactory().get("/api/auth/logout/", **meta)
        audit_log.logout(None, org, request)
        return (
            SecurityAuditLog.objects.filter(event_type="LOGOUT")
            .latest("created_at")
            .ip_address
        )

    def test_a_forged_header_is_ignored_with_no_proxy_configured(self, org_a):
        ip = self._logged_ip(
            org_a, HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="198.51.100.7"
        )
        assert ip == "198.51.100.7"

    def test_behind_one_proxy_only_its_entry_is_believed(self, org_a, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        ip = self._logged_ip(
            org_a,
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9",
            REMOTE_ADDR="127.0.0.1",
        )
        assert ip == "203.0.113.9"

    def test_a_junk_entry_records_nothing_rather_than_junk(self, org_a, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        ip = self._logged_ip(
            org_a, HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="127.0.0.1"
        )
        assert ip is None
