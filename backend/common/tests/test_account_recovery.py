"""Security regression tests for recovery OTP separation and password resets."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status

from common.models import MagicLinkToken


@pytest.fixture(autouse=True)
def clear_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@override_settings(
    QSTASH_TOKEN="",
    QSTASH_CALLBACK_URL="",
    QSTASH_CURRENT_SIGNING_KEY="",
    QSTASH_NEXT_SIGNING_KEY="",
    MAGIC_LINK_ENCRYPTION_KEY="",
)
class TestAccountRecovery:
    request_url = "/api/auth/recovery/request-code/"
    validate_url = "/api/auth/recovery/validate-code/"
    reset_url = "/api/auth/recovery/reset-password/"

    def request_code(self, client, user, flow="reset"):
        with patch("common.tasks.send_magic_link_email") as task:
            task.apply.return_value.result = True
            response = client.post(
                self.request_url,
                {"email": user.email, "flow": flow},
                format="json",
            )
            raw_code = task.apply.call_args.kwargs["kwargs"]["raw_code"]
        return response, raw_code

    def test_password_reset_code_is_hashed_and_validates(
        self, unauthenticated_client, admin_user
    ):
        response, code = self.request_code(unauthenticated_client, admin_user)
        assert response.status_code == status.HTTP_200_OK
        challenge_id = response.data["challenge_id"]
        token = MagicLinkToken.objects.get(pk=challenge_id)
        assert token.purpose == MagicLinkToken.PURPOSE_PASSWORD_RESET
        assert token.code_hash and code not in token.code_hash
        valid = unauthenticated_client.post(
            self.validate_url,
            {"challenge_id": challenge_id, "code": code},
            format="json",
        )
        assert valid.status_code == status.HTTP_200_OK
        assert valid.data["valid"] is True
        assert not MagicLinkToken.objects.get(pk=challenge_id).is_used

    def test_wrong_code_is_reported_and_attempt_counted(
        self, unauthenticated_client, admin_user
    ):
        response, _ = self.request_code(unauthenticated_client, admin_user)
        token = MagicLinkToken.objects.get(pk=response.data["challenge_id"])
        wrong_code = "000000" if code != "000000" else "000001"
        wrong = unauthenticated_client.post(
            self.validate_url,
            {"challenge_id": str(token.pk), "code": wrong_code},
            format="json",
        )
        assert wrong.status_code == status.HTTP_400_BAD_REQUEST
        assert wrong.data["status"] == "invalid"
        token.refresh_from_db()
        assert token.attempts == 1

    def test_reset_consumes_code_and_changes_password(
        self, unauthenticated_client, admin_user
    ):
        response, code = self.request_code(unauthenticated_client, admin_user)
        challenge_id = response.data["challenge_id"]
        reset = unauthenticated_client.post(
            self.reset_url,
            {
                "challenge_id": challenge_id,
                "code": code,
                "password": "A-New-Strong-Password-928!",
                "confirm_password": "A-New-Strong-Password-928!",
            },
            format="json",
        )
        assert reset.status_code == status.HTTP_200_OK
        admin_user.refresh_from_db()
        assert admin_user.check_password("A-New-Strong-Password-928!")
        token = MagicLinkToken.objects.get(pk=challenge_id)
        assert token.is_used

    def test_recovery_login_otp_cannot_reset_password(
        self, unauthenticated_client, admin_user
    ):
        response, code = self.request_code(
            unauthenticated_client, admin_user, flow="login"
        )
        assert response.status_code == status.HTTP_200_OK
        token = MagicLinkToken.objects.get(pk=response.data["challenge_id"])
        assert token.purpose == MagicLinkToken.PURPOSE_RECOVERY_LOGIN
        wrong_flow = unauthenticated_client.post(
            self.validate_url,
            {"challenge_id": str(token.pk), "code": code},
            format="json",
        )
        assert wrong_flow.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_email_does_not_reveal_account_existence(
        self, unauthenticated_client
    ):
        response = unauthenticated_client.post(
            self.request_url,
            {"email": "nobody@example.com", "flow": "reset"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["challenge_id"]
        decoy = MagicLinkToken.objects.get(pk=response.data["challenge_id"])
        assert not decoy.is_used
        assert "If an active account" in response.data["message"]
