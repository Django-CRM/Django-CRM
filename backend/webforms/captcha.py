"""Cloudflare Turnstile verification for public web form submissions.

Optional per form and off by default. `requests` is already a dependency, so
this adds no package, and it makes no network call unless an org has configured
both a provider and a secret.

FAILS CLOSED. Every path that cannot positively confirm a good token returns
False: a timeout, a connection error, a malformed response, a missing secret, a
missing token. Failing open would mean an attacker's first step is to make
Cloudflare unreachable.
"""

import logging

import requests

from webforms.models import WebForm

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
VERIFY_TIMEOUT_SECONDS = 5


def verify(form, token, remote_ip):
    """Whether this submission's captcha token is good.

    Returns True immediately when the form has no captcha configured, which is
    the default. The secret is sent to Cloudflare and never written to a log
    line, including on the failure paths.
    """
    if form.captcha_provider != WebForm.CAPTCHA_TURNSTILE:
        return True

    if not form.captcha_secret:
        logger.warning(
            "Web form %s has a captcha provider set but no secret; failing closed.",
            form.id,
        )
        return False

    if not token:
        return False

    payload = {"secret": form.captcha_secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL, data=payload, timeout=VERIFY_TIMEOUT_SECONDS
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        # `exc` here is a requests exception or a JSON decode error, neither of
        # which carries the request body, so the secret cannot reach the log
        # through it.
        logger.warning(
            "Turnstile verification failed for web form %s: %s", form.id, exc
        )
        return False

    if not isinstance(body, dict):
        return False
    return bool(body.get("success"))
