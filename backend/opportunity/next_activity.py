"""A deal's next activity: its earliest open task, among the tasks the caller can open.

Shown on the deal list and the deal board so a deal with nothing scheduled
stands out. Only tasks the caller may read are considered, through
`tasks.access.visible_tasks_qs`, the rule the task list and detail use. A
deal is readable by people who cannot open every task on it, and counting a
hidden task would put its title and due date on their card.

It is returned for every deal, closed ones included, but "no next step" is a
flag only on an open deal: a won or lost deal has no next step to take. The
clients decide that from `stage_kind`, which travels in the same payload and
is derived from the deal's own pipeline stage, so neither re-derives it from
the stage code.

"Open" is every status but Completed, the line the task list's totals draw.
Earliest means the soonest due date, undated tasks after dated ones, then the
oldest task, then the id, so a tie still has one answer.

Each field is its own correlated subquery with that same ordering, so joining
the tasks never multiplies the deal rows. They are computed only for the deals
a response actually serializes (`attach_next_activity`, one query for a page
or a whole board), never on the queryset a paginator or a column counts: a
COUNT(*) over an annotated queryset would run all three subqueries for every
visible deal. The subqueries select from `Task` by id rather than from
`visible_tasks_qs` directly: for a non-admin that queryset is DISTINCT, and a
DISTINCT subquery ordered by a column it does not select is refused by
Postgres.
"""

from django.db.models import DateField, F, OuterRef, Subquery, UUIDField
from rest_framework import serializers

from opportunity.models import Opportunity
from tasks.access import visible_tasks_qs
from tasks.models import Task

DONE = "Completed"


def annotate_next_activity(queryset, profile):
    """``queryset`` of deals with ``next_activity_id``, ``_title`` and ``_due``.

    All three are ``None`` on a deal with no open task the caller can open.
    """
    tasks = (
        Task.objects.filter(
            org=profile.org,
            opportunity=OuterRef("pk"),
            id__in=visible_tasks_qs(profile).values("id"),
        )
        .exclude(status=DONE)
        .order_by(F("due_date").asc(nulls_last=True), "created_at", "id")
    )
    return queryset.annotate(
        next_activity_id=Subquery(tasks.values("id")[:1], output_field=UUIDField()),
        next_activity_title=Subquery(tasks.values("title")[:1]),
        next_activity_due=Subquery(
            tasks.values("due_date")[:1], output_field=DateField()
        ),
    )


def attach_next_activity(deals, profile):
    """Set ``next_activity_id``, ``_title`` and ``_due`` on each deal in ``deals``.

    ``deals`` is an evaluated page or board, not a queryset: one query for all
    of them, keyed by id, so the serializer reads the same attributes as it
    would from an annotated row and nothing was counted with them.
    """
    deals = list(deals)
    rows = {
        row["pk"]: row
        for row in annotate_next_activity(
            Opportunity.objects.filter(
                org=profile.org, pk__in=[deal.pk for deal in deals]
            ),
            profile,
        ).values("pk", "next_activity_id", "next_activity_title", "next_activity_due")
    }
    for deal in deals:
        row = rows.get(deal.pk, {})
        deal.next_activity_id = row.get("next_activity_id")
        deal.next_activity_title = row.get("next_activity_title")
        deal.next_activity_due = row.get("next_activity_due")
    return deals


class NextActivitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    due_date = serializers.DateField(allow_null=True)


def next_activity(deal):
    """The annotated next activity as a dict, or ``None`` when the deal has none.

    Reads the attributes directly: a deal that was not passed through
    `attach_next_activity` raises here rather than reporting "nothing
    scheduled" for a deal nobody checked.
    """
    if deal.next_activity_id is None:
        return None
    return NextActivitySerializer(
        {
            "id": deal.next_activity_id,
            "title": deal.next_activity_title,
            "due_date": deal.next_activity_due,
        }
    ).data
