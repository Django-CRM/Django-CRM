"""Pure analytics aggregation for the cases reporting endpoints.

The functions here take a base `Case` queryset (already filtered by org +
visibility + any caller-supplied filters) plus a [from_dt, to_dt) window
and return JSON-friendly dicts that the views serialize directly.

Why pull values into Python rather than use DB percentiles? SQLite (used in
the test suite) has no `percentile_cont`. The volumes here (cases per org per
window) sit well within the millions-of-rows-into-Python range; if a deployment
ever pushes past that, switch to a Postgres-only path or the optional
`CaseMetricsDaily` cache table called out in the spec.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from cases.workflow import (
    TERMINAL_STATUSES,
    resolve_next_response_targets,
    resolve_sla_targets,
)

# ---------------------------------------------------------------------------
# Helpers


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile (matches numpy.percentile default).

    Returns None for empty input. ``pct`` is in [0, 100]. ``sorted_values``
    must already be sorted ascending.
    """
    if not sorted_values:
        return None
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def _day_start(d: date) -> datetime:
    """The instant the org's day ``d`` begins.

    Every bucket here is labelled with a calendar date, so its edges have to be
    the same calendar's midnights. In Asia/Kolkata a ticket opened at 04:00
    belongs to that day; measured against a UTC boundary it lands in the one
    before, and the chart shows a quiet morning followed by a spike that never
    happened. ``get_current_timezone`` returns whatever ``activate_org_timezone``
    put there for this request, so this is the org's midnight, not the server's.
    """
    return datetime.combine(
        d, datetime.min.time(), tzinfo=timezone.get_current_timezone()
    )


def _bucket_dates(from_dt: datetime, to_dt: datetime) -> list[date]:
    """Inclusive list of org-local dates from from_dt to to_dt-1 day."""
    if to_dt <= from_dt:
        return []
    start = timezone.localdate(from_dt)
    end = timezone.localdate(to_dt - timedelta(seconds=1))
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _coerce_window(
    from_dt: Optional[datetime], to_dt: Optional[datetime]
) -> tuple[datetime, datetime]:
    """Defaults to last 30 days; ensures both ends are tz-aware.

    A naive end is read as the org's wall clock, matching how the client sent
    it. Reading it as UTC would shift an Indian org's window by five and a half
    hours against the days it is asking about.
    """
    now = timezone.now()
    if to_dt is None:
        to_dt = now
    if from_dt is None:
        from_dt = to_dt - timedelta(days=30)
    if timezone.is_naive(from_dt):
        from_dt = timezone.make_aware(from_dt)
    if timezone.is_naive(to_dt):
        to_dt = timezone.make_aware(to_dt)
    return from_dt, to_dt


# ---------------------------------------------------------------------------
# Metric computations


