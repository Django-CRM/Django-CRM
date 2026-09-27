"""The iCalendar (RFC 5545) writer behind the per-user task feed (G14).

Hand-written because the output is small and fixed: one VCALENDAR of all-day
VEVENTs. No third-party library is installed for it and none is wanted.

This module imports nothing from Django's model layer, so
``common.public_tokens`` (the log and Sentry scrubber for the feed URL, whose
token is the whole credential) can import it while settings are still loading.

What an event carries is an owner decision, not a formatting choice: the task's
title and priority and a link back into the app, nothing else. No notes, and no
contact, account or other record names, because a subscribed calendar is read by
a third-party service (Google, Microsoft, Apple) on the user's behalf.
"""

import re
from datetime import timedelta
from datetime import timezone as dt_timezone

CRLF = "\r\n"

# RFC 5545 3.1: "Lines of text SHOULD NOT be longer than 75 octets, excluding
# the line break."
FOLD_OCTETS = 75

PRODID = "-//BottleCRM//Task calendar feed//EN"
CALENDAR_NAME = "BottleCRM tasks"

# Every control character except HTAB is illegal in a TEXT value (RFC 5545
# 3.3.11). Line breaks are turned into the `\n` escape before this runs.
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def escape_text(value):
    """Escape a TEXT property value: backslash, semicolon, comma, line breaks."""
    value = (value or "").replace("\\", "\\\\")
    value = value.replace(";", "\\;").replace(",", "\\,")
    value = value.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    return _CONTROL.sub("", value)


def fold_line(line):
    """Fold one content line at 75 octets without splitting a UTF-8 character.

    Continuation lines start with a single space, which counts toward their own
    75, so they carry at most 74 octets of content.
    """
    parts = []
    current = ""
    size = 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > FOLD_OCTETS:
            parts.append(current)
            current = " "
            size = 1
        current += char
        size += width
    parts.append(current)
    return CRLF.join(parts)


def _stamp(moment):
    """A UTC DATE-TIME, e.g. 20260927T101500Z. ``moment`` must be aware."""
    return moment.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_calendar(events):
    """Render ``events`` as a complete VCALENDAR string.

    Each event is a dict with ``uid``, ``stamp`` (aware datetime), ``date`` (the
    day it falls on), ``summary``, ``description`` and ``url``. Every event is
    all-day: DTEND is the following day, which RFC 5545 treats as exclusive.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(CALENDAR_NAME)}",
    ]
    for event in events:
        day = event["date"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}",
            f"DTSTAMP:{_stamp(event['stamp'])}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{escape_text(event['summary'])}",
            f"DESCRIPTION:{escape_text(event['description'])}",
            f"URL:{event['url']}",
            # A task is something to do that day, not a meeting: it should not
            # mark the whole day busy for anyone checking free/busy.
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold_line(line) + CRLF for line in lines)


# The feed's path; the segment after it is the token, which
# `common.public_tokens` keeps out of the logs and out of Sentry.
FEED_PATH_PREFIX = "/api/public/calendar/"
