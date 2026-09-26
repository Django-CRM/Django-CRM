"""Google sign-in writes the same audit row a magic-link sign-in does.

`MagicLinkVerifyView` and `MagicLinkVerifyCodeView` have always called
`audit_log.login_success`; the web Google callback and the mobile ID-token
endpoint never did, so most sign-ins left no trace in the security audit log.
Magic link writes no failure row, so neither does Google: a refused sign-in
writes nothing. The row's address comes from `client_ip`, so a relayed
sign-in records the visitor the relay names, and a forged header records the
socket peer.
"""

from unittest.mock import MagicMock, patch

import pytest

from common.audit_log import SecurityAuditLog
from common.models import User
from common.tests.test_auth import _make_fake_id_token

CALLBACK = "/api/auth/google/callback/"
ID_TOKEN = "/api/auth/google/"
CODE = {"code": "c", "code_verifier": "v", "redirect_uri": "http://app.test/cb"}
SECRET = "r" * 48


def _google_exchange(email, email_verified=True):
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "id_token": _make_fake_id_token(email, email_verified=email_verified)
    }
    return patch("common.views.auth_views.requests.post", return_value=response)


def _google_verify(claims):
    """Patch Google's ID-token verification. `claims` None means invalid."""
    verify = patch(
        "google.oauth2.id_token.verify_oauth2_token",
        side_effect=ValueError("bad") if claims is None else None,
        return_value=claims,
    )
    return verify, patch("google.auth.transport.requests.Request")


def _rows():
    return SecurityAuditLog.objects.filter(event_type="LOGIN_SUCCESS")


@pytest.mark.django_db
class TestWebCallback:
    def test_success_writes_one_row_with_the_client_ip(self, unauthenticated_client):
        with _google_exchange("ada@example.com"):
            response = unauthenticated_client.post(
                CALLBACK, CODE, format="json", REMOTE_ADDR="198.51.100.9"
            )

        assert response.status_code == 200, response.content
        row = _rows().get()
        assert row.user == User.objects.get(email="ada@example.com")
        assert row.org is None
        assert row.ip_address == "198.51.100.9"
        assert row.success is True

    def test_a_relayed_sign_in_records_the_visitor(
        self, unauthenticated_client, settings
    ):
        settings.RELAY_SECRET = SECRET
        with _google_exchange("ada@example.com"):
            unauthenticated_client.post(
                CALLBACK,
                CODE,
                format="json",
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_BOTTLECRM_RELAY_SECRET=SECRET,
                HTTP_X_BOTTLECRM_CLIENT_IP="203.0.113.50",
                HTTP_X_FORWARDED_FOR="6.6.6.6",
            )

        assert _rows().get().ip_address == "203.0.113.50"

    def test_an_unverified_email_writes_no_row(self, unauthenticated_client):
        with _google_exchange("ada@example.com", email_verified=False):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")

        assert response.status_code == 400
        assert not SecurityAuditLog.objects.exists()

    def test_a_deactivated_user_writes_no_row(self, unauthenticated_client):
        User.objects.create_user(
            email="gone@example.com", password="x", is_active=False
        )
        with _google_exchange("gone@example.com"):
            response = unauthenticated_client.post(CALLBACK, CODE, format="json")

        assert response.status_code != 200
        assert not _rows().exists()


@pytest.mark.django_db
class TestMobileIdToken:
    def test_success_writes_one_row_with_the_client_ip(self, unauthenticated_client):
        verify, transport = _google_verify(
            {"email": "ada@example.com", "email_verified": True}
        )
        with verify, transport:
            response = unauthenticated_client.post(
                ID_TOKEN, {"idToken": "t"}, format="json", REMOTE_ADDR="198.51.100.9"
            )

        assert response.status_code == 200, response.content
        row = _rows().get()
        assert row.user == User.objects.get(email="ada@example.com")
        assert row.ip_address == "198.51.100.9"

    def test_an_invalid_token_writes_no_row(self, unauthenticated_client):
        verify, transport = _google_verify(None)
        with verify, transport:
            response = unauthenticated_client.post(
                ID_TOKEN, {"idToken": "t"}, format="json"
            )

        assert response.status_code == 400
        assert not SecurityAuditLog.objects.exists()

    def test_an_unverified_email_writes_no_row(self, unauthenticated_client):
        verify, transport = _google_verify(
            {"email": "ada@example.com", "email_verified": False}
        )
        with verify, transport:
            response = unauthenticated_client.post(
                ID_TOKEN, {"idToken": "t"}, format="json"
            )

        assert response.status_code == 400
        assert not SecurityAuditLog.objects.exists()