def compute_frt(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> dict:
    """First Response Time aggregation.

    Window scopes by `Case.created_at`. For each case with `first_response_at`
    set, FRT = first_response_at - created_at. Breach counts cases where
    FRT > sla_first_response_hours, OR where first_response_at is null and
    `now - created_at > sla` (an in-flight breach).
    """
    from_dt, to_dt = _coerce_window(from_dt, to_dt)
    in_window = qs.filter(created_at__gte=from_dt, created_at__lt=to_dt)

    rows = list(
        in_window.values_list(
            "id",
            "created_at",
            "first_response_at",
            "sla_first_response_hours",
        )
    )
    now = timezone.now()
    deltas: list[float] = []
    breach_count = 0
    case_ids: list[UUID] = []
    breach_ids: list[UUID] = []
    for case_id, created_at, first_response_at, sla_hours in rows:
        case_ids.append(case_id)
        if first_response_at is not None:
            hours = _hours_between(first_response_at, created_at)
            deltas.append(hours)
            if hours > (sla_hours or 0):
                breach_count += 1
                breach_ids.append(case_id)
        else:
            # Open-but-unresponded: count as breach if SLA already exceeded.
            if _hours_between(now, created_at) > (sla_hours or 0):
                breach_count += 1
                breach_ids.append(case_id)

    deltas.sort()
    median_h = _percentile(deltas, 50)
    p90_h = _percentile(deltas, 90)

    # Daily series of medians (created_at bucket).
    series: list[dict] = []
    by_day: dict[date, list[float]] = {}
    for _id, created_at, first_response_at, _sla in rows:
        if first_response_at is None:
            continue
        bucket = timezone.localdate(created_at)
        by_day.setdefault(bucket, []).append(
            _hours_between(first_response_at, created_at)
        )
    for d in _bucket_dates(from_dt, to_dt):
        values = sorted(by_day.get(d, []))
        series.append(
            {
                "bucket": d.isoformat(),
                "median": _percentile(values, 50) if values else None,
                "count": len(values),
            }
        )

    return {
        "median_hours": median_h,
        "p90_hours": p90_h,
        "count": len(deltas),
        "breach_count": breach_count,
        "series": series,
        "case_ids": [str(cid) for cid in case_ids],
        "breach_case_ids": [str(cid) for cid in breach_ids],
    }


def _next_response_waits(
    case_ids: Iterable[UUID],
) -> dict[UUID, list[tuple[datetime, Optional[datetime]]]]:
    """Every wait for a next reply on the given cases, as ``(start, answered_at)``.

    A customer message is a public comment with no ``commented_by`` (written in
    the portal, or by nobody on our side) or an inbound email on the ticket. An
    agent reply is a public comment with a ``commented_by``: the same test
    ``cases.signals._maybe_stamp_first_response`` uses, so the first reply this
    finds is the one ``first_response_at`` records. Internal notes are neither.

    Only waits after the first agent reply count. Everything before it is the
    first-response wait, which FRT already measures from ``created_at``; the
    email that opened an inbound ticket always falls there. A run of customer
    messages is one wait, timed from the first one nobody has answered yet, and
    an agent reply with nothing to answer ends no wait. ``answered_at`` is None
    for a wait still open.
    """
    from django.contrib.contenttypes.models import ContentType

    from cases.models import Case, EmailMessage
    from common.models import Comment

    case_ids = list(case_ids)
    if not case_ids:
        return {}

    # (case, when, is_agent). A customer message sorts before an agent reply
    # stamped the same instant, so that reply answers it.
    events: list[tuple[UUID, datetime, bool]] = []
    for case_id, created_at, author_id in Comment.objects.filter(
        content_type=ContentType.objects.get_for_model(Case),
        object_id__in=case_ids,
        is_internal=False,
    ).values_list("object_id", "created_at", "commented_by_id"):
        events.append((case_id, created_at, author_id is not None))
    for case_id, received_at in EmailMessage.objects.filter(
        case_id__in=case_ids, direction="inbound", drop_reason=""
    ).values_list("case_id", "received_at"):
        events.append((case_id, received_at, False))
    events.sort(key=lambda e: (e[1], e[2]))

    answered_once: set[UUID] = set()
    waiting_since: dict[UUID, datetime] = {}
    waits: dict[UUID, list[tuple[datetime, Optional[datetime]]]] = {}
    for case_id, at, is_agent in events:
        if is_agent:
            start = waiting_since.pop(case_id, None)
            if start is not None:
                waits.setdefault(case_id, []).append((start, at))
            answered_once.add(case_id)
        elif case_id in answered_once and case_id not in waiting_since:
            waiting_since[case_id] = at
    for case_id, start in waiting_since.items():
        waits.setdefault(case_id, []).append((start, None))
    return waits


def _wait_breached(start, answered_at, target_hours, is_open, now) -> bool:
    """Late if answered past the target; also late if still waiting past it on
    an open ticket. A closed ticket's unanswered last word (the customer's
    "thanks") is not somebody we owe a reply."""
    if answered_at is not None:
        return _hours_between(answered_at, start) > target_hours
    return is_open and _hours_between(now, start) > target_hours


def compute_nrt(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    *,
    org_id,
) -> dict:
    """Next Response Time aggregation: how long a customer who wrote back after
    the first reply waited for the next one. See `_next_response_waits`.

    Same shape as `compute_frt`, plus ``by_priority``: every priority in
    worst-first order, with its target, median and met/missed counts (an
    unanswered wait not yet past its target is neither). The window scopes by when
    the wait began (the customer's message), since one ticket can wait many
    times over its life. Only cases in ``qs`` are read, so a non-admin is scored
    on the tickets they may see. Breaches are scored against the org's
    next-response target for the case's priority
    (`cases.workflow.resolve_next_response_targets`): a wait answered late, or
    still unanswered past it on an open ticket.
    """
    from django.contrib.contenttypes.models import ContentType

    from cases.models import Case, EmailMessage
    from common.models import Comment

    from_dt, to_dt = _coerce_window(from_dt, to_dt)

    # Only a case with a customer message inside the window can have a wait
    # starting there. The wait itself is found from the case's whole history,
    # because whether a message opens a wait depends on what came before it.
    visible_ids = qs.values("id")
    started = set(
        Comment.objects.filter(
            content_type=ContentType.objects.get_for_model(Case),
            object_id__in=visible_ids,
            is_internal=False,
            commented_by__isnull=True,
            created_at__gte=from_dt,
            created_at__lt=to_dt,
        ).values_list("object_id", flat=True)
    ) | set(
        EmailMessage.objects.filter(
            case_id__in=visible_ids,
            direction="inbound",
            drop_reason="",
            received_at__gte=from_dt,
            received_at__lt=to_dt,
        ).values_list("case_id", flat=True)
    )
    rows = list(qs.filter(id__in=started).values_list("id", "priority", "status"))
    waits = _next_response_waits(r[0] for r in rows)
    targets = resolve_next_response_targets(org_id)

    now = timezone.now()
    deltas: list[float] = []
    by_day: dict[date, list[float]] = {}
    per_priority: dict[str, dict] = {
        prio: {"hours": [], "met": 0, "missed": 0} for prio in _SERVICE_PRIORITY_ORDER
    }
    case_ids: list[UUID] = []
    breach_ids: list[UUID] = []
    breach_count = 0
    for case_id, priority, status in rows:
        target = targets.get(priority, 4)
        # An unknown priority string still counts in the totals; it just has
        # no row of its own, as in `_first_response_by_priority`.
        bucket = per_priority.get(priority, {"hours": [], "met": 0, "missed": 0})
        in_window = [
            (start, answered)
            for start, answered in waits.get(case_id, [])
            if from_dt <= start < to_dt
        ]
        if not in_window:
            continue
        case_ids.append(case_id)
        case_breached = False
        for start, answered in in_window:
            if answered is not None:
                hours = _hours_between(answered, start)
                deltas.append(hours)
                bucket["hours"].append(hours)
                by_day.setdefault(timezone.localdate(start), []).append(hours)
            breached = _wait_breached(
                start, answered, target, status not in TERMINAL_STATUSES, now
            )
            if breached:
                breach_count += 1
                bucket["missed"] += 1
                case_breached = True
            elif answered is not None:
                bucket["met"] += 1
        if case_breached:
            breach_ids.append(case_id)

    deltas.sort()
    series = []
    for d in _bucket_dates(from_dt, to_dt):
        values = sorted(by_day.get(d, []))
        series.append(
            {
                "bucket": d.isoformat(),
                "median": _percentile(values, 50) if values else None,
                "count": len(values),
            }
        )
    by_priority = [
        {
            "priority": prio,
            "target_hours": targets.get(prio, 4),
            "median_hours": _percentile(sorted(b["hours"]), 50),
            "met": b["met"],
            "missed": b["missed"],
        }
        for prio, b in per_priority.items()
    ]

    return {
        "median_hours": _percentile(deltas, 50),
        "p90_hours": _percentile(deltas, 90),
        "count": len(deltas),
        "breach_count": breach_count,
        "series": series,
        "by_priority": by_priority,
        "case_ids": [str(cid) for cid in case_ids],
        "breach_case_ids": [str(cid) for cid in breach_ids],
    }


def compute_mttr(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> dict:
    """Mean Time To Resolution. Window scopes by `Case.resolved_at`."""
    from_dt, to_dt = _coerce_window(from_dt, to_dt)
    rows = list(
        qs.filter(
            resolved_at__isnull=False,
            resolved_at__gte=from_dt,
            resolved_at__lt=to_dt,
        ).values_list("id", "created_at", "resolved_at", "priority")
    )
    deltas: list[float] = []
    by_priority_lists: dict[str, list[float]] = {}
    case_ids: list[UUID] = []
    for case_id, created_at, resolved_at, priority in rows:
        h = _hours_between(resolved_at, created_at)
        deltas.append(h)
        case_ids.append(case_id)
        by_priority_lists.setdefault(priority, []).append(h)

    deltas_sorted = sorted(deltas)
    by_priority: dict[str, dict] = {}
    for prio, values in by_priority_lists.items():
        values.sort()
        by_priority[prio] = {
            "mean_hours": (sum(values) / len(values)) if values else None,
            "median_hours": _percentile(values, 50),
            "p90_hours": _percentile(values, 90),
            "count": len(values),
        }

    return {
        "mean_hours": (sum(deltas) / len(deltas)) if deltas else None,
        "median_hours": _percentile(deltas_sorted, 50),
        "p90_hours": _percentile(deltas_sorted, 90),
        "count": len(deltas),
        "by_priority": by_priority,
        "case_ids": [str(cid) for cid in case_ids],
    }


def compute_backlog(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> dict:
    """Open + urgent counts at end of each day in window.

    Open at day D = created_at <= end_of_D AND (resolved_at IS NULL OR resolved_at > end_of_D).
    Cheap with the new (org, resolved_at) index since we never scan more than
    once per case row to materialize the window.
    """
    from_dt, to_dt = _coerce_window(from_dt, to_dt)
    rows = list(
        qs.filter(
            Q(created_at__lt=to_dt)
            & (Q(resolved_at__isnull=True) | Q(resolved_at__gte=from_dt))
        ).values_list("created_at", "resolved_at", "priority")
    )

    series: list[dict] = []
    for d in _bucket_dates(from_dt, to_dt):
        end_of_day = _day_start(d + timedelta(days=1))
        open_count = 0
        urgent_count = 0
        for created_at, resolved_at, priority in rows:
            if created_at >= end_of_day:
                continue
            if resolved_at is not None and resolved_at <= end_of_day:
                continue
            open_count += 1
            if priority == "Urgent":
                urgent_count += 1
        series.append(
            {
                "date": d.isoformat(),
                "open_count": open_count,
                "urgent_count": urgent_count,
            }
        )

    return {"series": series}


def compute_agents(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> list[dict]:
    """Per-agent rollup. A case with N assignees counts once per agent.

    Returns list of `{profile_id, name, email, handled, avg_frt_hours, csat_avg, breach_rate}`.
    `csat_avg` is computed from `CsatSurvey` rows (responded, in-window) tied
    to cases this agent was assigned to.
    """
    from_dt, to_dt = _coerce_window(from_dt, to_dt)
    in_window = qs.filter(created_at__gte=from_dt, created_at__lt=to_dt)

    rows = list(
        in_window.values_list(
            "id",
            "created_at",
            "first_response_at",
            "resolved_at",
            "sla_first_response_hours",
            "sla_resolution_hours",
            "assigned_to",
            "assigned_to__user__email",
        )
    )
    now = timezone.now()

    # Per-agent CSAT averages, scoped to the same case set + window. Pulled
    # in one query keyed by profile_id so we don't N+1 inside the loop.
    from cases.models import CsatSurvey

    case_ids = {row[0] for row in rows}
    csat_pairs = list(
        CsatSurvey.objects.filter(
            case_id__in=case_ids, rating__isnull=False
        ).values_list("case_id", "rating")
    )
    rating_by_case = {cid: rating for cid, rating in csat_pairs}

    agg: dict[UUID, dict] = {}
    for (
        case_id,
        created_at,
        first_response_at,
        resolved_at,
        sla_frt,
        sla_res,
        profile_id,
        email,
    ) in rows:
        if profile_id is None:
            continue
        bucket = agg.setdefault(
            profile_id,
            {
                "profile_id": str(profile_id),
                "name": email or "",
                "email": email or "",
                "handled": 0,
                "frt_values": [],
                "csat_values": [],
                "breach_count": 0,
            },
        )
        bucket["handled"] += 1
        if case_id in rating_by_case:
            bucket["csat_values"].append(rating_by_case[case_id])
        if first_response_at is not None:
            h = _hours_between(first_response_at, created_at)
            bucket["frt_values"].append(h)
            if h > (sla_frt or 0):
                bucket["breach_count"] += 1
        elif _hours_between(now, created_at) > (sla_frt or 0):
            bucket["breach_count"] += 1
        # Resolution breach: closed past SLA, OR open past SLA.
        if resolved_at is not None:
            if _hours_between(resolved_at, created_at) > (sla_res or 0):
                bucket["breach_count"] += 1
        elif _hours_between(now, created_at) > (sla_res or 0):
            bucket["breach_count"] += 1

    out: list[dict] = []
    for bucket in agg.values():
        frt_values = bucket.pop("frt_values")
        avg_frt = (sum(frt_values) / len(frt_values)) if frt_values else None
        csat_values = bucket.pop("csat_values")
        avg_csat = (sum(csat_values) / len(csat_values)) if csat_values else None
        breach_count = bucket.pop("breach_count")
        # breach_rate denominator is "events that could breach": one FRT-check
        # plus one resolution-check per case, so 2 * handled.
        denom = max(1, 2 * bucket["handled"])
        bucket["avg_frt_hours"] = avg_frt
        bucket["csat_avg"] = avg_csat
        bucket["breach_rate"] = breach_count / denom
        out.append(bucket)

    out.sort(key=lambda r: (-r["handled"], r["email"]))
    return out


def compute_sla(
    qs: QuerySet,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
) -> dict:
    """Org-wide SLA breach rates. Window scopes by `Case.created_at`.

    A case breached next response when any of its waits for a reply after the
    first was answered late, or is still open past the target (see
    `compute_nrt`); the rate is over every case in the window, like the others.
    """
    from_dt, to_dt = _coerce_window(from_dt, to_dt)
    rows = list(
        qs.filter(created_at__gte=from_dt, created_at__lt=to_dt).values_list(
            "id",
            "created_at",
            "first_response_at",
            "resolved_at",
            "sla_first_response_hours",
            "sla_resolution_hours",
            "priority",
            "status",
            "org_id",
        )
    )
    now = timezone.now()
    waits = _next_response_waits(row[0] for row in rows)
    nrt_targets = resolve_next_response_targets(rows[0][8]) if rows else {}

    total = len(rows)
    frt_breach = 0
    res_breach = 0
    nrt_breach = 0
    breach_ids_frt: list[UUID] = []
    breach_ids_res: list[UUID] = []
    breach_ids_nrt: list[UUID] = []
    by_priority: dict[str, dict] = {}

    for (
        case_id,
        created_at,
        first_response_at,
        resolved_at,
        sla_frt,
        sla_res,
        priority,
        status,
        _org_id,
    ) in rows:
        prio_bucket = by_priority.setdefault(
            priority,
            {"total": 0, "frt_breach": 0, "resolution_breach": 0, "nrt_breach": 0},
        )
        prio_bucket["total"] += 1

        is_frt_breach = False
        if first_response_at is not None:
            if _hours_between(first_response_at, created_at) > (sla_frt or 0):
                is_frt_breach = True
        elif _hours_between(now, created_at) > (sla_frt or 0):
            is_frt_breach = True
        if is_frt_breach:
            frt_breach += 1
            breach_ids_frt.append(case_id)
            prio_bucket["frt_breach"] += 1

        is_res_breach = False
        if resolved_at is not None:
            if _hours_between(resolved_at, created_at) > (sla_res or 0):
                is_res_breach = True
        elif _hours_between(now, created_at) > (sla_res or 0):
            is_res_breach = True
        if is_res_breach:
            res_breach += 1
            breach_ids_res.append(case_id)
            prio_bucket["resolution_breach"] += 1

        nrt_target = nrt_targets.get(priority, 4)
        is_open = status not in TERMINAL_STATUSES
        if any(
            _wait_breached(start, answered, nrt_target, is_open, now)
            for start, answered in waits.get(case_id, [])
        ):
            nrt_breach += 1
            breach_ids_nrt.append(case_id)
            prio_bucket["nrt_breach"] += 1

    def _rate(num: int, denom: int) -> Optional[float]:
        return (num / denom) if denom else None

    by_priority_out: dict[str, dict] = {}
    for prio, b in by_priority.items():
        by_priority_out[prio] = {
            "total": b["total"],
            "frt_breach_rate": _rate(b["frt_breach"], b["total"]),
            "resolution_breach_rate": _rate(b["resolution_breach"], b["total"]),
            "nrt_breach_rate": _rate(b["nrt_breach"], b["total"]),
        }

    return {
        "total": total,
        "frt_breach_count": frt_breach,
        "resolution_breach_count": res_breach,
        "nrt_breach_count": nrt_breach,
        "frt_breach_rate": _rate(frt_breach, total),
        "resolution_breach_rate": _rate(res_breach, total),
        "nrt_breach_rate": _rate(nrt_breach, total),
        "by_priority": by_priority_out,
        "frt_breach_case_ids": [str(cid) for cid in breach_ids_frt],
        "resolution_breach_case_ids": [str(cid) for cid in breach_ids_res],
        "nrt_breach_case_ids": [str(cid) for cid in breach_ids_nrt],
    }


# ---------------------------------------------------------------------------
# Drilldown helper


def case_ids_for_metric(
    metric: str,
    qs: QuerySet,
    from_dt: Optional[datetime],
    to_dt: Optional[datetime],
    bucket: Optional[str] = None,
) -> Iterable[UUID]:
    """Return the set of case ids that the named metric/bucket pulls in.

    Used by both the drilldown JSON endpoint and the CSV export. Bucket is
    metric-specific: e.g. "breach" for frt/sla, a date string for backlog.
    """
    from_dt, to_dt = _coerce_window(from_dt, to_dt)

    if metric == "frt":
        in_window = qs.filter(created_at__gte=from_dt, created_at__lt=to_dt)
        if bucket == "breach":
            data = compute_frt(qs, from_dt, to_dt)
            return [UUID(s) for s in data["breach_case_ids"]]
        return in_window.values_list("id", flat=True)

    if metric == "mttr":
        return qs.filter(
            resolved_at__isnull=False,
            resolved_at__gte=from_dt,
            resolved_at__lt=to_dt,
        ).values_list("id", flat=True)

    if metric == "sla":
        if bucket == "frt_breach":
            return [
                UUID(s) for s in compute_sla(qs, from_dt, to_dt)["frt_breach_case_ids"]
            ]
        if bucket == "resolution_breach":
            return [
                UUID(s)
                for s in compute_sla(qs, from_dt, to_dt)["resolution_breach_case_ids"]
            ]
        return qs.filter(created_at__gte=from_dt, created_at__lt=to_dt).values_list(
            "id", flat=True
        )

    if metric == "backlog":
        # Bucket is a YYYY-MM-DD date. Return cases open at end of that day.
        if not bucket:
            raise ValueError("backlog drilldown requires a bucket=YYYY-MM-DD")
        target = date.fromisoformat(bucket)
        end_of_day = _day_start(target + timedelta(days=1))
        return qs.filter(
            Q(created_at__lt=end_of_day)
            & (Q(resolved_at__isnull=True) | Q(resolved_at__gt=end_of_day))
        ).values_list("id", flat=True)

    if metric == "agents":
        # Bucket = profile_id (uuid string).
        if not bucket:
            raise ValueError("agents drilldown requires a bucket=<profile_id>")
        return (
            qs.filter(
                created_at__gte=from_dt,
                created_at__lt=to_dt,
                assigned_to=bucket,
            )
            .values_list("id", flat=True)
            .distinct()
        )

    raise ValueError(f"unknown metric: {metric}")


# ---------------------------------------------------------------------------
# Service-desk overview (single-call dashboard for /v2/tickets/analytics)
#
# The per-metric endpoints above answer one question each; this assembles the
# whole "service health" page in one admin-only call so the frontend does not
# fan out five requests. Elapsed times are wall-clock, consistent with the
# compute_* functions above (business hours inform SLA *deadlines*, not the
# elapsed math here, the same caveat the rest of this module already lives
# with). The caller admin-gates this, so `qs` is the full org queryset with no
# per-user visibility narrowing.

# Worst-priority first, so the card leads with what hurts most. Every priority
# is emitted even with no cases, so the shape is stable window to window.
_SERVICE_PRIORITY_ORDER = ("Urgent", "High", "Normal", "Low")

# How long the service window is when the caller does not say. The view reads
# this too, so "no `?days=`" and "the function's default" cannot drift apart.
DEFAULT_SERVICE_DAYS = 14


def _business_hours_state(org_id) -> tuple[Optional[str], bool]:
    """(calendar_name, business_hours_applied) for the org's default calendar.

    Mirrors the SLA engine's own fallback (`business_hours.calendar`): no
    calendar, or a calendar with no open weekday window, means 24/7
    wall-clock, reported here as not-applied with a null name.
    """
    from business_hours.calendar import get_default_calendar

    cal = get_default_calendar(org_id)
    if cal is None:
        return None, False
    applied = any(
        o is not None and c is not None and c > o for (o, c) in cal.windows_by_weekday()
    )
    return cal.name, applied


def _accumulate_frt(
    bucket: dict, created_at, first_response_at, sla_hours, now
) -> None:
    """Fold one case's first-response outcome into a per-{priority,agent} bucket.

    A responded case contributes its FRT (minutes) to the median list and, if
    over its own SLA, a breach. An unresponded case only counts as a breach
    once it is already overdue; while still in-flight it counts toward neither
    met nor missed, so attainment describes only decided cases.
    """
    sla = sla_hours or 0
    if first_response_at is not None:
        hours = _hours_between(first_response_at, created_at)
        bucket["frt_minutes"].append(hours * 60.0)
        if hours > sla:
            bucket["breached"] += 1
        else:
            bucket["met"] += 1
    elif _hours_between(now, created_at) > sla:
        bucket["breached"] += 1


def _first_response_by_priority(created_rows, now, org_id) -> list[dict]:
    """Per-priority first-response attainment, worst-priority first.

    `target_minutes` is the org's promise for that priority today: its active
    `EscalationPolicy` target, or the built-in default where it has none
    (`cases.workflow.resolve_sla_targets`), in minutes. `met`/`missed` are
    scored against each case's own stored SLA, the target in force when it
    was opened. Priorities with no activity are still emitted with zero counts
    and a null median so the card never collapses.
    """
    per: dict[str, dict] = {
        prio: {"met": 0, "breached": 0, "frt_minutes": []}
        for prio in _SERVICE_PRIORITY_ORDER
    }
    for _id, created_at, first_response_at, sla_hours, priority, _ctype in created_rows:
        bucket = per.get(priority)
        if bucket is None:
            # Unknown priority string: skip rather than invent a column.
            continue
        _accumulate_frt(bucket, created_at, first_response_at, sla_hours, now)

    out: list[dict] = []
    for prio in _SERVICE_PRIORITY_ORDER:
        bucket = per[prio]
        median = _percentile(sorted(bucket["frt_minutes"]), 50)
        out.append(
            {
                "priority": prio,
                "target_minutes": resolve_sla_targets(org_id, prio)[0] * 60,
                "median_minutes": round(median) if median is not None else None,
                "met": bucket["met"],
                "missed": bucket["breached"],
            }
        )
    return out


def _agent_table(qs, from_dt, to_dt, now) -> list[dict]:
    """Per-agent table: currently-open, closed-this-week, median FRT, breaches.

    Each column has its own natural time basis: `open` is point-in-time (not
    in a terminal status), `closed_this_week` is the trailing 7 days, and the
    FRT/breach figures are over the [from, to) window. A case with N assignees
    counts once per agent (M2M fan-out). The unassigned bucket is computed with
    explicit `assigned_to__isnull=True` filters rather than a NULL row from the
    M2M join, so it does not depend on whether Django emits an inner or outer
    join for the multi-valued relation.
    """
    week_from = now - timedelta(days=7)
    open_qs = qs.exclude(status__in=TERMINAL_STATUSES)
    closed_week_qs = qs.filter(
        resolved_at__isnull=False, resolved_at__gte=week_from, resolved_at__lt=to_dt
    )
    window_qs = qs.filter(created_at__gte=from_dt, created_at__lt=to_dt)

    agents: dict = {}

    def bucket(pid):
        return agents.setdefault(
            pid,
            {
                "open": 0,
                "closed_this_week": 0,
                "frt_minutes": [],
                "breached": 0,
                "met": 0,
            },
        )

    for _cid, pid in open_qs.values_list("id", "assigned_to"):
        if pid is not None:
            bucket(pid)["open"] += 1
    for _cid, pid in closed_week_qs.values_list("id", "assigned_to"):
        if pid is not None:
            bucket(pid)["closed_this_week"] += 1
    for _cid, created_at, fra, sla_hours, pid in window_qs.values_list(
        "id",
        "created_at",
        "first_response_at",
        "sla_first_response_hours",
        "assigned_to",
    ):
        if pid is not None:
            _accumulate_frt(bucket(pid), created_at, fra, sla_hours, now)

    # Unassigned bucket: explicit isnull filters (see docstring).
    un = {"open": 0, "closed_this_week": 0, "frt_minutes": [], "breached": 0, "met": 0}
    un["open"] = open_qs.filter(assigned_to__isnull=True).count()
    un["closed_this_week"] = closed_week_qs.filter(assigned_to__isnull=True).count()
    for _cid, created_at, fra, sla_hours in window_qs.filter(
        assigned_to__isnull=True
    ).values_list("id", "created_at", "first_response_at", "sla_first_response_hours"):
        _accumulate_frt(un, created_at, fra, sla_hours, now)

    from common.models import Profile

    label_by_pid = {
        pid: (name or email or str(pid))
        for pid, name, email in Profile.objects.filter(
            id__in=list(agents.keys())
        ).values_list("id", "user__name", "user__email")
    }

    def _row(pid, name, b):
        median = _percentile(sorted(b["frt_minutes"]), 50)
        return {
            "id": None if pid is None else str(pid),
            "name": name,
            "open": b["open"],
            "closed_this_week": b["closed_this_week"],
            "median_first_response_minutes": (
                round(median) if median is not None else None
            ),
            "breached": b["breached"],
        }

    rows = [_row(pid, label_by_pid.get(pid, str(pid)), b) for pid, b in agents.items()]
    rows.sort(key=lambda r: (-r["open"], r["name"].lower()))

    # Only show the unassigned line when it carries signal, so it does not
    # dangle as an all-zero row on a fully-triaged queue.
    if un["open"] or un["closed_this_week"] or un["frt_minutes"] or un["breached"]:
        rows.append(_row(None, "Unassigned", un))
    return rows


def compute_service_overview(
    qs: QuerySet, org_id, days: int = DEFAULT_SERVICE_DAYS
) -> dict:
    """Assemble the full /v2/tickets/analytics payload in one call.

    Returns `{totals, volume, first_response, by_type, by_agent}`. The window
    is the last `days` whole days (default 14, clamped to [1, 90]), anchored to
    the org's day boundaries so `volume` has exactly `days` buckets and the last
    one is the org's today. `qs` must be the org-scoped Case queryset; this
    function does no visibility narrowing (the endpoint is admin-only).
    """
    days = max(1, min(days, 90))

    now = timezone.now()
    to_dt = _day_start(timezone.localdate(now) + timedelta(days=1))
    from_dt = to_dt - timedelta(days=days)

    created_rows = list(
        qs.filter(created_at__gte=from_dt, created_at__lt=to_dt).values_list(
            "id",
            "created_at",
            "first_response_at",
            "sla_first_response_hours",
            "priority",
            "case_type",
        )
    )
    resolved_rows = list(
        qs.filter(
            resolved_at__isnull=False,
            resolved_at__gte=from_dt,
            resolved_at__lt=to_dt,
        ).values_list("id", "created_at", "resolved_at")
    )

    # ---- totals ----
    mttr_hours = sorted(
        _hours_between(resolved_at, created_at)
        for _id, created_at, resolved_at in resolved_rows
    )
    median_res = _percentile(mttr_hours, 50)
    calendar_name, business_hours_applied = _business_hours_state(org_id)
    totals = {
        "opened": len(created_rows),
        "closed": len(resolved_rows),
        "open_now": qs.exclude(status__in=TERMINAL_STATUSES).count(),
        "median_resolution_hours": round(median_res) if median_res is not None else 0,
        "window_days": days,
        "business_hours_applied": business_hours_applied,
        "calendar_name": calendar_name,
    }

    # ---- volume (opened/closed per day) ----
    opened_by_day: dict[date, int] = {}
    for _id, created_at, _fra, _sla, _prio, _ctype in created_rows:
        d = timezone.localdate(created_at)
        opened_by_day[d] = opened_by_day.get(d, 0) + 1
    closed_by_day: dict[date, int] = {}
    for _id, _created, resolved_at in resolved_rows:
        d = timezone.localdate(resolved_at)
        closed_by_day[d] = closed_by_day.get(d, 0) + 1
    volume = [
        {
            "date": d.isoformat(),
            "opened": opened_by_day.get(d, 0),
            "closed": closed_by_day.get(d, 0),
        }
        for d in _bucket_dates(from_dt, to_dt)
    ]

    # ---- case-type mix (None → "Uncategorized") ----
    type_counts: dict[str, int] = {}
    for _id, _created, _fra, _sla, _prio, case_type in created_rows:
        label = case_type or "Uncategorized"
        type_counts[label] = type_counts.get(label, 0) + 1
    by_type = [
        {"case_type": label, "count": n}
        for label, n in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {
        "totals": totals,
        "volume": volume,
        "first_response": _first_response_by_priority(created_rows, now, org_id),
        "by_type": by_type,
        "by_agent": _agent_table(qs, from_dt, to_dt, now),
    }
