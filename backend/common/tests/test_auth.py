"""
Tests for authentication views: me, token refresh, org switch,
Google OAuth callback, Google ID token, and token refresh edge cases.

Run with: pytest common/tests/test_auth.py -v
"""

import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework import status

from common.models import Profile, User
from common.serializer import OrgAwareRefreshToken


@pytest.mark.django_db
class TestMeView:
    """Tests for GET /api/auth/me/"""

    url = "/api/auth/me/"

    def test_me_authenticated(self, admin_client, admin_user):
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == admin_user.email

    def test_me_unauthenticated(self, unauthenticated_client):
        response = unauthenticated_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_me_returns_organizations(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        """Me endpoint should include user's organizations."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "organizations" in response.data
        org_ids = [o["id"] for o in response.data["organizations"]]
        assert str(org_a.id) in org_ids


@pytest.mark.django_db
class TestTokenRefreshView:
    """Tests for POST /api/auth/refresh-token/"""

    url = "/api/auth/refresh-token/"

    def test_refresh_valid_token(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        response = unauthenticated_client.post(
            self.url,
            {"refresh": str(token)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data

    def test_org_settings_claim_carries_the_org_timezone(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """The web's "today" defaults read the org's zone from this claim, so
        a refresh re-derives it: a zone changed in settings reaches the next
        token rather than waiting for a new sign-in."""
        from rest_framework_simplejwt.tokens import AccessToken

        org_a.timezone = "Asia/Kolkata"
        org_a.save(update_fields=["timezone"])
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        claim = AccessToken(str(token.access_token))["org_settings"]
        assert claim["timezone"] == "Asia/Kolkata"

        org_a.timezone = "America/New_York"
        org_a.save(update_fields=["timezone"])
        response = unauthenticated_client.post(
            self.url, {"refresh": str(token)}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        claim = AccessToken(response.data["access"])["org_settings"]
        assert claim["timezone"] == "America/New_York"

    def test_refresh_invalid_token(self, unauthenticated_client):
        response = unauthenticated_client.post(
            self.url,
            {"refresh": "invalid-token-string"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_missing_token(self, unauthenticated_client):
        """Missing refresh token should fail."""
        response = unauthenticated_client.post(
            self.url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_token_without_org(self, unauthenticated_client, admin_user):
        """Refresh token without org context should still work."""
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, None)
        response = unauthenticated_client.post(
            self.url,
            {"refresh": str(token)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data

    def test_refresh_revoked_membership(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """Refreshing token after org membership is revoked should fail."""
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        # Deactivate the profile (revoke membership)
        admin_profile.is_active = False
        admin_profile.save()
        response = unauthenticated_client.post(
            self.url,
            {"refresh": str(token)},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_refresh_inactive_user(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """Refreshing token for an inactive user should fail."""
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        admin_user.is_active = False
        admin_user.save()
        response = unauthenticated_client.post(
            self.url,
            {"refresh": str(token)},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_refresh_rejects_replayed_token(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """A refresh token is single-use: replaying it after rotation must fail.

        Without blacklisting, a stolen refresh token keeps minting access
        tokens for its full lifetime even after the legitimate user rotates.
        """
        token = str(
            OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        )

        first = unauthenticated_client.post(self.url, {"refresh": token}, format="json")
        assert first.status_code == status.HTTP_200_OK

        replay = unauthenticated_client.post(
            self.url, {"refresh": token}, format="json"
        )
        assert replay.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_rejects_replayed_token_without_org(
        self, unauthenticated_client, admin_user
    ):
        """The no-org refresh branch must rotate its token too."""
        token = str(OrgAwareRefreshToken.for_user_and_org(admin_user, None))

        first = unauthenticated_client.post(self.url, {"refresh": token}, format="json")
        assert first.status_code == status.HTTP_200_OK

        replay = unauthenticated_client.post(
            self.url, {"refresh": token}, format="json"
        )
        assert replay.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_rotated_token_is_usable(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """Rotation must hand back a token that works for the next refresh."""
        token = str(
            OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        )

        first = unauthenticated_client.post(self.url, {"refresh": token}, format="json")
        assert first.status_code == status.HTTP_200_OK

        second = unauthenticated_client.post(
            self.url,
            {"refresh": first.data["refresh"]},
            format="json",
        )
        assert second.status_code == status.HTTP_200_OK
        assert second.data["refresh"] != first.data["refresh"]


@pytest.mark.django_db
class TestOrgSwitchView:
    """Tests for POST /api/auth/switch-org/"""

    url = "/api/auth/switch-org/"

    def test_switch_org_success(self, admin_client, admin_user, org_b):
        # Give admin_user access to org_b
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["current_org"]["id"] == str(org_b.id)

    def test_switch_to_unauthorized_org(self, admin_client, org_b):
        # admin_user does NOT have a profile in org_b
        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_switch_org_missing_org_id(self, admin_client):
        response = admin_client.post(
            self.url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_org_returns_profile(self, admin_client, admin_user, org_b):
        """Switch org should return profile details."""
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "profile" in response.data
        assert response.data["profile"]["role"] == "USER"

    def test_switch_org_returns_tokens(self, admin_client, admin_user, org_b):
        """Switch org should return new access and refresh tokens."""
        Profile.objects.create(user=admin_user, org=org_b, role="ADMIN", is_active=True)
        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.data
        assert "refresh_token" in response.data

    def test_switch_org_unauthenticated(self, unauthenticated_client, org_a):
        """Unauthenticated user cannot switch org."""
        response = unauthenticated_client.post(
            self.url,
            {"org_id": str(org_a.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_switch_org_revokes_presented_refresh_token(
        self,
        admin_client,
        unauthenticated_client,
        admin_user,
        org_a,
        admin_profile,
        org_b,
    ):
        """Switching orgs must retire the refresh token the caller was holding.

        The client replaces its token pair on switch, so leaving the old refresh
        token live just means a stolen copy keeps working against the old org for
        the rest of its 14-day life.
        """
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        old_refresh = str(
            OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        )

        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id), "refresh": old_refresh},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        replay = unauthenticated_client.post(
            "/api/auth/refresh-token/", {"refresh": old_refresh}, format="json"
        )
        assert replay.status_code == status.HTTP_401_UNAUTHORIZED

    def test_switch_org_returned_refresh_token_is_usable(
        self,
        admin_client,
        unauthenticated_client,
        admin_user,
        org_a,
        admin_profile,
        org_b,
    ):
        """Retiring the old token must not strand the caller."""
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        old_refresh = str(
            OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        )

        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id), "refresh": old_refresh},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        follow_up = unauthenticated_client.post(
            "/api/auth/refresh-token/",
            {"refresh": response.data["refresh_token"]},
            format="json",
        )
        assert follow_up.status_code == status.HTTP_200_OK

    def test_switch_org_without_refresh_token_still_succeeds(
        self, admin_client, admin_user, org_b
    ):
        """Clients that omit `refresh` must keep working.

        Mobile builds already in the wild do not send it; failing their org
        switch would be worse than letting one token expire on its own.
        """
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        response = admin_client.post(self.url, {"org_id": str(org_b.id)}, format="json")
        assert response.status_code == status.HTTP_200_OK

    def test_switch_org_will_not_revoke_another_users_token(
        self, admin_client, unauthenticated_client, admin_user, org_a, org_b
    ):
        """A caller must not be able to retire someone else's session.

        Without an ownership check, `refresh` becomes a way to forcibly log out
        any user whose token you can observe.
        """
        Profile.objects.create(user=admin_user, org=org_b, role="USER", is_active=True)
        victim = User.objects.create(email="victim@example.com", password="unusable")
        victim_profile = Profile.objects.create(
            user=victim, org=org_a, role="USER", is_active=True
        )
        victim_refresh = str(
            OrgAwareRefreshToken.for_user_and_org(victim, org_a, victim_profile)
        )

        response = admin_client.post(
            self.url,
            {"org_id": str(org_b.id), "refresh": victim_refresh},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        still_valid = unauthenticated_client.post(
            "/api/auth/refresh-token/", {"refresh": victim_refresh}, format="json"
        )
        assert still_valid.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestProfileDetailView:
    """Tests for GET /api/auth/profile/"""

    url = "/api/auth/profile/"

    def test_profile_detail_authenticated(self, admin_client, admin_profile, org_a):
        """Authenticated user with org context gets profile details."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["role"] == "ADMIN"
        assert str(response.data["org"]["id"]) == str(org_a.id)

    def test_profile_detail_unauthenticated(self, unauthenticated_client):
        """The request is refused, and refused as a response, not an exception.

        The previous form caught ``(PermissionDenied, Exception)``, which is
        just ``Exception``: it would have passed on a typo, an import error or
        a 500. It asserted nothing about access control.
        """
        response = unauthenticated_client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Google OAuth Callback View tests (lines 52-137)
# ---------------------------------------------------------------------------


def _make_fake_id_token(
    email, picture="https://photo.example.com/pic.jpg", email_verified=True
):
    """Create a fake JWT-like ID token with the given email in the payload."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"email": email, "picture": picture, "email_verified": email_verified}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


@pytest.mark.django_db
class TestGoogleOAuthCallbackView:
    """Tests for POST /api/auth/google/callback/"""

    url = "/api/auth/google/callback/"

    def test_missing_parameters(self, unauthenticated_client):
        """Missing code/code_verifier/redirect_uri should return 400."""
        response = unauthenticated_client.post(
            self.url, {"code": "test"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing required parameters" in response.data["error"]

    @patch("common.views.auth_views.requests.post")
    def test_google_request_exception(self, mock_post, unauthenticated_client):
        """Network error communicating with Google should return 502."""
        import requests as req_lib

        mock_post.side_effect = req_lib.RequestException("Connection error")
        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_502_BAD_GATEWAY

    @patch("common.views.auth_views.requests.post")
    def test_google_token_exchange_failure(self, mock_post, unauthenticated_client):
        """Non-200 from Google token endpoint should return 400."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b'{"error_description": "invalid_grant"}'
        mock_response.json.return_value = {"error_description": "invalid_grant"}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "badcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid_grant" in response.data["error"]

    @patch("common.views.auth_views.requests.post")
    def test_missing_id_token(self, mock_post, unauthenticated_client):
        """Response without id_token should return 400."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at"}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No ID token" in response.data["error"]

    @patch("common.views.auth_views.requests.post")
    def test_invalid_id_token_format(self, mock_post, unauthenticated_client):
        """Malformed id_token should return 400."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": "not-a-jwt"}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid ID token" in response.data["error"]

    @patch("common.views.auth_views.requests.post")
    def test_no_email_in_token(self, mock_post, unauthenticated_client):
        """ID token without email should return 400."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Build a token without email
        payload = (
            base64.urlsafe_b64encode(json.dumps({"sub": "123"}).encode())
            .decode()
            .rstrip("=")
        )
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode())
            .decode()
            .rstrip("=")
        )
        sig = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
        mock_response.json.return_value = {"id_token": f"{header}.{payload}.{sig}"}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No email" in response.data["error"]

    @patch("common.views.auth_views.requests.post")
    def test_successful_oauth_new_user(self, mock_post, unauthenticated_client):
        """Successful OAuth should create user and return tokens."""
        fake_token = _make_fake_id_token("newgoogle@example.com")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": fake_token, "access_token": "at"}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.data
        assert "refresh_token" in response.data
        assert response.data["user"]["email"] == "newgoogle@example.com"
        # Verify user was created
        assert User.objects.filter(email="newgoogle@example.com").exists()

    @patch("common.views.auth_views.requests.post")
    def test_successful_oauth_existing_user(
        self, mock_post, unauthenticated_client, admin_user
    ):
        """Successful OAuth with existing user should return tokens without creating new user."""
        fake_token = _make_fake_id_token(admin_user.email)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id_token": fake_token, "access_token": "at"}
        mock_post.return_value = mock_response

        user_count_before = User.objects.count()
        response = unauthenticated_client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert User.objects.count() == user_count_before

    @patch("common.views.auth_views.requests.post")
    def test_token_exchange_failure_empty_content(
        self, mock_post, unauthenticated_client
    ):
        """Non-200 with empty content body should return generic error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b""
        mock_response.json.return_value = {}
        mock_post.return_value = mock_response

        response = unauthenticated_client.post(
            self.url,
            {
                "code": "badcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def _post_code(self, client):
        return client.post(
            self.url,
            {
                "code": "authcode",
                "code_verifier": "verifier",
                "redirect_uri": "http://localhost:3000/callback",
            },
            format="json",
        )

    @patch("common.views.auth_views.requests.post")
    def test_rejects_unverified_email(self, mock_post, unauthenticated_client):
        """An unverified Google email must not create or authenticate a user.

        Without this, a Google account holding an address the owner never
        proved control of is enough to become that address in this system.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id_token": _make_fake_id_token(
                "unverified@example.com", email_verified=False
            ),
            "access_token": "at",
        }
        mock_post.return_value = mock_response

        response = self._post_code(unauthenticated_client)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email="unverified@example.com").exists()

    @patch("common.views.auth_views.requests.post")
    def test_rejects_missing_email_verified_claim(
        self, mock_post, unauthenticated_client
    ):
        """A response with no email_verified claim must fail closed."""
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode())
            .decode()
            .rstrip("=")
        )
        payload = (
            base64.urlsafe_b64encode(
                json.dumps({"email": "noclaim@example.com"}).encode()
            )
            .decode()
            .rstrip("=")
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id_token": f"{header}.{payload}.sig",
            "access_token": "at",
        }
        mock_post.return_value = mock_response

        response = self._post_code(unauthenticated_client)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email="noclaim@example.com").exists()

    @patch("common.views.auth_views.requests.post")
    def test_rejects_deactivated_user(
        self, mock_post, unauthenticated_client, admin_user
    ):
        """A deactivated account must not be able to log back in via Google."""
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id_token": _make_fake_id_token(admin_user.email),
            "access_token": "at",
        }
        mock_post.return_value = mock_response

        response = self._post_code(unauthenticated_client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "access_token" not in response.data


# ---------------------------------------------------------------------------
# Google ID Token View tests (lines 173-232)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGoogleIdTokenView:
    """Tests for POST /api/auth/google/"""

    url = "/api/auth/google/"

    def test_missing_id_token(self, unauthenticated_client):
        """Missing idToken should return 400."""
        response = unauthenticated_client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing idToken" in response.data["error"]

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_invalid_token(self, mock_request_cls, mock_verify, unauthenticated_client):
        """Invalid Google token should return 400."""
        mock_verify.side_effect = ValueError("Invalid token")
        response = unauthenticated_client.post(
            self.url, {"idToken": "bad-token"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid token" in response.data["error"]

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_no_email_in_verified_token(
        self, mock_request_cls, mock_verify, unauthenticated_client
    ):
        """Token without email claim should return 400."""
        mock_verify.return_value = {"sub": "12345"}
        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-but-no-email"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No email" in response.data["error"]

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_successful_new_user(
        self, mock_request_cls, mock_verify, unauthenticated_client
    ):
        """Valid token with new email should create user and return JWT."""
        mock_verify.return_value = {
            "email": "mobileuser@example.com",
            "email_verified": True,
            "picture": "https://photo.example.com/pic.jpg",
        }
        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "JWTtoken" in response.data
        # Refresh token must be present so the mobile client can refresh the
        # 1-hour access token before the user picks an org (which is the only
        # other place that would mint a refresh token via OrgSwitchView).
        assert "refresh_token" in response.data
        assert response.data["refresh_token"]
        assert response.data["user"]["email"] == "mobileuser@example.com"
        assert User.objects.filter(email="mobileuser@example.com").exists()

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_successful_existing_user_with_orgs(
        self,
        mock_request_cls,
        mock_verify,
        unauthenticated_client,
        admin_user,
        admin_profile,
        org_a,
    ):
        """Existing user should get their organizations in response."""
        mock_verify.return_value = {
            "email": admin_user.email,
            "email_verified": True,
            "picture": "https://photo.example.com/pic.jpg",
        }
        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["organizations"]) == 1
        assert response.data["organizations"][0]["id"] == str(org_a.id)

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_rejects_unverified_email(
        self, mock_request_cls, mock_verify, unauthenticated_client
    ):
        """An unverified Google email must not authenticate on mobile either."""
        mock_verify.return_value = {
            "email": "unverified@example.com",
            "email_verified": False,
        }

        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email="unverified@example.com").exists()

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_rejects_missing_email_verified_claim(
        self, mock_request_cls, mock_verify, unauthenticated_client
    ):
        """A verified token with no email_verified claim must fail closed."""
        mock_verify.return_value = {"email": "noclaim@example.com"}

        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email="noclaim@example.com").exists()

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_rejects_deactivated_user(
        self, mock_request_cls, mock_verify, unauthenticated_client, admin_user
    ):
        """A deactivated account must not be able to log back in on mobile."""
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])
        mock_verify.return_value = {
            "email": admin_user.email,
            "email_verified": True,
        }

        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "JWTtoken" not in response.data


# ---------------------------------------------------------------------------
# Token refresh - User.DoesNotExist edge case (lines 503-504)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTokenRefreshUserNotFound:
    """Test token refresh when user has been deleted."""

    url = "/api/auth/refresh-token/"

    def test_refresh_deleted_user(
        self, unauthenticated_client, admin_user, org_a, admin_profile
    ):
        """Refreshing token for a deleted user should return 401."""
        token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
        token_str = str(token)
        # Delete the user
        admin_user.delete()
        response = unauthenticated_client.post(
            self.url,
            {"refresh": token_str},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "User not found" in response.data["error"]


@pytest.mark.django_db
class TestFlushExpiredRefreshTokens:
    """Tests for common.tasks.flush_expired_refresh_tokens."""

    def _outstanding(self, user, jti, expires_at):
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        return OutstandingToken.objects.create(
            user=user,
            jti=jti,
            token="not-a-real-token",
            created_at=timezone.now() - timedelta(days=30),
            expires_at=expires_at,
        )

    def test_deletes_expired_but_keeps_live_records(self, admin_user):
        """Rotation bookkeeping must not grow without bound."""
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        from common.tasks import flush_expired_refresh_tokens

        now = timezone.now()
        expired = self._outstanding(admin_user, "expired-jti", now - timedelta(days=1))
        live = self._outstanding(admin_user, "live-jti", now + timedelta(days=1))

        assert flush_expired_refresh_tokens() == 1

        assert not OutstandingToken.objects.filter(pk=expired.pk).exists()
        assert OutstandingToken.objects.filter(pk=live.pk).exists()

    def test_expired_blacklist_entry_is_cascaded(self, admin_user):
        """Deleting the outstanding row must take its blacklist entry with it."""
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        from common.tasks import flush_expired_refresh_tokens

        expired = self._outstanding(
            admin_user, "expired-jti", timezone.now() - timedelta(days=1)
        )
        BlacklistedToken.objects.create(token=expired)

        assert flush_expired_refresh_tokens() == 1
        assert not BlacklistedToken.objects.exists()
        assert not OutstandingToken.objects.exists()

    def test_returns_zero_when_nothing_expired(self, admin_user):
        self._outstanding(admin_user, "live-jti", timezone.now() + timedelta(days=1))
        from common.tasks import flush_expired_refresh_tokens

        assert flush_expired_refresh_tokens() == 0


@pytest.mark.django_db
class TestGoogleOrgListExcludesDeactivated:
    """A membership that `OrgSwitchView` will refuse must not be offered.

    The switch endpoint requires `is_active=True`, so a deactivated profile's
    org was an entry in the picker that answered 403 the moment it was chosen.
    """

    url = "/api/auth/google/"

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_deactivated_membership_is_not_listed(
        self,
        mock_request_cls,
        mock_verify,
        unauthenticated_client,
        admin_user,
        admin_profile,
        org_a,
        org_b,
    ):
        Profile.objects.create(
            user=admin_user, org=org_b, role="ADMIN", is_active=False
        )
        mock_verify.return_value = {
            "email": admin_user.email,
            "email_verified": True,
            "picture": "",
        }

        response = unauthenticated_client.post(
            self.url, {"idToken": "valid-token"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        returned = {o["id"] for o in response.data["organizations"]}
        assert returned == {str(org_a.id)}


@pytest.mark.django_db
class TestOrgPayloadCarriesCurrency:
    """Sign-in and org switch must say what currency an org keeps its books in.

    Both clients parse `default_currency` and `currency_symbol` off the org
    records these endpoints return, and no endpoint was sending either. The
    mobile deal and lead forms therefore defaulted every new record to USD,
    and the mobile dashboard printed a dollar sign over an org's totals
    whatever currency they were actually in.
    """

    @patch("google.oauth2.id_token.verify_oauth2_token")
    @patch("google.auth.transport.requests.Request")
    def test_google_sign_in_lists_the_currency(
        self,
        mock_request_cls,
        mock_verify,
        unauthenticated_client,
        admin_user,
        admin_profile,
        org_a,
    ):
        org_a.default_currency = "EUR"
        org_a.save(update_fields=["default_currency"])
        mock_verify.return_value = {
            "email": admin_user.email,
            "email_verified": True,
            "picture": "",
        }

        response = unauthenticated_client.post(
            "/api/auth/google/", {"idToken": "valid-token"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        listed = response.data["organizations"][0]
        assert listed["default_currency"] == "EUR"
        assert listed["currency_symbol"] == "€"

    def test_org_switch_returns_the_target_org_currency(
        self, admin_client, admin_user, org_b
    ):
        org_b.default_currency = "INR"
        org_b.save(update_fields=["default_currency"])
        Profile.objects.create(user=admin_user, org=org_b, role="ADMIN")

        response = admin_client.post(
            "/api/auth/switch-org/", {"org_id": str(org_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["current_org"]["default_currency"] == "INR"
        assert response.data["current_org"]["currency_symbol"] == "₹"

    def test_an_org_with_no_currency_set_falls_back_to_dollars(
        self, admin_client, admin_user, org_b
    ):
        """`default_currency` is nullable in practice, so the symbol lookup
        has to answer for an empty value rather than raise or return None."""
        org_b.default_currency = ""
        org_b.save(update_fields=["default_currency"])
        Profile.objects.create(user=admin_user, org=org_b, role="ADMIN")

        response = admin_client.post(
            "/api/auth/switch-org/", {"org_id": str(org_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["current_org"]["default_currency"] == "USD"
        assert response.data["current_org"]["currency_symbol"] == "$"
