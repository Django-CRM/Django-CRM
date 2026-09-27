"""Refused staff sign-ins write one LOGIN_FAILURE row each, capped per IP.

`audit_log.login_failure` had no callers, so a run of refused sign-ins left no
trace. Every refusal on the Google web callback, the mobile Google ID-token
endpoint, magic-link verify and verify-code now writes one row with a short
reason code. Token refresh and the magic-link request write none.

The rows are written by anonymous callers into `security_audit_log`, which has
no org and no RLS policy, so a client IP gets at most
`LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR` of them. The refusal itself is unchanged:
same status, same body.
"""

import secrets
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.utils import timezone

from common.audit_log import (
    LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR,
    AuditLogger,
    SecurityAuditLog,
)
from common.models import MagicLinkToken, User
from common.tests.test_auth import _make_fake_id_token

CALLBACK = "/api/auth/google/callback/"
ID_TOKEN = "/api/auth/google/"
VERIFY = "/api/auth/magic-link/verify/"
VERIFY_CODE = "/api/auth/magic-link/verify-code/"
CODE = {"code": "c", "code_verifier": "v", "redirect_uri": "http://app.test/cb"}


@pytest.fixture(autouse=True)
def _fresh_cap():
    """The cap counts in the shared test cache; start each test at zero."""
    cache.clear()
    yield
    cache.clear()


def _failures():
    return SecurityAuditLog.objects.filter(event_type="LOGIN_FAILURE")


def _only_failure(reason, email=""):
    row = _failures().get()
    assert row.metadata == {"email": email, "reason": reason}
    assert row.success is False
    assert row.org is None
    return row


def _exchange(status_code=200, body=None, raises=None):
    response = MagicMock(status_code=status_code, content=b"x")
    response.json.return_value = body or {}
    if raises:
        return patch("common.views.auth_views.requests.post", side_effect=raises)
    return patch("common.views.auth_views.requests.post", return_value=response)


def _google_verify(claims):
    verify = patch(
        "google.oauth2.id_token.verify_oauth2_token",
        side_effect=ValueError("bad") if claims is None else None,
        return_value=claims,
    )
    return verify, patch("google.auth.transport.requests.Request")


def _link(email, **kwargs):
    defaults = {"expires_at": timezone.now() + timedelta(minutes=10)}
    defaults.update(kwargs)
    return MagicLinkToken.objects.create(
        email=email, token=secrets.token_hex(32), **defaults
    )


def _code(email, code="123456", **kwargs):
    return _link(email, delivery="code", code_hash=make_password(code), **kwargs)


