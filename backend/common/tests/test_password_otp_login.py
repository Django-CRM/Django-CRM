"""Password sign-in must prove inbox control with a separate one-time code."""

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from unittest.mock import MagicMock, patch

from common.models import MagicLinkToken, User


@pytest.fixture(autouse=True)
def _fresh_throttle_cache():
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
class TestPasswordOtpLogin:
    request_url = "/api/auth/password/request-code/"
    verify_url = "/api/auth/password/verify-code/"

    def _request_code(self, client, user, password="Correct-Horse-Battery-42!"):
        user.set_password(password)
        user.save(update_fields=["password"])
        task_result = MagicMock()
        task_result.result = True
        with patch("common.tasks.send_magic_link_email") as task:
            task.apply.return_value = task_result
            response = client.post(
                self.request_url,
                {"email": user.email, "password": password},
                format="json",
            )
            raw_code = task.apply.call_args.kwargs["kwargs"]["raw_code"]
        return response, raw_code

    def test_correct_password_sends_code_not_a_login_link(
        self, unauthenticated_client, admin_user
    ):
        response, raw_code = self._request_code(unauthenticated_client, admin_user)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["challenge_id"]
        token = MagicLinkToken.objects.get(id=response.data["challenge_id"])
        assert token.delivery == MagicLinkToken.DELIVERY_CODE
        assert token.purpose == MagicLinkToken.PURPOSE_PASSWORD_LOGIN
        assert token.code_hash
        assert token.token not in response.data.values()
        assert len(raw_code) == 6 and raw_code.isdigit()

    def test_wrong_password_does_not_create_or_send_otp(
        self, unauthenticated_client, admin_user
    ):
        admin_user.set_password("Correct-Horse-Battery-42!")
        admin_user.save(update_fields=["password"])
        with patch("common.tasks.send_magic_link_email") as task:
            response = unauthenticated_client.post(
                self.request_url,
                {"email": admin_user.email, "password": "wrong-password"},
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not MagicLinkToken.objects.filter(
            email=admin_user.email,
            purpose=MagicLinkToken.PURPOSE_PASSWORD_LOGIN,
        ).exists()
        task.apply.assert_not_called()

    def test_code_signs_in_once_and_cannot_be_replayed(
        self, unauthenticated_client, admin_user
    ):
        request_response, raw_code = self._request_code(
            unauthenticated_client, admin_user
        )
        payload = {"email": admin_user.email, "code": raw_code}
        response = unauthenticated_client.post(self.verify_url, payload, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["access_token"]
        assert response.data["refresh_token"]
        assert response.data["user"]["email"] == admin_user.email

        replay = unauthenticated_client.post(self.verify_url, payload, format="json")
        assert replay.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_login_code_cannot_be_used_by_passwordless_mobile_flow(
        self, unauthenticated_client, admin_user
    ):
        _, raw_code = self._request_code(unauthenticated_client, admin_user)
        response = unauthenticated_client.post(
            "/api/auth/magic-link/verify-code/",
            {"email": admin_user.email, "code": raw_code},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert User.objects.filter(pk=admin_user.pk).exists()

    def test_inactive_account_cannot_request_code(
        self, unauthenticated_client, admin_user
    ):
        admin_user.set_password("Correct-Horse-Battery-42!")
        admin_user.is_active = False
        admin_user.save(update_fields=["password", "is_active"])
        response = unauthenticated_client.post(
            self.request_url,
            {"email": admin_user.email, "password": "Correct-Horse-Battery-42!"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not MagicLinkToken.objects.filter(
            email=admin_user.email,
            purpose=MagicLinkToken.PURPOSE_PASSWORD_LOGIN,
        ).exists()
