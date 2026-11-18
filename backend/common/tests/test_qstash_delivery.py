import base64
import hashlib
import hmac
import json
import time
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

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
