"""SNS message verification.

AWS SNS posts JSON like:

  {
    "Type": "Notification",            // or "SubscriptionConfirmation"
    "MessageId": "...",
    "TopicArn": "arn:aws:sns:...:...:...",
    "Subject": "Amazon SES Email Receipt",
    "Message": "<raw-email-or-json>",
    "Timestamp": "2026-05-09T12:00:00.000Z",
    "SignatureVersion": "1",           // or "2" for SHA-256
    "Signature": "<base64>",
    "SigningCertURL": "https://sns.<region>.amazonaws.com/SimpleNotificationService-XXXX.pem",
    "SubscribeURL": "https://sns...",  // SubscriptionConfirmation only
    "Token": "..."                     // SubscriptionConfirmation only
  }

We verify the signature against the AWS-published certificate so that an
attacker can't post arbitrary JSON to our public webhook URL. The cert URL
is pinned to the `sns.<region>.amazonaws.com` host family.

Three controls keep that pinning honest (CodeQL `py/full-ssrf` #27/#28):

* **No redirects.** The host allow-list is applied to the URL we are about to
  fetch; following a 3xx would land us somewhere never checked. `_OPENER`
  surfaces the redirect as an error instead of chasing it.
* **Strict TLS.** The fetch uses an explicit default-verifying SSL context, so
  transport trust is anchored to the CA store and can't silently degrade. This,
  not the signing cert's own issuer chain, is what proves the PEM really
  came from AWS.
* **Certificate validation.** The fetched cert must be inside its validity
  window and carry a subject CN in the same SNS host family, so a stale or
  foreign cert can't be used to satisfy the signature check.

Note on chain validation: we deliberately do *not* run X.509 path validation on
the signing certificate. AWS serves a bare leaf from these URLs with no bundled
intermediates, so `build_server_verifier`/`build_client_verifier` would fail on
genuine traffic (and the leaf's EKU/SAN shape is not contractual). Transport
trust plus the CN and validity checks are the durable controls here.

Reference: https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message.html
"""

from __future__ import annotations

import base64
import logging
import re
import ssl
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse to follow redirects.

    Returning `None` from `redirect_request` makes urllib fall through to the
    default error handler, which raises `HTTPError` carrying the 3xx. We want
    that: the host allow-list only ever saw the pre-redirect URL, so a followed
    redirect is an unvalidated fetch.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Shared opener for every outbound call in this module. Built once: the SSL
# context loads the CA store, which is not free.
_OPENER = build_opener(
    HTTPSHandler(context=ssl.create_default_context()),
    _NoRedirectHandler,
)

# AWS regional SNS hosts; we accept any of these but reject any other host as
# a defense against a forged SigningCertURL pointing at attacker-controlled storage.
#
# Always apply this with `fullmatch`, never `match`: `$` also matches just
# before a trailing newline, so `match` would accept "sns.amazonaws.com\n".
# The `^`/`$` anchors are redundant under `fullmatch` but are kept so the
# pattern stays anchored if anyone switches the call back.
_SIGNING_HOST_RE = re.compile(r"^sns(?:\.[a-z0-9-]+)?\.amazonaws\.com(?:\.cn)?$")


# Headers required for the canonical signing string for each message type.
# See AWS docs link above.
_NOTIFICATION_KEYS = (
    "Message",
    "MessageId",
    "Subject",
    "Timestamp",
    "TopicArn",
    "Type",
)
_SUBSCRIPTION_KEYS = (
    "Message",
    "MessageId",
    "SubscribeURL",
    "Timestamp",
    "Token",
    "TopicArn",
    "Type",
)

# An SNS topic ARN, `arn:aws:sns:<region>:<account id>:<topic name>`, in the
# commercial `aws` partition only: SES email receiving, the one producer the
# inbound webhook serves, does not run in the China or GovCloud partitions. A
# standard topic name is 1-256 letters, digits, hyphens and underscores; SES
# cannot publish to a FIFO topic, so a `.fifo` name is refused too. Applied
# with `fullmatch` for the trailing-newline reason given above.
_TOPIC_ARN_RE = re.compile(
    r"arn:aws:sns:[a-z]{2}(?:-[a-z]+)+-\d{1,2}:(\d{12}):[A-Za-z0-9_-]{1,256}"
)


def topic_account_id(arn) -> str | None:
    """The 12-digit AWS account id in a well-formed SNS topic ARN, else None.

    Anything that is not a string, or not exactly the shape above, is None, so
    a caller comparing the result against a set of allowed ids refuses it.
    """
    if not isinstance(arn, str):
        return None
    match = _TOPIC_ARN_RE.fullmatch(arn)
    return match.group(1) if match else None


class SNSVerificationError(Exception):
    """Raised when an SNS message fails signature verification."""


def _build_string_to_sign(payload: dict, keys: Iterable[str]) -> bytes:
    """Build the AWS-defined canonical string for signing.

    Format: for each key in `keys` (alphabetical order), if the key is present
    in the payload, append "<key>\\n<value>\\n".
    """
    pieces: list[str] = []
    for key in keys:
        if key in payload and payload[key] is not None:
            pieces.append(key)
            pieces.append("\n")
            pieces.append(str(payload[key]))
            pieces.append("\n")
    return "".join(pieces).encode("utf-8")


