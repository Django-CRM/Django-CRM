"""
Tests for magic link authentication: request, verify, rate limiting, token expiry.

Run with: pytest common/tests/test_magic_link.py -v
"""

import secrets
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

from common.models import MagicLinkToken, Profile, User


@pytest.fixture(autouse=True)
def _fresh_throttle():
    """The request endpoints are throttled per client, and every test here
    calls from the same address."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestMagicLinkRequest:
    """Tests for POST /api/auth/magic-link/request/"""

    url = "/api/auth/magic-link/request/"

    def test_request_returns_200_for_valid_email(self, unauthenticated_client):
        """Should always return 200 regardless of whether email exists."""
        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = lambda *a, **kw: None
            response = unauthenticated_client.post(
                self.url, {"email": "anyone@example.com"}, format="json"
            )
        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data

    def test_request_creates_token(self, unauthenticated_client):
        """Should create a MagicLinkToken record."""
        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = lambda *a, **kw: None
            unauthenticated_client.post(
                self.url, {"email": "test@example.com"}, format="json"
            )
        assert MagicLinkToken.objects.filter(email="test@example.com").exists()

    def test_request_invalidates_previous_tokens(self, unauthenticated_client):
        """New request should invalidate existing unused tokens."""
        old_token = MagicLinkToken.objects.create(
            email="test@example.com",
            token=secrets.token_hex(32),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = lambda *a, **kw: None
            unauthenticated_client.post(
                self.url, {"email": "test@example.com"}, format="json"
            )
        old_token.refresh_from_db()
        assert old_token.is_used is True

    def test_request_rate_limit(self, unauthenticated_client):
        """Should silently reject after 5 requests per hour."""
        email = "ratelimit@example.com"
        for _ in range(5):
            MagicLinkToken.objects.create(
                email=email,
                token=secrets.token_hex(32),
                expires_at=timezone.now() + timedelta(minutes=10),
            )
        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = lambda *a, **kw: None
            unauthenticated_client.post(self.url, {"email": email}, format="json")
        # Should still be exactly 5 tokens (no new one created due to rate limit)
        assert MagicLinkToken.objects.filter(email=email).count() == 5

    def test_request_invalid_email_format(self, unauthenticated_client):
        """Invalid email should still return 200 (no enumeration)."""
        response = unauthenticated_client.post(
            self.url, {"email": "not-an-email"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

    def test_request_missing_email(self, unauthenticated_client):
        """Missing email should still return 200 (no enumeration)."""
        response = unauthenticated_client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestMagicLinkVerify:
    """Tests for POST /api/auth/magic-link/verify/"""

    url = "/api/auth/magic-link/verify/"

    def _create_valid_token(self, email="test@example.com"):
        return MagicLinkToken.objects.create(
            email=email,
            token=secrets.token_hex(32),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def test_verify_valid_token_existing_user(
        self, unauthenticated_client, admin_user, admin_profile
    ):
        """Valid token for existing user should return JWT tokens."""
        token_obj = self._create_valid_token(email=admin_user.email)
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.data
        assert "refresh_token" in response.data
        assert "user" in response.data

    def test_verify_valid_token_new_user(self, unauthenticated_client):
        """Valid token for new email should create user and return tokens."""
        token_obj = self._create_valid_token(email="newuser@example.com")
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.data
        assert User.objects.filter(email="newuser@example.com").exists()
        user = User.objects.get(email="newuser@example.com")
        assert user.is_active is True

    def test_verify_marks_token_used(self, unauthenticated_client):
        """Token should be marked as used after verification."""
        token_obj = self._create_valid_token()
        unauthenticated_client.post(self.url, {"token": token_obj.token}, format="json")
        token_obj.refresh_from_db()
        assert token_obj.is_used is True
        assert token_obj.used_at is not None

    def test_verify_used_token_rejected(self, unauthenticated_client):
        """Already used token should be rejected."""
        token_obj = self._create_valid_token()
        token_obj.is_used = True
        token_obj.save()
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_expired_token_rejected(self, unauthenticated_client):
        """Expired token should be rejected."""
        token_obj = MagicLinkToken.objects.create(
            email="expired@example.com",
            token=secrets.token_hex(32),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_invalid_token(self, unauthenticated_client):
        """Non-existent token should be rejected."""
        response = unauthenticated_client.post(
            self.url, {"token": "nonexistent-token-value"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_missing_token(self, unauthenticated_client):
        """Missing token field should be rejected."""
        response = unauthenticated_client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_returns_org_for_existing_user(
        self, unauthenticated_client, admin_user, admin_profile, org_a
    ):
        """Existing user with org should get current_org in response."""
        token_obj = self._create_valid_token(email=admin_user.email)
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "current_org" in response.data
        assert response.data["current_org"]["id"] == str(org_a.id)

    def test_verify_new_user_no_org(self, unauthenticated_client):
        """New user should not have current_org."""
        token_obj = self._create_valid_token(email="brand-new@example.com")
        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "current_org" not in response.data

    def test_verify_replay_attack_prevented(self, unauthenticated_client):
        """Same token should not work twice."""
        token_obj = self._create_valid_token()
        # First use
        response1 = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response1.status_code == status.HTTP_200_OK
        # Second use (replay)
        response2 = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )
        assert response2.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_rejects_deactivated_user(self, unauthenticated_client, admin_user):
        """A deactivated account must not be able to log itself back in.

        Deactivation is the offboarding lever; if a magic link still mints
        tokens for `is_active=False`, revoking access does not actually revoke
        access.
        """
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
        token_obj = self._create_valid_token(email=admin_user.email)

        response = unauthenticated_client.post(
            self.url, {"token": token_obj.token}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "access_token" not in response.data


@pytest.mark.django_db
class TestMagicLinkRequestCode:
    """Tests for POST /api/auth/magic-link/request/ with delivery=code (mobile OTP flow)."""

    url = "/api/auth/magic-link/request/"

    def test_request_code_creates_code_hash(self, unauthenticated_client):
        """delivery=code should populate code_hash and set delivery."""
        captured = {}

        def fake_delay(token_id, *args, **kwargs):
            captured["token_id"] = token_id
            captured["raw_code"] = kwargs.get("raw_code")

        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = fake_delay
            response = unauthenticated_client.post(
                self.url,
                {"email": "otp@example.com", "delivery": "code"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        token = MagicLinkToken.objects.get(email="otp@example.com")
        assert token.delivery == "code"
        assert token.code_hash != ""
        # raw_code is 6 digits and was handed off to the Celery task; not on the row.
        assert captured["raw_code"] is not None
        assert len(captured["raw_code"]) == 6
        assert captured["raw_code"].isdigit()

    def test_request_link_default_delivery(self, unauthenticated_client):
        """Omitting delivery should default to link (no code_hash)."""
        with patch("common.tasks.send_magic_link_email") as mock_task:
            mock_task.delay = lambda *a, **kw: None
            unauthenticated_client.post(
                self.url, {"email": "linkdefault@example.com"}, format="json"
            )
        token = MagicLinkToken.objects.get(email="linkdefault@example.com")
        assert token.delivery == "link"
        assert token.code_hash == ""


@pytest.mark.django_db
class TestMagicLinkVerifyCode:
    """Tests for POST /api/auth/magic-link/verify-code/ (mobile OTP flow)."""

    url = "/api/auth/magic-link/verify-code/"

    def _create_code_token(self, email="otp@example.com", code="123456", **kwargs):
        from django.contrib.auth.hashers import make_password

        return MagicLinkToken.objects.create(
            email=email,
            token=secrets.token_hex(32),
            delivery="code",
            code_hash=make_password(code),
            expires_at=timezone.now() + timedelta(minutes=10),
            **kwargs,
        )

    def test_verify_code_valid_new_user(self, unauthenticated_client):
        """Valid code for a new email mints tokens and creates the user."""
        self._create_code_token(email="newotp@example.com", code="654321")
        response = unauthenticated_client.post(
            self.url,
            {"email": "newotp@example.com", "code": "654321"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.data
        assert "refresh_token" in response.data
        assert User.objects.filter(email="newotp@example.com").exists()

    def test_verify_code_valid_existing_user(
        self, unauthenticated_client, admin_user, admin_profile
    ):
        """Valid code for an existing user returns current_org."""
        self._create_code_token(email=admin_user.email, code="111111")
        response = unauthenticated_client.post(
            self.url,
            {"email": admin_user.email, "code": "111111"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "current_org" in response.data

    def test_verify_code_marks_used(self, unauthenticated_client):
        """Successful verification marks the row used (replay protection)."""
        token = self._create_code_token(email="markused@example.com", code="222222")
        unauthenticated_client.post(
            self.url,
            {"email": "markused@example.com", "code": "222222"},
            format="json",
        )
        token.refresh_from_db()
        assert token.is_used is True
        assert token.used_at is not None

    def test_verify_code_wrong_increments_attempts(self, unauthenticated_client):
        """Wrong code increments attempts and returns 400."""
        token = self._create_code_token(email="wrong@example.com", code="333333")
        response = unauthenticated_client.post(
            self.url,
            {"email": "wrong@example.com", "code": "000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        token.refresh_from_db()
        assert token.attempts == 1
        assert token.is_used is False

    def test_verify_code_lockout_after_max_attempts(self, unauthenticated_client):
        """After 5 wrong attempts the row is locked (is_used=True)."""
        token = self._create_code_token(email="lockout@example.com", code="444444")
        for _ in range(5):
            unauthenticated_client.post(
                self.url,
                {"email": "lockout@example.com", "code": "000000"},
                format="json",
            )
        token.refresh_from_db()
        assert token.attempts == 5
        assert token.is_used is True
        # The right code now also fails, because the row is locked.
        response = unauthenticated_client.post(
            self.url,
            {"email": "lockout@example.com", "code": "444444"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_expired(self, unauthenticated_client):
        """Expired token returns 400."""
        from django.contrib.auth.hashers import make_password

        MagicLinkToken.objects.create(
            email="expired-otp@example.com",
            token=secrets.token_hex(32),
            delivery="code",
            code_hash=make_password("555555"),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        response = unauthenticated_client.post(
            self.url,
            {"email": "expired-otp@example.com", "code": "555555"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_replay_rejected(self, unauthenticated_client):
        """Second use of the same code is rejected."""
        self._create_code_token(email="replay@example.com", code="666666")
        first = unauthenticated_client.post(
            self.url,
            {"email": "replay@example.com", "code": "666666"},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK
        second = unauthenticated_client.post(
            self.url,
            {"email": "replay@example.com", "code": "666666"},
            format="json",
        )
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_email_mismatch(self, unauthenticated_client):
        """Right code on a different email is rejected (no cross-account use)."""
        self._create_code_token(email="alice@example.com", code="777777")
        response = unauthenticated_client.post(
            self.url,
            {"email": "bob@example.com", "code": "777777"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_missing_fields(self, unauthenticated_client):
        """Missing email or code returns 400."""
        r1 = unauthenticated_client.post(
            self.url, {"email": "x@example.com"}, format="json"
        )
        assert r1.status_code == status.HTTP_400_BAD_REQUEST
        r2 = unauthenticated_client.post(self.url, {"code": "123456"}, format="json")
        assert r2.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_non_numeric(self, unauthenticated_client):
        """Non-numeric code is rejected at serializer level."""
        self._create_code_token(email="numeric@example.com", code="888888")
        response = unauthenticated_client.post(
            self.url,
            {"email": "numeric@example.com", "code": "abcdef"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verify_code_ignores_link_tokens(self, unauthenticated_client):
        """A delivery=link token must not satisfy verify-code, even with same email."""

        # Link-style token (no code_hash, delivery=link by default)
        MagicLinkToken.objects.create(
            email="linktok@example.com",
            token=secrets.token_hex(32),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        # Concurrently, attacker tries the verify-code endpoint with a guessed code
        response = unauthenticated_client.post(
            self.url,
            {"email": "linktok@example.com", "code": "999999"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Also confirm that even a code that hashes to *something* on a link
        # token can't be used. There's no code_hash on a link row.
        # (Defensive: ensure the lookup filters by delivery="code".)
        assert not MagicLinkToken.objects.filter(
            email="linktok@example.com", is_used=True
        ).exists()

    def test_verify_code_rejects_deactivated_user(
        self, unauthenticated_client, admin_user
    ):
        """The OTP path must honour deactivation just like the link path."""
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
        self._create_code_token(email=admin_user.email, code="222222")

        response = unauthenticated_client.post(
            self.url,
            {"email": admin_user.email, "code": "222222"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "access_token" not in response.data


@pytest.mark.django_db
class TestVerifyCodeOrgSelection:
    """The code flow must not choose an org on the user's behalf.

    It used to bind the session to ``profiles.first()``, an arbitrary row, and
    return only ``current_org``. A three-org admin landed in whichever org the
    database returned first, with no picker (the client needs an org list to
    offer one) and the org named nowhere on screen.
    """

    url = "/api/auth/magic-link/verify-code/"

    def _code_token(self, email, code="123456"):
        from django.contrib.auth.hashers import make_password

        return MagicLinkToken.objects.create(
            email=email,
            token=secrets.token_hex(32),
            delivery="code",
            code_hash=make_password(code),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def _verify(self, client, email, code="123456"):
        return client.post(self.url, {"email": email, "code": code}, format="json")

    def test_single_org_user_is_bound_and_skips_the_picker(
        self, unauthenticated_client, admin_user, admin_profile, org_a
    ):
        self._code_token(admin_user.email)
        response = self._verify(unauthenticated_client, admin_user.email)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["current_org"]["id"] == str(org_a.id)
        assert [o["id"] for o in response.data["organizations"]] == [str(org_a.id)]

    def test_the_listed_org_says_which_currency_it_uses(
        self, unauthenticated_client, admin_user, admin_profile, org_a
    ):
        """The client defaults a new deal's currency from this record, so an
        org list without a currency made every org look like a dollar org."""
        org_a.default_currency = "GBP"
        org_a.save(update_fields=["default_currency"])
        self._code_token(admin_user.email)

        response = self._verify(unauthenticated_client, admin_user.email)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["organizations"][0]["default_currency"] == "GBP"
        assert response.data["organizations"][0]["currency_symbol"] == "£"

    def test_multi_org_user_is_offered_the_choice_instead_of_one_being_made(
        self, unauthenticated_client, admin_user, admin_profile, org_a, org_b
    ):
        Profile.objects.create(user=admin_user, org=org_b, role="ADMIN", is_active=True)
        self._code_token(admin_user.email)
        response = self._verify(unauthenticated_client, admin_user.email)

        assert response.status_code == status.HTTP_200_OK
        # No org chosen for them ...
        assert "current_org" not in response.data
        # ... and both are offered, so the client can show a picker.
        returned = {o["id"] for o in response.data["organizations"]}
        assert returned == {str(org_a.id), str(org_b.id)}

    def test_organizations_carry_the_role(
        self, unauthenticated_client, admin_user, admin_profile, org_a
    ):
        self._code_token(admin_user.email)
        response = self._verify(unauthenticated_client, admin_user.email)

        assert response.data["organizations"][0]["role"] == "ADMIN"

    def test_deactivated_membership_is_not_offered(
        self, unauthenticated_client, admin_user, admin_profile, org_a, org_b
    ):
        Profile.objects.create(
            user=admin_user, org=org_b, role="ADMIN", is_active=False
        )
        self._code_token(admin_user.email)
        response = self._verify(unauthenticated_client, admin_user.email)

        returned = {o["id"] for o in response.data["organizations"]}
        assert returned == {str(org_a.id)}
        # One active membership left, so it is still bound automatically.
        assert response.data["current_org"]["id"] == str(org_a.id)

    def test_user_with_no_org_gets_an_empty_list_not_a_missing_key(
        self, unauthenticated_client
    ):
        self._code_token("orgless@example.com")
        response = self._verify(unauthenticated_client, "orgless@example.com")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["organizations"] == []
        assert "current_org" not in response.data
