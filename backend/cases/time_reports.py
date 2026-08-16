"""Grouped totals and the CSV rows behind the time reports.

Kept beside ``time_views.py`` for the same reason ``analytics.py`` sits beside
``analytics_views.py``: the grouping and the money are the parts worth testing
without an HTTP layer wrapped around them.

Two shapes come out of here. :func:`build_report` answers "where did the time
go", one row per agent, ticket or account. :func:`csv_rows` answers "give me
the entries", one row per :class:`cases.models.TimeEntry`, which is what a
finance team pastes into a spreadsheet and what an invoice is checked against.

Both read only stopped entries. A running timer has ``duration_minutes`` 0
until it is stopped, so counting one would report a morning's work as nothing
and quietly drag an agent's total down.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
from django.utils import timezone
from rest_framework import status

GROUPINGS = ("agent", "ticket", "account")

# The columns each grouping needs out of the row. The first is the key.
_GROUP_FIELDS = {
    "agent": ("profile_id", "profile__user__name", "profile__user__email"),
    "ticket": ("case_id", "case__name"),
    "account": ("case__account_id", "case__account__name"),
}

_BILLABLE = Q(billable=True) & Q(hourly_rate__isnull=False)

# Money, computed in the database at the rate stored on each entry rather than
# today's rate: `hourly_rate` is snapshotted per entry precisely so that a rate
# change next month does not silently rewrite what last month was worth.
# `decimal_places=4` because minutes/60 is a third of a cent on odd durations,
# and rounding is done once, on the sum, not on every row of it.
_VALUE = ExpressionWrapper(
    F("duration_minutes") * F("hourly_rate") / Value(Decimal("60")),
    output_field=DecimalField(max_digits=16, decimal_places=4),
)

CSV_COLUMNS = (
    "Date",
    "Agent",
    "Ticket",
    "Account",
    "Description",
    "Minutes",
    "Hours",
    "Billable",
    "Rate",
    "Currency",
    "Value",
    "Invoice",
)


class TimeReportParamError(Exception):
    """A query parameter the report will not run on.

    Carries the status as well as the message so the caller does not have to
    guess whether a rejected ``profile`` is a 400 or a 403; reading another
    agent's time without being an admin is the second one.
    """

    def __init__(self, detail, status_code=status.HTTP_400_BAD_REQUEST):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def money(value):
    """A Decimal rounded to cents, as a string. ``None`` becomes ``"0.00"``."""
    amount = Decimal(value or 0)
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def hours(minutes):
    """Minutes as decimal hours, to two places: 45 minutes is ``"0.75"``."""
    return str(
        (Decimal(minutes or 0) / Decimal(60)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def build_report(entries, group_by):
    """Group ``entries`` and return ``{rows, totals, currencies}``.

    ``entries`` is expected to be scoped and filtered already: this function
    does not know who is asking, and adding an org filter here would read as
    though it did.

    Rows are ordered by time logged, descending, because the question the
    report answers is which agent, ticket or account took the most of it. A
    row's ``key`` is null for time on tickets with no account; that bucket is
    named rather than dropped, since unattributed time is exactly what someone
    running this report is looking for.
    """
    if group_by not in _GROUP_FIELDS:
        raise TimeReportParamError(
            f"group_by must be one of {', '.join(GROUPINGS)}, not {group_by!r}."
        )

    fields = _GROUP_FIELDS[group_by]
    grouped = (
        entries.values(*fields)
        .annotate(
            total_minutes=Sum("duration_minutes"),
            billable_minutes=Sum("duration_minutes", filter=Q(billable=True)),
            billable_value=Sum(_VALUE, filter=_BILLABLE),
            entry_count=Count("id"),
        )
        .order_by("-total_minutes")
    )

    rows = []
    for row in grouped:
        key = row[fields[0]]
        rows.append(
            {
                "key": str(key) if key else None,
                "name": _row_name(group_by, row, fields),
                "total_minutes": row["total_minutes"] or 0,
                "billable_minutes": row["billable_minutes"] or 0,
                "billable_value": money(row["billable_value"]),
                "entry_count": row["entry_count"],
            }
        )

    totals = {
        "total_minutes": sum(r["total_minutes"] for r in rows),
        "billable_minutes": sum(r["billable_minutes"] for r in rows),
        "billable_value": money(sum(Decimal(r["billable_value"]) for r in rows)),
        "entry_count": sum(r["entry_count"] for r in rows),
    }

    # Every currency present in the range, so a client can say "these totals
    # mix currencies" instead of printing one symbol over a sum of two. The
    # value column adds them regardless, which is the same compromise the
    # timesheet page and the shell's pipeline figures already make.
    currencies = sorted(set(entries.values_list("currency", flat=True).distinct()))

    return {"rows": rows, "totals": totals, "currencies": currencies}


def _row_name(group_by, row, fields):
    if group_by == "agent":
        # `User.name` is never blank (it falls back to the email local-part on
        # first save), but an entry can outlive nothing here, so the email is
        # the backstop rather than an empty cell.
        return row[fields[1]] or row[fields[2]] or "Unknown agent"
    if group_by == "ticket":
        return row[fields[1]] or "Deleted ticket"
    return row[fields[1]] or "No account"


def csv_rows(entries):
    """Yield the header, then one list per entry, for ``csv.writer``.

    A generator over ``.iterator()`` so a year of entries streams instead of
    being built in memory: the caller wraps this in a ``StreamingHttpResponse``.
    """
    yield list(CSV_COLUMNS)

    queryset = entries.select_related(
        "profile__user", "case", "case__account", "invoice"
    ).order_by("started_at")

    for entry in queryset.iterator(chunk_size=200):
        user = entry.profile.user if entry.profile_id else None
        account = entry.case.account if entry.case_id else None
        value = (
            money(Decimal(entry.duration_minutes) * entry.hourly_rate / Decimal(60))
            if entry.billable and entry.hourly_rate is not None
            else ""
        )
        yield [
            timezone.localdate(entry.started_at).isoformat(),
            (user.name or user.email) if user else "",
            entry.case.name if entry.case_id else "",
            account.name if account else "",
            entry.description or "",
            entry.duration_minutes,
            hours(entry.duration_minutes),
            "yes" if entry.billable else "no",
            str(entry.hourly_rate) if entry.hourly_rate is not None else "",
            entry.currency,
            value,
            entry.invoice.invoice_number if entry.invoice_id else "",
        ]
