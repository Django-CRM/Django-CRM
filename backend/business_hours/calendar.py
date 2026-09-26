"""Business-hours forward walker.

Walks forward from ``start_dt`` adding ``hours`` of working time, skipping
non-working windows and holidays as configured on a ``BusinessCalendar``.

Falls back to 24/7 wall-clock arithmetic when no calendar is supplied so the
helper is backward compatible with code that hasn't been migrated yet.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

# Hard cap on how many calendar days the walker is allowed to traverse before
# giving up. 5 years is more than enough for any realistic SLA target while
# still keeping pathological inputs (e.g. a calendar with every weekday closed
# but a pruned-fallback bug) from looping forever.
_MAX_DAYS = 366 * 5

_UTC = ZoneInfo("UTC")


def _resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return _UTC


def _next_midnight(tz: ZoneInfo, current_local: datetime) -> datetime:
    """Return next-day local midnight in ``tz``, DST-safe."""
    next_date = current_local.date() + timedelta(days=1)
    return datetime.combine(next_date, time(0, 0), tzinfo=tz)


def _has_any_open_window(windows) -> bool:
    return any(o is not None and c is not None and c > o for (o, c) in windows)


def add_business_hours(start_dt: datetime, hours: float, calendar) -> datetime:
    """Return the datetime ``hours`` working hours after ``start_dt``.

    ``calendar`` is a ``BusinessCalendar`` instance, or ``None`` to fall back
    to 24/7 wall-clock arithmetic. Holidays attached to the calendar are
    skipped (full days off, in the calendar's timezone). The result preserves
    the input timezone. A UTC ``start_dt`` returns a UTC-aware datetime even
    if the calendar lives in another zone.
    """
    if hours is None:
        return start_dt
    if start_dt is None:
        return None

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_UTC)

    if calendar is None or hours <= 0:
        return start_dt + timedelta(hours=float(hours or 0))

    windows = calendar.windows_by_weekday()
    if not _has_any_open_window(windows):
        return start_dt + timedelta(hours=float(hours))

    tz = _resolve_tz(calendar.timezone or "UTC")
    # `.all()`, not `values_list`: the latter ignores the holidays that
    # `get_default_calendar` prefetches and queried them again on every call,
    # up to four times per ticket on a board.
    holidays = {holiday.date for holiday in calendar.holidays.all()}

    cur = start_dt.astimezone(tz)
    remaining = timedelta(hours=float(hours))
    out_tz = start_dt.tzinfo

    for _ in range(_MAX_DAYS):
        weekday = cur.weekday()  # 0=Mon..6=Sun
        open_t, close_t = windows[weekday]
        date_local = cur.date()

        is_closed = (
            open_t is None
            or close_t is None
            or close_t <= open_t
            or date_local in holidays
        )
        if is_closed:
            cur = _next_midnight(tz, cur)
            continue

        day_open = datetime.combine(date_local, open_t, tzinfo=tz)
        day_close = datetime.combine(date_local, close_t, tzinfo=tz)

        if cur >= day_close:
            cur = _next_midnight(tz, cur)
            continue
        if cur < day_open:
            cur = day_open

        available = day_close - cur
        if remaining <= available:
            return (cur + remaining).astimezone(out_tz)
        remaining -= available
        cur = _next_midnight(tz, cur)

    # Defensive: if we somehow exhausted the day cap (calendar with every day
    # closed, slipping past _has_any_open_window), return what we've got
    # rather than spinning forever.
    return cur.astimezone(out_tz)


def get_default_calendar(org_id) -> Optional["BusinessCalendar"]:  # noqa: F821
    """Return the org's default BusinessCalendar with holidays prefetched.

    Returns ``None`` if the org has no calendar yet (helper falls back to
    24/7 wall-clock, matching pre-feature behavior).
    """
    from business_hours.models import BusinessCalendar

    return (
        BusinessCalendar.objects.filter(org_id=org_id, is_default=True)
        .prefetch_related("holidays")
        .first()
    )
