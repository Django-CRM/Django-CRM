"""Resolving whose day it is.

Every "today", "overdue" and "this month" in this app goes through
``django.utils.timezone.localdate()``, which answers in whichever timezone is
currently active. This module is the one place that decides what that is.

Keeping it to one function is the point. The alternative, each view reaching for
``org.timezone`` itself, is how the codebase ended up with 132 sites computing
the day in UTC: a rule spread across 132 places cannot be changed, only
re-audited. Adding a per-user timezone later is a change to
``resolve_timezone_name`` and nothing else.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def resolve_timezone_name(org):
    """The IANA name whose day ``org`` keeps its books in.

    Falls back to ``settings.TIME_ZONE`` when there is no org, which is the case
    on the auth endpoints that run before one is chosen.
    """
    name = getattr(org, "timezone", None)
    return name or settings.TIME_ZONE


def activate_org_timezone(org):
    """Make ``timezone.localdate()`` answer in the org's day for this thread.

    A name that no longer exists in the system tz database (a zone renamed or
    dropped between releases) falls back to ``settings.TIME_ZONE`` rather than
    raising: the field is validated on write, so this can only happen to a value
    that was legal when it was stored, and refusing every request for that org
    would turn a stale string into an outage.
    """
    name = resolve_timezone_name(org)
    try:
        timezone.activate(ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Org %s has unknown timezone %r; falling back to %s",
            getattr(org, "id", None),
            name,
            settings.TIME_ZONE,
        )
        timezone.activate(ZoneInfo(settings.TIME_ZONE))


@contextmanager
def org_timezone(org):
    """``activate_org_timezone`` for one block, then put back what was active.

    For a Celery task that does one piece of day math: a worker runs no
    middleware to deactivate the zone afterwards, and its threads are reused,
    so a zone activated and left behind would hand this org's day to the next
    task. ``timezone.override(None)`` records the zone active on entry and
    restores it on exit, raised or not.
    """
    with timezone.override(None):
        activate_org_timezone(org)
        yield


# Region-prefixed legacy aliases. Every one of these has a modern equivalent in
# the list (`US/Eastern` is `America/New_York`), so offering both doubles the
# list without adding a single choice. `Etc/*` goes too: `Etc/GMT+5` counts its
# offset backwards, which nobody expects, and plain "UTC" is added back below.
_LEGACY_PREFIXES = (
    "US/",
    "Canada/",
    "Mexico/",
    "Brazil/",
    "Chile/",
    "Etc/",
    "SystemV/",
)


def selectable_timezones():
    """The zone names a client may offer, each with its current UTC offset.

    Both clients need this list, and they must not each build their own: the
    browser's `Intl.supportedValuesOf('timeZone')` answers "Asia/Calcutta" where
    Python's tz database also knows "Asia/Kolkata". A `<select>` built from one
    vocabulary cannot show a value stored from the other, and a select with no
    matching option submits its first entry instead, which would quietly move an
    Indian org to Africa/Abidjan the next time an admin saved the page.

    The offset travels with each name so a client can preselect a sensible
    default without shipping its own copy of the tz database. Mobile has no way
    to read the device's IANA name without a platform package, but every
    platform can report the device's current UTC offset, which narrows 490
    choices to a handful. It is the offset *now*, so it moves with daylight
    saving, and both sides compute "now" within the same request.

    Sorted by name, so a caller can render it without deciding on an order.
    """
    now = datetime.now(dt_timezone.utc)
    options = []
    for name in available_timezones():
        if "/" not in name or name.startswith(_LEGACY_PREFIXES):
            continue
        options.append(_option(name, now))
    options.append(_option("UTC", now))
    options.sort(key=lambda o: o["name"])
    return options


def _option(name, now):
    offset = now.astimezone(ZoneInfo(name)).utcoffset() or timedelta(0)
    return {"name": name, "offset_minutes": int(offset.total_seconds() // 60)}
