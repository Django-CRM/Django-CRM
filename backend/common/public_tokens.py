"""Credentials that travel in a public URL path, kept out of logs and Sentry.

Each prefix below is followed by a path segment that is itself the credential:
whoever holds the URL reads, and for an estimate accepts or declines, without
signing in. Anything that records a request's path (Django's `django.request`
warnings such as ``Not Found: <path>`` and ``Too Many Requests: <path>``, and
a Sentry event's request block, transaction, breadcrumbs and spans) would hand
that credential on. Sentry's event scrubber matches keys, not values inside a
URL, so it cannot catch this.

Imported by ``crm/server_settings.py`` and by ``LOGGING`` while settings load,
so it must not need Django.
"""

import logging
import re

from common.calendar_feed import FEED_PATH_PREFIX

TOKEN_PATH_PREFIXES = (
    FEED_PATH_PREFIX,  # the task calendar feed, /api/public/calendar/<token>.ics
    "/api/public/csat/",  # a satisfaction survey
    "/api/public/invoice/",  # an invoice's portal link, and its /pdf/
    "/api/public/estimate/",  # an estimate's, and its /pdf/, /accept/, /decline/
)

_TOKEN = re.compile(
    "(" + "|".join(re.escape(prefix) for prefix in TOKEN_PATH_PREFIXES) + ")"
    r"[^/?#\s\"']+"
)


def redact(value):
    """``value`` with every public token cut from it, walking containers."""
    if isinstance(value, str):
        return _TOKEN.sub(r"\1[Filtered]", value)
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def scrub_public_tokens(event, hint):
    """Sentry ``before_send`` / ``before_send_transaction``: drop the tokens.

    Walks the whole event rather than naming the fields a URL can appear in,
    because that list belongs to the SDK and grows between releases.
    """
    return redact(event)


class RedactPublicTokens(logging.Filter):
    """A logging filter that rewrites a record's message without the tokens.

    The message is formatted once here and its arguments dropped, so no
    handler downstream can format the raw path back in.
    """

    def filter(self, record):
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