def _fetch_signing_cert(url: str, *, timeout: float = 5.0) -> bytes:
    """Fetch the SNS signing certificate, with hostname pinning."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SNSVerificationError(f"SigningCertURL must be https: {url!r}")
    if not _SIGNING_HOST_RE.fullmatch(parsed.hostname or ""):
        raise SNSVerificationError(
            f"SigningCertURL host {parsed.hostname!r} not in the AWS SNS family"
        )
    if not parsed.path.endswith(".pem"):
        raise SNSVerificationError(f"SigningCertURL must end in .pem: {url!r}")
    # `_OPENER` refuses redirects, so the host checked above is the host we talk
    # to. The pinning cannot be sidestepped by a 3xx.
    with _OPENER.open(url, timeout=timeout) as response:  # noqa: S310  # host pinned
        return response.read()


def _validate_signing_cert(cert) -> None:
    """Reject a signing cert that is out of its validity window or not AWS's.

    Without this, any PEM reachable from the pinned host family would be
    trusted to supply the public key that gates the whole webhook.
    """
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise SNSVerificationError(
            "Signing certificate is not currently valid "
            f"({cert.not_valid_before_utc.isoformat()} .. "
            f"{cert.not_valid_after_utc.isoformat()})"
        )
    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not common_names:
        raise SNSVerificationError("Signing certificate has no subject common name")
    common_name = common_names[0].value
    if not isinstance(common_name, str) or not _SIGNING_HOST_RE.fullmatch(common_name):
        raise SNSVerificationError(
            f"Signing certificate common name {common_name!r} "
            "is not in the AWS SNS family"
        )


def _hash_alg_for_version(version: str) -> hashes.HashAlgorithm:
    if version == "1":
        return hashes.SHA1()
    if version == "2":
        return hashes.SHA256()
    raise SNSVerificationError(f"Unsupported SignatureVersion: {version!r}")


def verify_sns_message(
    payload: dict,
    *,
    fetch_cert=_fetch_signing_cert,
) -> None:
    """Verify the signature on an SNS payload.

    Raises `SNSVerificationError` if anything looks wrong. The `fetch_cert`
    seam exists so tests can substitute a deterministic cert without going to
    the network.
    """
    msg_type = payload.get("Type")
    if msg_type not in {
        "Notification",
        "SubscriptionConfirmation",
        "UnsubscribeConfirmation",
    }:
        raise SNSVerificationError(f"Unknown SNS Type: {msg_type!r}")

    signature_b64 = payload.get("Signature")
    cert_url = payload.get("SigningCertURL")
    sig_version = str(payload.get("SignatureVersion", ""))
    if not signature_b64 or not cert_url or not sig_version:
        raise SNSVerificationError("Missing signature fields")

    try:
        signature = base64.b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise SNSVerificationError(f"Bad base64 signature: {exc}") from exc

    cert_bytes = fetch_cert(cert_url)
    try:
        cert = load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        # Malformed PEM is an attacker-shaped input, not a server fault. A 403
        # from the caller beats a 500.
        raise SNSVerificationError(
            f"Signing certificate is not valid PEM: {exc}"
        ) from exc
    _validate_signing_cert(cert)
    public_key = cert.public_key()

    keys = _NOTIFICATION_KEYS if msg_type == "Notification" else _SUBSCRIPTION_KEYS
    string_to_sign = _build_string_to_sign(payload, keys)
    hash_alg = _hash_alg_for_version(sig_version)

    try:
        # SNS signs with PKCS#1 v1.5 RSA.
        public_key.verify(
            signature,
            string_to_sign,
            padding.PKCS1v15(),
            hash_alg,
        )
    except InvalidSignature as exc:
        raise SNSVerificationError("Signature does not match") from exc
    except Exception as exc:  # pragma: no cover. Non-RSA cert is impossible in practice
        raise SNSVerificationError(f"Verification failed: {exc}") from exc


def confirm_subscription(payload: dict, *, fetch=None, timeout: float = 5.0) -> None:
    """If the payload is a SubscriptionConfirmation, hit the SubscribeURL once.

    SNS will GET this URL itself when the topic is wired through the AWS console;
    confirming programmatically is convenient when an admin pastes the webhook
    URL directly into the SES Receipt Rule and lets SNS auto-subscribe.
    """
    if payload.get("Type") != "SubscriptionConfirmation":
        return
    url = payload.get("SubscribeURL")
    if not url:
        raise SNSVerificationError("SubscriptionConfirmation missing SubscribeURL")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _SIGNING_HOST_RE.fullmatch(
        parsed.hostname or ""
    ):
        raise SNSVerificationError(f"SubscribeURL not on AWS SNS host: {url!r}")
    # Resolved at call time, not bound as a default, so the module-level opener
    # stays the single place redirect policy is decided.
    fetch = fetch or _OPENER.open
    try:
        with fetch(url, timeout=timeout) as response:  # noqa: S310  # host pinned
            response.read()
    except Exception:
        logger.exception("Failed to confirm SNS subscription at %s", url)
        raise
