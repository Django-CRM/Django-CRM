"""Public signup is staged until a separate, single-use email activation code is redeemed."""

from unittest.mock import patch

import pytest
from rest_framework import status

from common.models import AccountSignupChallenge, Org, Profile, User


@pytest.mark.django_db
class TestAccountSignup:
    request_url = "/api/auth/signup/request-code/"
    verify_url = "/api/auth/signup/verify-code/"
    payload = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada.signup@example.com",
        "phone": "+254712345678",
        "country": "KE",
        "organization_name": "Analytical Engines Ltd",
        "job_title": "Founder",
        "industry": "Technology",
        "employee_count": "2–5",
        "website": "https://analytical.example",
        "business_description": "Builds analytical software.",
        "password": "Godan-Signup-2026!Secure",
        "confirm_password": "Godan-Signup-2026!Secure",
        "accept_terms": True,
        "accept_privacy": True,
        "marketing_consent": False,
    }

    def test_signup_requires_both_agreements(self, unauthenticated_client):
        payload = {**self.payload, "accept_privacy": False}
        with patch("common.views.registration_views._send_activation_email") as send:
            response = unauthenticated_client.post(self.request_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not AccountSignupChallenge.objects.exists()
        send.assert_not_called()

    def test_account_and_workspace_are_created_only_after_email_activation(
        self, unauthenticated_client
    ):
        sent = {}

        def capture_code(challenge, code):
            sent["challenge"] = challenge
            sent["code"] = code
            return True

        with patch(
            "common.views.registration_views._send_activation_email",
            side_effect=capture_code,
        ):
            response = unauthenticated_client.post(
                self.request_url, self.payload, format="json"
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["email"] == self.payload["email"]
        challenge = AccountSignupChallenge.objects.get(
            id=response.data["challenge_id"]
        )
        assert challenge.password_hash != self.payload["password"]
        assert challenge.code_hash
        assert challenge.terms_version
        assert challenge.privacy_version
        assert challenge.terms_accepted_at
        assert challenge.privacy_accepted_at
        assert challenge.marketing_consent is False
        assert not User.objects.filter(email=self.payload["email"]).exists()
        assert not Org.objects.filter(name=self.payload["organization_name"]).exists()

        activated = unauthenticated_client.post(
            self.verify_url,
            {"challenge_id": str(challenge.id), "code": sent["code"]},
            format="json",
        )
        assert activated.status_code == status.HTTP_201_CREATED
        assert activated.data["access_token"]
        user = User.objects.get(email=self.payload["email"])
        org = Org.objects.get(name=self.payload["organization_name"])
        profile = Profile.objects.get(user=user, org=org)
        assert user.is_active is True
        assert user.name == "Ada Lovelace"
        assert profile.phone == self.payload["phone"]
        assert profile.role == "ADMIN"
        assert profile.job_title == "Founder"
        assert org.industry == "Technology"
        assert org.employee_count == "2–5"
        assert org.website == "https://analytical.example"
        challenge.refresh_from_db()
        assert challenge.is_used is True

        replay = unauthenticated_client.post(
            self.verify_url,
            {"challenge_id": str(challenge.id), "code": sent["code"]},
            format="json",
        )
        assert replay.status_code == status.HTTP_400_BAD_REQUEST

    def test_wrong_activation_code_is_limited(self, unauthenticated_client):
        with patch("common.views.registration_views._send_activation_email", return_value=True):
            response = unauthenticated_client.post(
                self.request_url, self.payload, format="json"
            )
        challenge_id = response.data["challenge_id"]
        for _ in range(5):
            wrong = unauthenticated_client.post(
                self.verify_url,
                {"challenge_id": challenge_id, "code": "000000"},
                format="json",
            )
            assert wrong.status_code == status.HTTP_400_BAD_REQUEST
        locked = unauthenticated_client.post(
            self.verify_url,
            {"challenge_id": challenge_id, "code": "123456"},
            format="json",
        )
        assert locked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert not User.objects.filter(email=self.payload["email"]).exists()
