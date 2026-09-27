"""Credentials that travel in a public URL path, kept out of logs and Sentry.

Each prefix below is followed by a path segment that is itself the credential:
whoever holds the URL reads, and for an estimate accepts or declines, without
signing in. Anything that records a request's path (Django's `django.request`
warnings such as ``Not Found: <path>`` and ``Too Many Requests: <path>``, and
a Sentry event's request block, transaction, breadcrumbs and spans) would hand
that credential on. Sentry's event scrubber matches keys, not values inside a
URL, so it cannot catch this.

The app server's access log (uvicorn's ``uvicorn.access``, gunicorn's
``gunicorn.access``) records every path too; ``crm/settings.py`` attaches
``RedactAccessLog`` to both. A reverse proxy's access log is outside the
process and needs its own redaction (see the self-hosting security docs).

Imported by ``crm/server_settings.py`` and by ``crm/settings.py`` while
settings load, so it must not need Django.
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


class RedactAccessLog(logging.Filter):
    """The same redaction for an app server's access log, keeping the args.

    Both access formatters read ``record.args`` rather than the message:
    uvicorn's ``AccessFormatter`` unpacks it as a five-item tuple, and gunicorn
    formats its ``access_log_format`` against a dict of atoms whose own type
    answers ``-`` for a header the request lacked. So each argument is
    redacted where it stands instead of being formatted in and dropped.
    """

    def filter(self, record):
        if isinstance(record.args, dict):
            for key, item in record.args.items():
                record.args[key] = redact(item)
        else:
            record.args = redact(record.args)
        return True
