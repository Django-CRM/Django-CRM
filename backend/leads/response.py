"""Who is waiting for an answer, and how fast each person answers.

Both read the fields `interactions.service` keeps on each lead
(`first_response_at`, `first_response_by`, `last_outbound_at`) and the org's
`lead_response_since`, the moment it started measuring. The rules are spelled
out in `docs/api/leads.md` ("Unanswered leads", "Response times").
"""

from datetime import datetime, time, timedelta
from statistics import median

from django.db.models import (
    BooleanField,
    Case,
    Count,
    DateTimeField,
    F,
    Min,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from leads.models import Lead

#: A lead can wait for an answer only while it is open.
WAITING_STATUSES = ("assigned", "in process", "recycled")

FIVE_MINUTES = timedelta(minutes=5)
ONE_HOUR = timedelta(hours=1)


def _waiting(org):
    """The unanswered rule as one condition, over the `arrival_at` annotation."""
    return (
        Q(status__in=WAITING_STATUSES)
        & (Q(last_outbound_at__isnull=True) | Q(last_outbound_at__lt=F("arrival_at")))
        & Q(arrival_at__gte=org.lead_response_since)
    )


def with_response_state(queryset, org):
    """``queryset``, narrowed to ``org``, with three annotations per lead.

    ``arrival_at`` is the latest enquiry, else when the lead was created.
    ``is_unanswered`` is the rule in ``_waiting``. ``waiting_since`` is null
    unless the lead is unanswered; a lead nobody ever answered waits from its
    creation, unless it predates the stamp, when it waits from the enquiry
    that brought it back.
    """
    waiting = _waiting(org)
    return (
        queryset.filter(org=org)
        .annotate(arrival_at=Coalesce("last_enquiry_at", "created_at"))
        .annotate(
            is_unanswered=Case(
                When(waiting, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            waiting_since=Case(
                When(
                    waiting
                    & Q(
                        last_outbound_at__isnull=True,
                        created_at__gte=org.lead_response_since,
                    ),
                    then=F("created_at"),
                ),
                When(waiting, then=F("arrival_at")),
                output_field=DateTimeField(),
            ),
        )
    )


def unanswered(queryset, org):
    """The unanswered leads in ``queryset``, annotated as above."""
    return with_response_state(queryset, org).filter(_waiting(org))


def unanswered_summary(queryset, org):
    """``{"count", "oldest_since"}`` over the unanswered leads in ``queryset``,
    which the caller has already narrowed to what the viewer may see."""
    return unanswered(queryset, org).aggregate(
        count=Count("pk"), oldest_since=Min("waiting_since")
    )


def _median_in_python(
    rows, key="first_response_by", start="created_at", end="first_response_at"
):
    """The median of ``end - start`` for each ``key`` in ``rows``, by default
    each person's response time: the middle value, or the mean of the middle
    two. In Python on every database, as `cases.analytics` does, because SQLite
    has no ``percentile_cont``."""
    times = {}
    for group, began, ended in rows.order_by().values_list(key, start, end):
        times.setdefault(group, []).append(ended - began)
    return {group: median(values) for group, values in times.items()}


def window(days):
    """The first day of the last ``days`` days of the org's calendar, today
    included, and the moment that day starts."""
    since = timezone.localdate() - timedelta(days=days - 1)
    return since, datetime.combine(
        since, time.min, tzinfo=timezone.get_current_timezone()
    )


def _percent(part, whole):
    # Rounded down, so 100 means every one of them.
    return part * 100 // whole if whole else 0


def response_times(org, profiles, days):
    """One row per profile in ``profiles`` (all of them members of ``org``)
    over the last ``days`` days of the org's calendar, today included.

    A row counts the leads its person answered first inside the window that
    arrived at or after ``org.lead_response_since``: response times of leads
    from before the org started measuring would count years of unlogged
    history. ``unanswered_now`` counts the person's assigned leads that are
    unanswered right now.
    """
    since, start = window(days)
    profile_ids = [profile.id for profile in profiles]

    answered = Lead.objects.filter(
        org=org,
        first_response_by__in=profile_ids,
        first_response_at__gte=start,
        created_at__gte=org.lead_response_since,
    )
    counts = {
        row["first_response_by"]: row
        for row in answered.order_by()
        .values("first_response_by")
        .annotate(
            answered=Count("pk"),
            within_5_minutes=Count(
                "pk", filter=Q(first_response_at__lte=F("created_at") + FIVE_MINUTES)
            ),
            within_1_hour=Count(
                "pk", filter=Q(first_response_at__lte=F("created_at") + ONE_HOUR)
            ),
        )
    }
    medians = _median_in_python(answered) if counts else {}
    # From the M2M's own table, so a lead with several assignees counts once
    # for each of them and the join cannot multiply anything.
    waiting = dict(
        Lead.assigned_to.through.objects.filter(
            profile_id__in=profile_ids,
            lead_id__in=unanswered(Lead.objects.all(), org).values("pk"),
        )
        .values("profile_id")
        .annotate(n=Count("lead_id"))
        .values_list("profile_id", "n")
    )

    rows = []
    for profile in profiles:
        count = counts.get(profile.id, {})
        answered_count = count.get("answered", 0)
        middle = medians.get(profile.id)
        rows.append(
            {
                "profile": str(profile.id),
                "name": profile.user.name or profile.user.email,
                "answered": answered_count,
                "median_seconds": (
                    None if middle is None else int(middle.total_seconds())
                ),
                "within_5_minutes": _percent(
                    count.get("within_5_minutes", 0), answered_count
                ),
                "within_1_hour": _percent(
                    count.get("within_1_hour", 0), answered_count
                ),
                "unanswered_now": waiting.get(profile.id, 0),
            }
        )
    rows.sort(key=lambda row: (row["name"].casefold(), row["profile"]))
    return {"since": since, "rows": rows}
