from datetime import timedelta

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import visible_accounts_qs
from accounts.models import Account
from cases.access import visible_cases_qs, writable_cases_qs
from cases.models import Case
from common import serializer, swagger_params
from common.models import Activity
from common.permissions import HasOrgContext, is_org_admin
from contacts.access import visible_contacts_qs
from contacts.models import Contact
from invoices.models import UNPAID_STATUSES, Invoice
from invoices.permissions import visible_invoices_qs
from leads.access import visible_leads_qs
from leads.models import Lead
from opportunity.access import visible_deals_qs
from opportunity.models import DealPipeline, Opportunity, stage_kind_q
from opportunity.stages import aging_q, stage_index
from opportunity.workflow import OPEN, WON
from tasks.access import visible_tasks_qs
from tasks.models import Task
from tasks.serializer import TaskSerializer

# Case statuses that are still open. The rest (Closed, Rejected, Duplicate) are
# terminal; listing the open ones explicitly means a new terminal status is a
# deliberate edit here, not a silent inclusion in the "needs a reply" queue.
OPEN_CASE_STATUSES = ["New", "Assigned", "Pending"]

# How many rows the Today queue shows, and how many each of its four sources
# contributes before ranking. Neither is a count of anything: `summary.count`
# is derived from the sources themselves, so raising or lowering these changes
# what is displayed and never what the header claims.
TODAY_QUEUE_LIMIT = 8
TODAY_SOURCE_LIMIT = 25

_CURRENCY_SYMBOL = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "AUD": "A$",
    "CAD": "C$",
}


def _fmt_money(amount, currency):
    """One-line money for a human sentence, e.g. '$42,000'. Falls back to
    'CODE 42,000' for currencies without a symbol. Formatting lives here only
    because the Today queue ships each row as one prebuilt line; every other
    endpoint returns structured numbers and lets the client format them."""
    n = float(amount or 0)
    sym = _CURRENCY_SYMBOL.get((currency or "").upper())
    return f"{sym}{n:,.0f}" if sym else f"{(currency or '').upper()} {n:,.0f}".strip()


def _fmt_date(d):
    """'Jul 5'. Portable (avoids the platform-specific %-d)."""
    return f"{d:%b} {d.day}"


def _readable(queryset, visible):
    """Narrow ``queryset`` to the rows in ``visible``, without joining.

    ``visible`` is the module's own read rule (``visible_accounts_qs`` and its
    siblings), so every count and total here matches what the caller's list
    shows and what the detail view opens. It is applied by id: filtering
    straight onto the ``assigned_to`` M2M multiplies rows, a record carrying
    three assignees comes back three times, which inflates ``.count()`` and,
    worse, ``Sum()``. Resolving the ids in a subquery keeps the outer query at
    one row per record, which is what the counts and the aggregates assume.
    """
    return queryset.filter(pk__in=visible.values("pk"))


# The read rule for each `Activity.entity_type` a feed may name. An activity
# carries the record's name and what was done to it, so showing one is showing
# a slice of that record: it has to pass the same rule the record's own detail
# view applies. Types without an entry here (Event, Document, Team: nothing
# writes them today) have no rule to pass, so a member never sees them.
_ACTIVITY_READ_RULES = {
    "Account": lambda profile, user: visible_accounts_qs(profile, user),
    "Lead": lambda profile, user: visible_leads_qs(profile, user),
    "Contact": lambda profile, user: visible_contacts_qs(profile),
    "Opportunity": lambda profile, user: visible_deals_qs(profile, user),
    "Case": lambda profile, user: visible_cases_qs(profile),
    "Task": lambda profile, user: visible_tasks_qs(profile),
    "Invoice": lambda profile, user: visible_invoices_qs(profile, user),
}


def _readable_activities(profile, user):
    """The org's activities that ``profile`` may see: one query, whatever the size.

    An admin sees the whole org's feed, as before. Anyone else sees an activity
    only while its record is one they can open, resolved as one id subquery per
    entity type, so the cost does not grow with the number of rows returned.
    That fails closed twice: an entity type with no read rule is hidden, and so
    is an activity whose record has since been deleted, because no read rule
    can match a row that no longer exists.
    """
    qs = Activity.objects.filter(org=profile.org)
    if is_org_admin(profile):
        return qs
    readable = Q()
    for entity_type, rule in _ACTIVITY_READ_RULES.items():
        readable |= Q(
            entity_type=entity_type,
            entity_id__in=rule(profile, user).values("pk"),
        )
    return qs.filter(readable)


