"""Phone pushes through Firebase Cloud Messaging's HTTP v1 API.

`FCM_SERVICE_ACCOUNT_FILE` names a Firebase service-account JSON file. Empty or
unreadable, push is off: `push_configured()` answers False and callers
schedule nothing. Only a phone app built with the same Firebase project
receives the pushes, which is why a self-hosted server cannot reach the Play
Store build (docs/features/lead-alerts.md).

A device token is a secret of the phone it belongs to, and the service account
holds a private key, so neither is ever logged, and a retry carries device row
ids rather than tokens. Sentry gets neither: `common.sentry_scrub` drops the
local variables of any stack through this module.
"""

import json
import logging

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from common.models import DeviceToken, Profile
from common.tasks import clear_rls_context, set_rls_context

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 600
_REQUIRED_KEYS = ("project_id", "client_email", "private_key")
# A member's newest phones in an org get the pushes, and registering another
# drops the rest (`common.views.device_views`).
DEVICES_PER_MEMBER = 5


def _service_account():
    """The service account's JSON as a dict, or None when push is off."""
    path = settings.FCM_SERVICE_ACCOUNT_FILE
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        return None
    if not all(isinstance(info.get(k), str) and info[k] for k in _REQUIRED_KEYS):
        return None
    return info


def push_configured() -> bool:
    return _service_account() is not None


def _session(info):
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=[SCOPE]
    )
    return AuthorizedSession(credentials)


def _message(token, title, body, link, verb):
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {"link": link, "verb": verb},
            "android": {"priority": "high"},
        }
    }


def _error_of(response):
    """`(status, error codes, fields named as invalid)` from an FCM error reply."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return "", set(), set()
    codes, fields = set(), set()
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if detail.get("errorCode"):
            codes.add(detail["errorCode"])
        for violation in detail.get("fieldViolations") or []:
            if isinstance(violation, dict) and violation.get("field"):
                fields.add(violation["field"])
    return str(error.get("status") or ""), codes, fields


def _token_is_dead(response):
    """FCM says this token will never work again: the app was uninstalled or
    the token rotated (UNREGISTERED), or it is not a token at all. A 404 or a
    400 about anything else (a wrong project, a bad payload) is our problem
    and must not wipe every phone's token."""
    status, codes, fields = _error_of(response)
    if response.status_code == 404:
        return "UNREGISTERED" in codes
    if response.status_code == 400:
        return "INVALID_ARGUMENT" in (codes | {status}) and "message.token" in fields
    return False


def _retry_delay(response, retries):
    """Seconds before the next try: FCM's Retry-After when it sends one, else
    30 s doubling per retry, never more than 10 minutes."""
    header = response.headers.get("Retry-After") if response is not None else None
    if header is not None and str(header).isdigit():
        return min(int(header), RETRY_MAX_SECONDS)
    return min(RETRY_BASE_SECONDS * 2**retries, RETRY_MAX_SECONDS)


@shared_task(bind=True, max_retries=MAX_RETRIES)
def send_push(self, profile_id, title, body, link, verb="", devices=None):
    """Push one notification to each of the profile's newest phones in its org.

    `devices` (row ids) is set only by a retry, to the phones that failed.
    Sends nothing to an inactive member or user, or one who switched push off.
    A phone that has not registered for a refresh token's lifetime is deleted
    unsent: it was signed out, perhaps while offline, or registered by
    someone who has since lost the account.
    """
    info = _service_account()
    if info is None:
        return
    profile = (
        Profile.objects.filter(pk=profile_id, is_active=True, user__is_active=True)
        .only("id", "org_id", "user_id", "lead_alerts_push")
        .first()
    )
    if profile is None or not profile.lead_alerts_push:
        return
    set_rls_context(profile.org_id)
    try:
        phones = DeviceToken.objects.filter(
            org_id=profile.org_id, user_id=profile.user_id
        )
        signed_out = timezone.now() - settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
        phones.filter(last_seen_at__lt=signed_out).delete()
        if devices is not None:
            phones = phones.filter(pk__in=devices)
        phones = list(phones.order_by("-last_seen_at")[:DEVICES_PER_MEMBER])
        if not phones:
            return
        failed, wait = _deliver(
            info, phones, (title, body, link, verb), self.request.retries
        )
    finally:
        clear_rls_context()
    if not failed:
        return
    if self.request.retries >= MAX_RETRIES:
        logger.warning(
            "Push to profile %s gave up on %d device(s) after %d retries",
            profile_id,
            len(failed),
            MAX_RETRIES,
        )
        return
    raise self.retry(
        kwargs={"verb": verb, "devices": [str(pk) for pk in failed]},
        countdown=wait,
    )


def _deliver(info, phones, content, retries):
    """Post one message per phone. Returns the ids worth another try and how
    long to wait before it."""
    try:
        session = _session(info)
    except (GoogleAuthError, ValueError):
        logger.warning("Push service-account credentials do not load")
        return [], 0
    url = SEND_URL.format(project=info["project_id"])
    failed, wait = [], 0
    for phone in phones:
        try:
            response = session.post(
                url, json=_message(phone.token, *content), timeout=TIMEOUT_SECONDS
            )
        except (TransportError, requests.RequestException) as exc:
            logger.warning(
                "Push to device %s could not connect: %s", phone.pk, type(exc).__name__
            )
            failed.append(phone.pk)
            wait = max(wait, _retry_delay(None, retries))
            continue
        except GoogleAuthError:
            logger.warning("Google refused the push service-account credentials")
            return [], 0
        code = response.status_code
        if 200 <= code < 300:
            continue
        if code == 429 or code >= 500:
            failed.append(phone.pk)
            wait = max(wait, _retry_delay(response, retries))
            continue
        if _token_is_dead(response):
            DeviceToken.objects.filter(pk=phone.pk, org_id=phone.org_id).delete()
            continue
        status, codes, _ = _error_of(response)
        logger.warning(
            "FCM refused a push to device %s: HTTP %s %s %s",
            phone.pk,
            code,
            status,
            ",".join(sorted(codes)),
        )
    return failed, wait
