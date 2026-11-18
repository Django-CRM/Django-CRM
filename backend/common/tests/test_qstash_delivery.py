import base64
import hashlib
import hmac
import json
import time
import secrets
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import MagicLinkToken
from common.qstash_delivery import publish_json, verify_signature


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signature(body, url, key="sig_test_current", **overrides):
    now = int(time.time())
    claims = {
        "iss": "Upstash",
        "sub": url,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
        "jti": "test-message",
        "body": _b64(hashlib.sha256(body).digest()),
    }
    claims.update(overrides)
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    message = f"{header}.{payload}".encode()
    digest = hmac.new(key.encode(), message, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(digest)}"


@pytest.mark.parametrize("key_name", ["QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY"])
def test_verify_signature_accepts_current_or_next_key(key_name):
    body = b'{"token_id":"abc"}'
    url = "https://godan.example/api/auth/magic-link/deliver/"
    key = "sig_test_key"
    with override_settings(
        QSTASH_CURRENT_SIGNING_KEY=key if key_name.endswith("CURRENT_SIGNING_KEY") else "",
        QSTASH_NEXT_SIGNING_KEY=key if key_name.endswith("NEXT_SIGNING_KEY") else "",
    ):
        assert verify_signature(_signature(body, url, key=key), body, url)


def test_verify_signature_rejects_body_url_and_expiry_mismatch():
    body = b'{"token_id":"abc"}'
    url = "https://godan.example/api/auth/magic-link/deliver/"
    key = "sig_test_current"
    with override_settings(
        QSTASH_CURRENT_SIGNING_KEY=key,
        QSTASH_NEXT_SIGNING_KEY="sig_test_next",
    ):
        signature = _signature(body, url, key=key)
        assert not verify_signature(signature, b'{"token_id":"changed"}', url)
        assert not verify_signature(signature, body, url + "wrong")
        expired = _signature(body, url, key=key, exp=int(time.time()) - 1)
        assert not verify_signature(expired, body, url)


def test_verify_signature_rejects_bad_signature_and_missing_keys():
    body = b"{}"
    url = "https://godan.example/callback/"
    with override_settings(QSTASH_CURRENT_SIGNING_KEY="", QSTASH_NEXT_SIGNING_KEY=""):
        assert not verify_signature("bad.jwt.value", body, url)
    with override_settings(QSTASH_CURRENT_SIGNING_KEY="sig_test_current", QSTASH_NEXT_SIGNING_KEY=""):
        assert not verify_signature("bad.jwt.value", body, url)


@patch("common.qstash_delivery.requests.post")
def test_publish_json_sends_opaque_json_and_retry_policy(mock_post):
    response = Mock()
    response.json.return_value = {"messageId": "qstash-message"}
    response.raise_for_status.return_value = None
    mock_post.return_value = response
    with override_settings(QSTASH_TOKEN="qstash-test-token"):
        result = publish_json(
            "https://godan.example/api/auth/magic-link/deliver/",
            {"token_id": "opaque-row-id"},
            retries=3,
        )
    assert result["messageId"] == "qstash-message"
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer qstash-test-token"
    assert kwargs["headers"]["Upstash-Retries"] == "3"
    assert kwargs["json"] == {"token_id": "opaque-row-id"}


@pytest.mark.django_db
def test_otp_request_publishes_only_opaque_id_and_encrypts_code():
    client = APIClient()
    encryption_key = Fernet.generate_key().decode()
    callback = "https://godan.example/api/auth/magic-link/deliver/"
    with (
        override_settings(
            QSTASH_TOKEN="qstash-test-token",
            QSTASH_CALLBACK_URL=callback,
            QSTASH_CURRENT_SIGNING_KEY="sig_test_current",
            QSTASH_NEXT_SIGNING_KEY="sig_test_next",
            MAGIC_LINK_ENCRYPTION_KEY=encryption_key,
        ),
        patch("common.qstash_delivery.publish_json") as publish,
        patch("common.tasks.send_magic_link_email") as inline_send,
    ):
        response = client.post(
            "/api/auth/magic-link/request/",
            {"email": "queued@example.com", "delivery": "code"},
            format="json",
        )

    assert response.status_code == 200
    token = MagicLinkToken.objects.get(email="queued@example.com")
    assert token.encrypted_code
    assert token.code_hash
    assert token.email_sent_at is None
    publish.assert_called_once_with(
        callback,
        {"token_id": str(token.id)},
        retries=3,
    )
    inline_send.apply.assert_not_called()


@pytest.mark.django_db
def test_signed_callback_delivers_once_and_acknowledges_duplicate():
    email_token = MagicLinkToken.objects.create(
        email="callback@example.com",
        token=secrets.token_hex(32),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    callback = "https://godan.example/api/auth/magic-link/deliver/"
    body = json.dumps({"token_id": str(email_token.id)}).encode()
    signature = _signature(body, callback)
    client = APIClient()

    with (
        override_settings(
            QSTASH_CALLBACK_URL=callback,
            QSTASH_CURRENT_SIGNING_KEY="sig_test_current",
            QSTASH_NEXT_SIGNING_KEY="sig_test_next",
        ),
        patch("common.tasks.send_magic_link_email") as send_task,
    ):
        send_task.apply.return_value.result = True
        first = client.generic(
            "POST",
            "/api/auth/magic-link/deliver/",
            body,
            content_type="application/json",
            HTTP_UPSTASH_SIGNATURE=signature,
        )
        second = client.generic(
            "POST",
            "/api/auth/magic-link/deliver/",
            body,
            content_type="application/json",
            HTTP_UPSTASH_SIGNATURE=signature,
        )

    assert first.status_code == 204
    assert second.status_code == 204
    assert send_task.apply.call_count == 1
    email_token.refresh_from_db()
    assert email_token.email_sent_at is not None


@pytest.mark.django_db
def test_signed_callback_rejects_invalid_signature():
    client = APIClient()
    with override_settings(
        QSTASH_CALLBACK_URL="https://godan.example/api/auth/magic-link/deliver/",
        QSTASH_CURRENT_SIGNING_KEY="sig_test_current",
        QSTASH_NEXT_SIGNING_KEY="sig_test_next",
    ):
        response = client.generic(
            "POST",
            "/api/auth/magic-link/deliver/",
            b'{"token_id":"00000000-0000-0000-0000-000000000000"}',
            content_type="application/json",
            HTTP_UPSTASH_SIGNATURE="not-a-valid-jwt",
        )
    assert response.status_code == 401