class TestGoogleWebCallback:
    def test_google_unreachable(self, unauthenticated_client):
        with _exchange(raises=requests.ConnectionError()):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 502
        assert response.json() == {"error": "Failed to communicate with Google"}
        _only_failure("google_unreachable")

    def test_code_rejected(self, unauthenticated_client):
        with _exchange(400, {"error_description": "Bad Request"}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 400
        assert response.json() == {"error": "Bad Request"}
        _only_failure("google_code_rejected")

    @pytest.mark.parametrize("status_code", [400, 502])
    def test_a_rejection_that_is_not_json(self, unauthenticated_client, status_code):
        """A proxy or outage page in place of Google's JSON used to be a 500."""
        response = MagicMock(status_code=status_code, content=b"<html>Bad</html>")
        response.json.side_effect = requests.JSONDecodeError("x", "<html>", 0)
        with patch("common.views.auth_views.requests.post", return_value=response):
            reply = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert reply.status_code == 400
        assert reply.json() == {"error": "Token exchange failed"}
        _only_failure("google_code_rejected")

    def test_a_rejection_that_is_a_json_list(self, unauthenticated_client):
        with _exchange(400, ["not", "an", "object"]):
            reply = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert reply.status_code == 400
        assert reply.json() == {"error": "Token exchange failed"}
        _only_failure("google_code_rejected")

    def test_a_success_that_is_not_json(self, unauthenticated_client):
        response = MagicMock(status_code=200, content=b"<html>ok</html>")
        response.json.side_effect = requests.JSONDecodeError("x", "<html>", 0)
        with patch("common.views.auth_views.requests.post", return_value=response):
            reply = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert reply.status_code == 400
        assert reply.json() == {"error": "No ID token received"}
        _only_failure("google_invalid_id_token")

    def test_an_id_token_that_is_not_a_string(self, unauthenticated_client):
        with _exchange(200, {"id_token": 12345}):
            reply = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert reply.status_code == 400
        assert reply.json() == {"error": "Invalid ID token format"}
        _only_failure("google_invalid_id_token")

    def test_no_id_token(self, unauthenticated_client):
        with _exchange(200, {"access_token": "a"}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 400
        assert response.json() == {"error": "No ID token received"}
        _only_failure("google_invalid_id_token")

    def test_malformed_id_token(self, unauthenticated_client):
        with _exchange(200, {"id_token": "no-dots"}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid ID token format"}
        _only_failure("google_invalid_id_token")

    def test_no_email(self, unauthenticated_client):
        with _exchange(200, {"id_token": _make_fake_id_token("")}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 400
        assert response.json() == {"error": "No email in token"}
        _only_failure("google_no_email")

    def test_unverified_email(self, unauthenticated_client):
        token = _make_fake_id_token("ada@example.com", email_verified=False)
        with _exchange(200, {"id_token": token}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 400
        assert response.json() == {"error": "Google account email is not verified"}
        _only_failure("google_email_unverified", "ada@example.com")
        assert not User.objects.filter(email="ada@example.com").exists()

    def test_disabled_account(self, unauthenticated_client):
        user = User.objects.create_user(
            email="gone@example.com", password="x", is_active=False
        )
        with _exchange(200, {"id_token": _make_fake_id_token("gone@example.com")}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 403
        assert response.json() == {"error": "User account is disabled"}
        assert _only_failure("account_disabled", "gone@example.com").user == user

    def test_success_writes_no_failure_row(self, unauthenticated_client):
        with _exchange(200, {"id_token": _make_fake_id_token("ada@example.com")}):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")
        assert response.status_code == 200
        assert not _failures().exists()


class TestGoogleMobileIdToken:
    def _post(self, client, claims):
        verify, transport = _google_verify(claims)
        with verify, transport:
            return client.post(ID_TOKEN, {"idToken": "t"}, format="json")

    def test_invalid_token(self, unauthenticated_client):
        response = self._post(unauthenticated_client, None)
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid token"}
        _only_failure("google_invalid_token")

    def test_no_email(self, unauthenticated_client):
        response = self._post(unauthenticated_client, {"email_verified": True})
        assert response.status_code == 400
        assert response.json() == {"error": "No email in token"}
        _only_failure("google_no_email")

    def test_unverified_email(self, unauthenticated_client):
        response = self._post(
            unauthenticated_client,
            {"email": "ada@example.com", "email_verified": False},
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Google account email is not verified"}
        _only_failure("google_email_unverified", "ada@example.com")

    def test_disabled_account(self, unauthenticated_client):
        User.objects.create_user(
            email="gone@example.com", password="x", is_active=False
        )
        response = self._post(
            unauthenticated_client,
            {"email": "gone@example.com", "email_verified": True},
        )
        assert response.status_code == 403
        assert response.json() == {"error": "User account is disabled"}
        _only_failure("account_disabled", "gone@example.com")

    def test_success_writes_no_failure_row(self, unauthenticated_client):
        response = self._post(
            unauthenticated_client,
            {"email": "ada@example.com", "email_verified": True},
        )
        assert response.status_code == 200
        assert not _failures().exists()


class TestMagicLinkVerify:
    def test_unknown_link(self, unauthenticated_client):
        response = unauthenticated_client.post(
            VERIFY, {"token": "nonexistent"}, format="json"
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid or expired link"}
        _only_failure("magic_link_invalid")

    def test_replayed_link_names_its_email_but_not_the_token(
        self, unauthenticated_client
    ):
        link = _link("ada@example.com", is_used=True)
        response = unauthenticated_client.post(
            VERIFY, {"token": link.token}, format="json"
        )
        assert response.status_code == 400
        row = _only_failure("magic_link_invalid", "ada@example.com")
        assert link.token not in row.description

    def test_expired_link(self, unauthenticated_client):
        link = _link(
            "ada@example.com", expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = unauthenticated_client.post(
            VERIFY, {"token": link.token}, format="json"
        )
        assert response.status_code == 400
        # The expired row is swept before the lookup, so no email survives.
        _only_failure("magic_link_invalid")

    def test_disabled_account(self, unauthenticated_client):
        User.objects.create_user(
            email="gone@example.com", password="x", is_active=False
        )
        link = _link("gone@example.com")
        response = unauthenticated_client.post(
            VERIFY, {"token": link.token}, format="json"
        )
        assert response.status_code == 403
        assert response.json() == {"error": "User account is disabled"}
        _only_failure("account_disabled", "gone@example.com")

    def test_success_writes_no_failure_row(self, unauthenticated_client):
        link = _link("ada@example.com")
        response = unauthenticated_client.post(
            VERIFY, {"token": link.token}, format="json"
        )
        assert response.status_code == 200
        assert not _failures().exists()


class TestMagicLinkVerifyCode:
    def _post(self, client, email="ada@example.com", code="123456"):
        return client.post(VERIFY_CODE, {"email": email, "code": code}, format="json")

    def test_no_active_code(self, unauthenticated_client):
        response = self._post(unauthenticated_client)
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid or expired code"}
        _only_failure("code_not_found", "ada@example.com")

    def test_wrong_code_keeps_its_attempt_and_names_no_code(
        self, unauthenticated_client
    ):
        token = _code("ada@example.com", code="123456")
        response = self._post(unauthenticated_client, code="999999")
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid or expired code"}
        row = _only_failure("code_wrong", "ada@example.com")
        assert "999999" not in row.description
        token.refresh_from_db()
        assert token.attempts == 1
        assert token.is_used is False

    def test_the_attempt_that_burns_the_code(self, unauthenticated_client):
        token = _code("ada@example.com", attempts=4)
        response = self._post(unauthenticated_client, code="999999")
        assert response.status_code == 400
        _only_failure("code_attempts_exhausted", "ada@example.com")
        token.refresh_from_db()
        assert token.is_used is True

    def test_a_burned_code_is_not_found(self, unauthenticated_client):
        _code("ada@example.com", is_used=True)
        response = self._post(unauthenticated_client)
        assert response.status_code == 400
        _only_failure("code_not_found", "ada@example.com")

    def test_disabled_account(self, unauthenticated_client):
        User.objects.create_user(
            email="gone@example.com", password="x", is_active=False
        )
        _code("gone@example.com")
        response = self._post(unauthenticated_client, email="gone@example.com")
        assert response.status_code == 403
        assert response.json() == {"error": "User account is disabled"}
        _only_failure("account_disabled", "gone@example.com")

    def test_success_writes_no_failure_row(self, unauthenticated_client):
        _code("ada@example.com")
        response = self._post(unauthenticated_client)
        assert response.status_code == 200
        assert not _failures().exists()


class TestNotAudited:
    def test_a_bad_refresh_token_writes_no_failure_row(self, unauthenticated_client):
        response = unauthenticated_client.post(
            "/api/auth/refresh-token/", {"refresh": "junk"}, format="json"
        )
        assert response.status_code >= 400
        assert not _failures().exists()

    def test_a_magic_link_request_writes_no_failure_row(self, unauthenticated_client):
        response = unauthenticated_client.post(
            "/api/auth/magic-link/request/", {"email": "nope"}, format="json"
        )
        assert response.status_code == 200
        assert not _failures().exists()


class TestCap:
    def _fail(self, client, ip):
        return client.post(
            VERIFY, {"token": "nonexistent"}, format="json", REMOTE_ADDR=ip
        )

    def test_rows_stop_at_the_cap_per_ip_and_another_ip_still_writes(
        self, unauthenticated_client
    ):
        for _ in range(LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR + 5):
            response = self._fail(unauthenticated_client, "198.51.100.7")
            # Refused the same way past the cap: only the row is dropped.
            assert response.status_code == 400
            assert response.json() == {"error": "Invalid or expired link"}

        assert LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR == 20
        assert _failures().filter(ip_address="198.51.100.7").count() == 20

        self._fail(unauthenticated_client, "203.0.113.9")
        assert _failures().filter(ip_address="203.0.113.9").count() == 1

    def test_the_window_resets(self, unauthenticated_client):
        for _ in range(LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR):
            self._fail(unauthenticated_client, "198.51.100.7")
        cache.clear()  # the hour has passed
        self._fail(unauthenticated_client, "198.51.100.7")
        assert _failures().count() == LOGIN_FAILURE_ROWS_PER_IP_PER_HOUR + 1

    @pytest.mark.parametrize("method", ["add", "incr"])
    def test_a_cache_outage_skips_the_row_and_keeps_the_400(
        self, unauthenticated_client, caplog, method
    ):
        """Redis down must not turn a refused sign-in into a 500, and must not
        let a flood write uncapped rows into the unscoped audit table."""
        _code("ada@example.com", code="123456")
        caplog.set_level("WARNING", logger="security.audit")
        with patch(
            f"common.audit_log.cache.{method}",
            side_effect=ConnectionError("redis down"),
        ):
            response = unauthenticated_client.post(
                VERIFY_CODE,
                {"email": "ada@example.com", "code": "999999"},
                format="json",
            )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid or expired code"}
        assert not _failures().exists()
        warnings = [r.getMessage() for r in caplog.records]
        assert (
            "Login failure audit row skipped: cache unavailable (ConnectionError)"
            in (warnings)
        )
        assert not any("ada@example.com" in w or "999999" in w for w in warnings)


class TestRowShape:
    def test_the_stored_email_is_truncated(self):
        AuditLogger().login_failure("a" * 400 + "@example.com", "code_wrong")
        row = _failures().get()
        assert row.metadata["email"] == "a" * 254
        assert len(row.metadata["email"]) == 254

    def test_an_org_admin_never_sees_failure_rows(
        self, admin_client, admin_user, unauthenticated_client
    ):
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
        _code(admin_user.email)
        unauthenticated_client.post(
            VERIFY_CODE, {"email": admin_user.email, "code": "123456"}, format="json"
        )
        admin_user.is_active = True
        admin_user.save(update_fields=["is_active"])
        row = _only_failure("account_disabled", admin_user.email)
        assert row.user == admin_user

        response = admin_client.get("/api/org/audit-log/")
        assert response.status_code == 200
        ids = [entry["id"] for entry in response.json()["results"]]
        assert str(row.id) not in ids
