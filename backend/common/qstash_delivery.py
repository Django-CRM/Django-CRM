"""Small, dependency-free QStash transport and request-signature verification.

QStash signs delivery JWTs with HS256. Keep the raw request body for
verification; never verify a re-serialized DRF payload.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

QSTASH_API_URL = os.environ.get("QSTASH_URL", "https://qstash.upstash.io").rstrip("/")


class QStashError(Exception):
    """QStash publish or signature verification failed."""


def publish_json(destination, body, retries=3):
    token = getattr(settings, "QSTASH_TOKEN", "")
    if not token:
        raise QStashError("QSTASH_TOKEN is not configured")
    url = f"{QSTASH_API_URL}/v2/publish/{quote(destination, safe='')}"
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Upstash-Retries": str(retries),
            },
            json=body,
            timeout=8,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        # Do not log request bodies, tokens, or destination payloads.
        logger.exception("QStash publish failed")
        raise QStashError("Unable to publish QStash message") from exc


def _b64url_decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_signature(signature, raw_body, expected_url):
    """Verify QStash HS256 JWT, claims, destination URL, and raw-body hash."""
    if not signature or not expected_url:
        return False

    current = getattr(settings, "QSTASH_CURRENT_SIGNING_KEY", "")
    following = getattr(settings, "QSTASH_NEXT_SIGNING_KEY", "")
    if not current and not following:
        return False

    try:
        parts = signature.split(".")
        if len(parts) != 3:
            return False
        encoded_header, encoded_payload, encoded_signature = parts
        header = json.loads(_b64url_decode(encoded_header))
        claims = json.loads(_b64url_decode(encoded_payload))
        if header.get("alg") != "HS256":
            return False

        signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
        supplied = _b64url_decode(encoded_signature)
        valid = False
        for key in (current, following):
            if not key:
                continue
            expected = hmac.new(key.encode("utf-8"), signed, hashlib.sha256).digest()
            valid = hmac.compare_digest(expected, supplied) or valid
        if not valid:
            return False

        now = time.time()
        if claims.get("iss") != "Upstash":
            return False
        if str(claims.get("sub", "")).rstrip("/") != expected_url.rstrip("/"):
            return False
        if float(claims.get("exp", 0)) <= now:
            return False
        if float(claims.get("nbf", 0)) > now:
            return False

        body_digest = hashlib.sha256(raw_body).digest()
        expected_body = base64.urlsafe_b64encode(body_digest).decode("ascii").rstrip("=")
        supplied_body = str(claims.get("body", "")).rstrip("=")
        return hmac.compare_digest(expected_body, supplied_body)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return False
