"""Per-credential request allowance for personal tokens and the org key."""

import logging
import os
import subprocess
import sys

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from common import ratelimit
from common.models import PersonalAccessToken, Profile, User


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    cache.clear()
    monkeypatch.setattr(ratelimit, "_limits", [])
    # A frozen clock, so a test cannot straddle a minute boundary.
    monkeypatch.setattr(ratelimit, "_now", lambda: 1_700_000_010.0)
    yield
    cache.clear()


def _pat_client(profile, scopes=None, name="cli"):
    raw, _ = PersonalAccessToken.generate(profile=profile, name=name, scopes=scopes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _key_client(org):
    client = APIClient()
    client.credentials(HTTP_TOKEN=org.api_key)
    return client


@pytest.mark.django_db
class TestTokenRateLimit:
    @override_settings(API_TOKEN_RATE_PER_MINUTE=2)
    def test_token_over_the_limit_gets_429_with_retry_after(self, admin_profile):
        client = _pat_client(admin_profile)
        assert client.get("/api/leads/").status_code == 200
        assert client.get("/api/leads/").status_code == 200
        resp = client.get("/api/leads/")
        assert resp.status_code == 429
        assert 1 <= int(resp["Retry-After"]) <= 60
        assert "detail" in resp.json()

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_each_credential_has_its_own_allowance(self, admin_profile):
        first = _pat_client(admin_profile, name="one")
        second = _pat_client(admin_profile, name="two")
        assert first.get("/api/leads/").status_code == 200
        assert first.get("/api/leads/").status_code == 429
        assert second.get("/api/leads/").status_code == 200

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_org_key_is_counted_per_org(self, org_a, org_b, admin_profile):
        user = User.objects.create_user(email="adm-b@example.com", password="x")
        Profile.objects.create(user=user, org=org_b, role="ADMIN", is_active=True)
        assert _key_client(org_a).get("/api/leads/").status_code == 200
        assert _key_client(org_a).get("/api/leads/").status_code == 429
        # One shared bucket would answer 429 here.
        assert _key_client(org_b).get("/api/leads/").status_code == 200
        assert _key_client(org_b).get("/api/leads/").status_code == 429

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_jwt_requests_are_never_counted(self, admin_client):
        for _ in range(5):
            assert admin_client.get("/api/leads/").status_code == 200

    @override_settings(API_TOKEN_RATE_PER_MINUTE=None)
    def test_no_limit_when_unset(self, admin_profile, monkeypatch):
        def boom(*args):
            raise AssertionError("counted with no limit")

        monkeypatch.setattr(ratelimit, "hit", boom)
        client = _pat_client(admin_profile)
        for _ in range(5):
            assert client.get("/api/leads/").status_code == 200

    @override_settings(API_TOKEN_RATE_PER_MINUTE=100)
    def test_registered_limit_lowers_the_ceiling(self, admin_profile):
        ratelimit.register_limit(lambda org: None)
        ratelimit.register_limit(lambda org: 1)
        client = _pat_client(admin_profile)
        assert client.get("/api/leads/").status_code == 200
        assert client.get("/api/leads/").status_code == 429

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_registered_limit_never_raises_the_setting(self, org_a):
        ratelimit.register_limit(lambda org: 500)
        assert ratelimit.token_limit_per_minute(org_a) == 1

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_refused_scope_is_not_counted(self, admin_profile):
        client = _pat_client(admin_profile, scopes=["leads:read"])
        for _ in range(3):
            assert client.get("/api/contacts/").status_code == 403
        assert client.get("/api/leads/").status_code == 200


@pytest.mark.django_db
class TestFailsOpen:
    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_a_cache_error_lets_the_request_through(
        self, admin_profile, monkeypatch, caplog
    ):
        def broken(*args):
            raise ConnectionError("cache down")

        monkeypatch.setattr(ratelimit, "hit", broken)
        client = _pat_client(admin_profile)
        with caplog.at_level(logging.WARNING, logger="common.middleware.get_company"):
            assert client.get("/api/leads/").status_code == 200
        messages = [
            r.getMessage() for r in caplog.records if "rate limit" in r.getMessage()
        ]
        assert len(messages) == 1
        assert "bcrm_pat_" not in messages[0]

    @override_settings(API_TOKEN_RATE_PER_MINUTE=1)
    def test_a_raising_registered_limit_is_ignored_and_the_rest_apply(
        self, admin_profile
    ):
        def broken(org):
            raise RuntimeError("extension fault")

        ratelimit.register_limit(broken)
        client = _pat_client(admin_profile)
        assert client.get("/api/leads/").status_code == 200
        assert client.get("/api/leads/").status_code == 429


class TestTheSetting:
    @pytest.mark.parametrize(
        "raw, expected",
        [("", None), ("0", None), ("-5", None), ("1200", 1200)],
    )
    def test_below_one_is_unset(self, raw, expected):
        backend = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "crm.test_settings"}
        env["API_TOKEN_RATE_PER_MINUTE"] = raw
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from django.conf import settings; "
                "print(repr(settings.API_TOKEN_RATE_PER_MINUTE))",
            ],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip().splitlines()[-1] == repr(expected)