class ApiHomeView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["home"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ApiHomeResponse",
                fields={
                    "accounts_count": serializers.IntegerField(),
                    "contacts_count": serializers.IntegerField(),
                    "leads_count": serializers.IntegerField(),
                    "opportunities_count": serializers.IntegerField(),
                    "urgent_counts": serializers.DictField(),
                    "pipeline_by_stage": serializers.DictField(),
                    "revenue_metrics": serializers.DictField(),
                    "hot_leads": serializers.ListField(),
                    "tasks": TaskSerializer(many=True),
                    "activities": serializer.DashboardActivitySerializer(many=True),
                    "goal_summary": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, format=None):
        org = request.profile.org
        profile = request.profile
        today = timezone.localdate()

        user = request.user

        # Each one is its module's read rule, so every figure below counts the
        # rows the matching list shows. They differ on purpose: contacts add
        # account assignment, and tasks have no superuser clause.
        accounts = _readable(
            Account.objects.filter(is_active=True, org=org),
            visible_accounts_qs(profile, user),
        )
        contacts = _readable(
            Contact.objects.filter(org=org), visible_contacts_qs(profile)
        )
        # Kept separate from `leads` because the conversion rate below needs
        # converted leads, which `leads` deliberately excludes.
        all_leads = _readable(
            Lead.objects.filter(org=org), visible_leads_qs(profile, user)
        )
        leads = all_leads.exclude(Q(status="converted") | Q(status="closed"))
        opportunities = _readable(
            Opportunity.objects.filter(org=org), visible_deals_qs(profile, user)
        )
        tasks = _readable(Task.objects.filter(org=org), visible_tasks_qs(profile))

        # Decides only whether the goal summary below includes org-wide goals.
        is_admin = is_org_admin(profile) or user.is_superuser

        # Counts only. This used to serialize every account, contact, lead and
        # opportunity in the org in full beside them: 372 KB of a 384 KB
        # response, measured against the seeded org, none of which any caller
        # read. The screens below want counts, the pipeline, the urgent numbers,
        # ten hot leads and ten tasks. Whoever needs a list calls its own
        # endpoint, which pages; these four never did.
        context = {}
        context["accounts_count"] = accounts.count()
        context["contacts_count"] = contacts.count()
        context["leads_count"] = leads.count()
        context["opportunities_count"] = opportunities.count()

        # NEW: Urgent counts for Focus Bar
        overdue_tasks = tasks.filter(
            status__in=["New", "In Progress"], due_date__lt=today
        ).count()

        tasks_due_today = tasks.filter(
            status__in=["New", "In Progress"], due_date=today
        ).count()

        followups_today = leads.filter(next_follow_up=today).count()

        hot_leads = leads.filter(
            rating="HOT", status__in=["assigned", "in process"]
        ).count()

        context["urgent_counts"] = {
            "overdue_tasks": overdue_tasks,
            "tasks_due_today": tasks_due_today,
            "followups_today": followups_today,
            "hot_leads": hot_leads,
        }

        # Get org's default currency for filtering
        org_currency = org.default_currency or "USD"

        # Pipeline by stage for the default pipeline (filtered by org's default
        # currency). Keyed by stage code as it always was; `kind` and `order`
        # let a client read the board's meaning and sequence instead of
        # assuming today's six codes. One grouped query, not two per stage.
        in_org_currency = (
            Q(currency=org_currency) | Q(currency__isnull=True) | Q(currency="")
        )
        default_pipeline = DealPipeline.default_for(org)
        grouped = {
            row["stage"]: row
            for row in opportunities.filter(pipeline=default_pipeline)
            .values("stage")
            .annotate(
                count=Count("id"),
                # Value only for deals in the org's currency.
                value=Coalesce(
                    Sum("amount", filter=in_org_currency),
                    0,
                    output_field=DecimalField(),
                ),
            )
            .order_by()
        }
        context["pipeline_by_stage"] = {
            stage.code: {
                "count": grouped.get(stage.code, {}).get("count", 0),
                "value": float(grouped.get(stage.code, {}).get("value") or 0),
                "label": stage.label,
                "kind": stage.kind,
                "order": stage.order,
            }
            for stage in default_pipeline.stages.all()
        }

        # Revenue metrics (filtered by org's default currency), across every
        # pipeline: open and won are the stage's kind, whatever it is called.
        open_opps = opportunities.filter(stage_kind_q(OPEN))
        # Filter by currency for value calculations
        open_opps_with_currency = open_opps.filter(in_org_currency)

        pipeline_value = open_opps_with_currency.aggregate(
            total=Coalesce(Sum("amount"), 0, output_field=DecimalField())
        )["total"]

        # Weighted pipeline = sum of (amount * probability / 100)
        weighted_pipeline = open_opps_with_currency.aggregate(
            total=Coalesce(
                Sum(F("amount") * F("probability") / 100),
                0,
                output_field=DecimalField(),
            )
        )["total"]

        # Won this month (use timezone-aware datetime for updated_at comparison)
        now = timezone.now()
        first_day_of_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        won_opps = opportunities.filter(
            stage_kind_q(WON), updated_at__gte=first_day_of_month
        )
        won_opps_with_currency = won_opps.filter(in_org_currency)
        won_this_month = won_opps_with_currency.aggregate(
            total=Coalesce(Sum("amount"), 0, output_field=DecimalField())
        )["total"]

        # Conversion rate over the caller's own leads. It used to query
        # `Lead.objects.filter(org=org)` directly, which skipped the narrowing
        # above and printed an org-wide percentage beside a member's own lead
        # count, on the same row of the same card.
        total_leads_all = all_leads.count()
        converted_leads = all_leads.filter(status="converted").count()
        conversion_rate = (
            (converted_leads / total_leads_all * 100) if total_leads_all > 0 else 0
        )

        # Count opportunities in other currencies (for info)
        other_currency_count = opportunities.exclude(in_org_currency).count()

        context["revenue_metrics"] = {
            "pipeline_value": float(pipeline_value or 0),
            # How many deals that pipeline value is made of. Counted from
            # `open_opps` rather than the currency-filtered set, because a deal
            # in another currency is still an open deal. `opportunities_count`
            # above counts every stage, closed ones included, so it is not the
            # number to put under an "Open Deals" label.
            "open_opportunities_count": open_opps.count(),
            "weighted_pipeline": float(weighted_pipeline or 0),
            "won_this_month": float(won_this_month or 0),
            "conversion_rate": round(conversion_rate, 1),
            "currency": org_currency,
            "other_currency_count": other_currency_count,
        }

        # NEW: Hot leads list for dedicated panel
        hot_leads_qs = leads.filter(
            rating="HOT", status__in=["assigned", "in process"]
        ).order_by("-created_at")[:10]

        context["hot_leads"] = [
            {
                "id": str(lead.id),
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "company": lead.company_name,
                "rating": lead.rating,
                "next_follow_up": (
                    lead.next_follow_up.isoformat() if lead.next_follow_up else None
                ),
                "last_contacted": (
                    lead.last_contacted.isoformat() if lead.last_contacted else None
                ),
            }
            for lead in hot_leads_qs
        ]

        # Include tasks in dashboard response (avoid separate API call)
        upcoming_tasks = tasks.filter(
            status__in=["New", "In Progress"], due_date__isnull=False
        ).order_by("due_date")[:10]
        context["tasks"] = TaskSerializer(upcoming_tasks, many=True).data

        # Goal summary for current user
        from opportunity.models import SalesGoal

        goal_filter = Q(assigned_to=profile) | Q(team__in=profile.user_teams.all())
        if is_admin:
            goal_filter |= Q(assigned_to__isnull=True, team__isnull=True)

        active_goals = SalesGoal.attach_progress(
            SalesGoal.objects.filter(
                org=org,
                is_active=True,
                period_start__lte=today,
                period_end__gte=today,
            )
            .filter(goal_filter)
            .select_related("assigned_to", "team")
            .distinct()[:3]
        )
        context["goal_summary"] = [
            {
                "id": str(g.id),
                "name": g.name,
                "goal_type": g.goal_type,
                "currency": g.currency,
                "target_value": float(g.target_value),
                "progress_value": float(g.compute_progress()),
                "progress_percent": g.progress_percent,
                "status": g.status,
            }
            for g in active_goals
        ]

        # Recent activities, narrowed like every figure above: a member sees
        # activity only on records they can open.
        activities = (
            _readable_activities(profile, user)
            .select_related("user", "user__user")
            .order_by("-created_at")[:10]
        )
        context["activities"] = serializer.DashboardActivitySerializer(
            activities, many=True
        ).data

        return Response(context, status=status.HTTP_200_OK)


class ApiTodayView(APIView):
    """The v2 home ("Today"): one prioritised, cross-model action queue.

    This is NOT the KPI dashboard (that is ``ApiHomeView`` at
    ``/api/dashboard/``). It answers "what wants me right now" by folding four
    sources into a single ranked list:

      * support cases still awaiting a first response (SLA-breached first),
      * invoices past their due date and still unpaid,
      * open deals that have gone quiet (stage-aging yellow/red),
      * tasks overdue or due today.

    Security: every query is org-scoped. The org comes from the JWT via
    middleware, never the client, and each source is narrowed by its own
    module's read rule, as ``ApiHomeView`` does, so the queue never offers a
    row the caller cannot open. Every row also carries an action, so each
    source is one the caller can act on: opening a deal or a task, and sending
    an invoice, need nothing beyond reading it, but replying to a ticket needs
    the write rule, which leaves watchers out. A ticket someone only watches
    is therefore not in the queue, nor in its count. ``HasOrgContext``
    guarantees ``request.profile``/``org`` are set, so this never dereferences
    a ``None`` profile.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["home"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="ApiTodayResponse",
                fields={
                    "queue": serializers.ListField(child=serializers.DictField()),
                    "summary": inline_serializer(
                        name="ApiTodaySummary",
                        fields={
                            "count": serializers.IntegerField(),
                            "shown": serializers.IntegerField(),
                            "sources": serializers.ListField(
                                child=serializers.DictField()
                            ),
                            "quiet_deals": serializers.IntegerField(),
                            "quiet_value": serializers.FloatField(),
                            "cleared_yesterday": serializers.IntegerField(),
                        },
                    ),
                    "later": serializers.ListField(child=serializers.DictField()),
                },
            )
        },
    )
    def get(self, request, format=None):
        org = request.profile.org
        profile = request.profile
        now = timezone.now()
        # The org's calendar day, not UTC's. `now.date()` put the whole "Today"
        # queue a day behind for every hour that TIME_ZONE is ahead of UTC.
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        week_end = today + timedelta(days=7)
        org_currency = org.default_currency or "USD"

        user = request.user
        # Each source is narrowed by its module's read rule, so a row here is
        # one the caller can open and a count here matches the list it links
        # to. Resolved once, reused by the queue, the summary and "later".
        visible_deals = visible_deals_qs(profile, user)
        visible_cases = visible_cases_qs(profile)
        visible_invoices = visible_invoices_qs(profile, user)
        visible_tasks = visible_tasks_qs(profile)

        # ── base, org-scoped querysets ──────────────────────────────────────
        opportunities = _readable(
            Opportunity.objects.filter(stage_kind_q(OPEN), org=org), visible_deals
        )
        # The one source whose action needs more than reading: "Reply" is a
        # write, so a ticket the caller only watches would be a dead button.
        cases = _readable(
            Case.objects.filter(org=org, status__in=OPEN_CASE_STATUSES),
            writable_cases_qs(profile),
        )
        invoices = _readable(
            Invoice.objects.filter(
                org=org, status__in=UNPAID_STATUSES, due_date__lt=today
            ),
            visible_invoices,
        )
        tasks = _readable(
            Task.objects.filter(
                org=org, status__in=["New", "In Progress"], due_date__lte=today
            ),
            visible_tasks,
        )

        # ── deal aging as DB date cutoffs (no per-row Python) ───────────────
        # "Quiet" (yellow+) once a deal has sat past its stage's yellow
        # threshold, "rotten" (red) past its red one: the thresholds
        # `get_aging_status` uses, as stage_changed_at cutoffs, so this is a
        # filter, not a scan. Closed stages have no clauses.
        stages = stage_index(org.id)
        quiet_opps = opportunities.filter(aging_q(stages.values(), "yellow", now))
        quiet_deals = quiet_opps.count()
        quiet_value = quiet_opps.filter(
            Q(currency=org_currency) | Q(currency__isnull=True) | Q(currency="")
        ).aggregate(total=Coalesce(Sum("amount"), 0, output_field=DecimalField()))[
            "total"
        ]

        # ── build the queue (each source pre-ordered by urgency, capped) ────
        queue = []
        awaiting_cases = cases.filter(first_response_at__isnull=True)

        # 1. Cases awaiting a first response. SLA-breached outrank in-SLA ones;
        #    High/Urgent priority render with the alarm tone.
        for c in awaiting_cases.select_related("account").order_by("created_at")[
            :TODAY_SOURCE_LIMIT
        ]:
            deadline = c.created_at + timedelta(hours=c.sla_first_response_hours or 4)
            breached = deadline < now
            hot = c.priority in ("High", "Urgent")
            queue.append(
                {
                    "_rank": 0 if breached else 3,
                    "id": f"case-{c.id}",
                    "tone": "rust" if (hot or breached) else "clay",
                    "due": "Overdue" if breached else "Today",
                    "title": c.name,
                    "detail": f"{c.priority} · {c.account.name if c.account_id else 'No account'} · awaiting first reply",
                    "action": "Reply",
                    "href": f"/tickets/{c.id}",
                }
            )

        # 2. Overdue invoices.
        for inv in invoices.select_related("account").order_by("due_date")[
            :TODAY_SOURCE_LIMIT
        ]:
            queue.append(
                {
                    "_rank": 1,
                    "id": f"invoice-{inv.id}",
                    "tone": "clay",
                    "due": "Overdue",
                    "title": inv.invoice_title or inv.invoice_number,
                    "detail": f"{_fmt_money(inv.total_amount, inv.currency)} · {inv.account.name if inv.account_id else 'No account'} · due {_fmt_date(inv.due_date)}",
                    "action": "Send a reminder",
                    "href": f"/invoices/{inv.id}",
                }
            )

        # 3. Quiet deals (aging). Rotten (red) outrank merely slowing (yellow).
        def stage_label(opp):
            stage = opp.current_stage(stages)
            return stage.label if stage else opp.stage

        for opp in quiet_opps.order_by("stage_changed_at")[:TODAY_SOURCE_LIMIT]:
            rotten = opp.get_aging_status(stages) == "red"
            days = (now - opp.stage_changed_at).days if opp.stage_changed_at else 0
            queue.append(
                {
                    "_rank": 2 if rotten else 6,
                    "id": f"deal-{opp.id}",
                    "tone": "rust" if rotten else "clay",
                    "due": "Stalled" if rotten else "Aging",
                    "title": opp.name,
                    "detail": f"No movement for {days} days · {_fmt_money(opp.amount, opp.currency)} · {stage_label(opp)}",
                    "action": "Open the deal",
                    "href": f"/pipeline/{opp.id}",
                }
            )

        # 4. Tasks overdue or due today.
        for t in tasks.order_by("due_date")[:TODAY_SOURCE_LIMIT]:
            overdue = t.due_date is not None and t.due_date < today
            queue.append(
                {
                    "_rank": 4 if overdue else 5,
                    "id": f"task-{t.id}",
                    "tone": "clay" if overdue else "slate",
                    "due": "Overdue" if overdue else "Today",
                    "title": t.title,
                    "detail": (
                        f"Due {_fmt_date(t.due_date)} · {t.priority}"
                        if t.due_date
                        else t.priority
                    ),
                    "action": "Open the task",
                    "href": f"/tasks/{t.id}",
                }
            )

        queue.sort(key=lambda item: item["_rank"])
        queue = [
            {k: v for k, v in item.items() if k != "_rank"}
            for item in queue[:TODAY_QUEUE_LIMIT]
        ]

        # ── the count, and where the rows that did not fit have gone ────────
        # `len(queue)` was a third number that matched nothing on screen: it is
        # taken after the per-source cap and before the queue cap, so the header
        # could claim 42 over 8 rows, and an org past the source cap would get
        # neither its true total nor its visible one. Count each source instead,
        # and hand the page a per-source breakdown so the overflow has somewhere
        # to go. The hrefs are literals built here, never stored values.
        sources = [
            {
                "label": "tickets awaiting a reply",
                "count": awaiting_cases.count(),
                "href": "/tickets",
            },
            {
                "label": "overdue invoices",
                "count": invoices.count(),
                "href": "/invoices",
            },
            {"label": "quiet deals", "count": quiet_deals, "href": "/pipeline"},
            {"label": "tasks due", "count": tasks.count(), "href": "/tasks"},
        ]
        sources = [s for s in sources if s["count"]]
        total_urgent = sum(s["count"] for s in sources)

        # ── "cleared yesterday" (a morale line) ─────────────────────────────
        # Tasks have no completed_at, so proxy with "marked Completed and last
        # touched yesterday"; cases carry a real closed_on date.
        cleared_tasks = _readable(
            Task.objects.filter(
                org=org, status="Completed", updated_at__date=yesterday
            ),
            visible_tasks,
        )
        cleared_cases = _readable(
            Case.objects.filter(org=org, status="Closed", closed_on=yesterday),
            visible_cases,
        )

        summary = {
            "count": total_urgent,
            "shown": len(queue),
            "sources": sources,
            "quiet_deals": quiet_deals,
            "quiet_value": float(quiet_value or 0),
            "cleared_yesterday": cleared_tasks.count() + cleared_cases.count(),
        }

        # ── "later this week" (due tomorrow … +7 days) ──────────────────────
        soon = Q(due_date__gt=today, due_date__lte=week_end)
        later_tasks = _readable(
            Task.objects.filter(org=org, status__in=["New", "In Progress"]).filter(
                soon
            ),
            visible_tasks,
        )
        later_opps = _readable(
            Opportunity.objects.filter(
                stage_kind_q(OPEN),
                org=org,
                closed_on__gt=today,
                closed_on__lte=week_end,
            ),
            visible_deals,
        )
        later_invoices = _readable(
            Invoice.objects.filter(
                org=org,
                status__in=UNPAID_STATUSES,
                due_date__gt=today,
                due_date__lte=week_end,
            ),
            visible_invoices,
        )

        later_rows = []
        for t in later_tasks.order_by("due_date")[:10]:
            later_rows.append(
                (
                    t.due_date,
                    {
                        "id": f"task-{t.id}",
                        "day": t.due_date.strftime("%a"),
                        "title": t.title,
                        "meta": f"Task · {t.priority}",
                    },
                )
            )
        for o in later_opps.order_by("closed_on")[:10]:
            later_rows.append(
                (
                    o.closed_on,
                    {
                        "id": f"deal-{o.id}",
                        "day": o.closed_on.strftime("%a"),
                        "title": f"{o.name} expected to close",
                        "meta": f"{stage_label(o)} · {_fmt_money(o.amount, o.currency)}",
                    },
                )
            )
        for inv in later_invoices.order_by("due_date")[:10]:
            later_rows.append(
                (
                    inv.due_date,
                    {
                        "id": f"invoice-{inv.id}",
                        "day": inv.due_date.strftime("%a"),
                        "title": f"{inv.invoice_title or inv.invoice_number} due",
                        "meta": _fmt_money(inv.total_amount, inv.currency),
                    },
                )
            )
        later_rows.sort(key=lambda r: r[0])
        later = [row for _, row in later_rows[:6]]

        return Response(
            {"queue": queue, "summary": summary, "later": later},
            status=status.HTTP_200_OK,
        )


class ActivityListView(APIView):
    """Recent activities the caller may see, newest first, 10 by default.

    Scoped the way the dashboard's feed is (`_readable_activities`): an admin
    sees the org, a member sees activity on records they can open.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["activities"],
        parameters=swagger_params.organization_params
        + [
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Number of activities to return (default: 10, max: 50)",
            ),
            OpenApiParameter(
                name="entity_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by entity type (Account, Lead, Contact, etc.)",
            ),
        ],
        responses={200: serializer.DashboardActivitySerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        # A malformed or negative `limit` used to reach `int()` and the slice
        # unguarded, a 500 either way. Anything unusable is the default.
        try:
            limit = int(request.query_params.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        if limit < 1:
            limit = 10
        limit = min(limit, 50)
        entity_type = request.query_params.get("entity_type", None)

        queryset = _readable_activities(request.profile, request.user)

        # Filter by entity type if specified
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)

        # Get most recent activities
        activities = queryset.select_related("user", "user__user")[:limit]

        # Serialize
        activities_data = serializer.DashboardActivitySerializer(
            activities, many=True
        ).data

        return Response(
            {
                "error": False,
                "count": len(activities_data),
                "activities": activities_data,
            },
            status=status.HTTP_200_OK,
        )
