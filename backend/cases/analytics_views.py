"""REST endpoints for the cases analytics dashboard.

The five metric endpoints (FRT, MTTR, Backlog, Agents, SLA) plus a
drilldown JSON endpoint and a streaming CSV export. All accept the same
filter set: `from`, `to`, `team`, `agent`, `priority`. Default window is
the last 30 days.

See `docs/cases/tier2/reporting.md` for the response shapes.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases import analytics
from cases.access import is_org_admin
from cases.analytics import DEFAULT_SERVICE_DAYS
from cases.models import Case
from cases.serializer import CaseSerializer
from common.permissions import HasOrgContext
from common.renderers import CSV_RENDERERS
from common.validators import uuid_param

# ---------------------------------------------------------------------------
# Shared filter parsing


def _parse_dt(
    value: str, field: str, *, end_of_day: bool = False
) -> Optional[datetime]:
    """Accepts YYYY-MM-DD (the org's midnight) or full ISO-8601.

    A date picker sends the day the user pointed at, not an instant. Anchoring
    it to UTC midnight would ask an Indian org about a window that starts at
    05:30 on the chosen day and ends at 05:30 on the day after the one they
    picked, so the answer would silently cover the wrong 24 hours.

    A value that will not parse is refused rather than dropped. Returning
    ``None`` for it fell through to the default last-30-days window, so
    ``?from=banana`` was answered 200 with figures for a period nobody asked
    about, and a client with a broken date format had no way to find out. Every
    other date-valued parameter in this API answers 400 (see
    ``common.validators.date_param``); this one now agrees with them.
    """
    if not value:
        return None
    try:
        # Date-only: anchor to the org's midnight; end_of_day means the
        # exclusive upper bound, so the whole chosen day is inside the window.
        if len(value) == 10 and value.count("-") == 2:
            d = datetime.fromisoformat(value)
            return timezone.make_aware(d + timedelta(days=1) if end_of_day else d)
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            {
                field: [
                    f"'{value}' is not a valid date. "
                    "Use YYYY-MM-DD or an ISO-8601 timestamp."
                ]
            }
        ) from None


def _parse_days(value) -> int:
    """The service window's length, in whole days.

    Absent means the default. A value that is not a whole number is refused for
    the same reason a malformed ``from`` is: silently answering about 14 days
    when asked about ``banana`` looks like an answer. Out-of-range is a
    different case and stays a clamp, which `compute_service_overview`
    documents: 999 days is a legible request for "as much as you have".
    """
    if value in (None, ""):
        return DEFAULT_SERVICE_DAYS
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(
            {"days": [f"'{value}' is not a whole number of days."]}
        ) from None


def _filtered_qs(request) -> tuple[object, datetime, datetime]:
    """Build the org-scoped Case queryset + (from, to) window from query params.

    Visibility: admins see every case; non-admins see only those they
    created, are assigned to, or watch. Mirrors `CaseListView.get_queryset`.
    """
    profile = request.profile
    params = request.query_params

    qs = (
        Case.objects.filter(org=profile.org, is_active=True)
        .filter(merged_into__isnull=True)
        .exclude(status="Duplicate")
    )

    if not is_org_admin(profile):
        qs = qs.filter(
            Q(created_by=profile.user) | Q(assigned_to=profile) | Q(watchers=profile)
        ).distinct()

    if priority := params.get("priority"):
        qs = qs.filter(priority=priority)
    if team_id := uuid_param(params, "team"):
        qs = qs.filter(teams=team_id)
    if agent_id := uuid_param(params, "agent"):
        qs = qs.filter(assigned_to=agent_id)

    from_dt = _parse_dt(params.get("from", ""), "from")
    to_dt = _parse_dt(params.get("to", ""), "to", end_of_day=True)
    # Coerce defaults (last 30 days) consistently with the analytics module.
    coerced_from, coerced_to = analytics._coerce_window(from_dt, to_dt)
    return qs, coerced_from, coerced_to


# ---------------------------------------------------------------------------
# Metric endpoints


_FILTER_PARAMS = [
    OpenApiParameter(
        "from", str, description="Start of window (ISO-8601 or YYYY-MM-DD)"
    ),
    OpenApiParameter("to", str, description="End of window (ISO-8601 or YYYY-MM-DD)"),
    OpenApiParameter("team", str, description="Filter to one team's cases"),
    OpenApiParameter("agent", str, description="Filter to one assignee profile"),
    OpenApiParameter("priority", str, description="Filter by priority"),
]


class _AnalyticsBaseView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)


class AnalyticsFrtView(_AnalyticsBaseView):
    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        return Response(analytics.compute_frt(qs, from_dt, to_dt))


class AnalyticsMttrView(_AnalyticsBaseView):
    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        return Response(analytics.compute_mttr(qs, from_dt, to_dt))


class AnalyticsBacklogView(_AnalyticsBaseView):
    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        return Response(analytics.compute_backlog(qs, from_dt, to_dt))


class AnalyticsAgentsView(_AnalyticsBaseView):
    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        return Response({"results": analytics.compute_agents(qs, from_dt, to_dt)})


class AnalyticsSlaView(_AnalyticsBaseView):
    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        return Response(analytics.compute_sla(qs, from_dt, to_dt))


class AnalyticsServiceView(_AnalyticsBaseView):
    """Admin-only single-call dashboard behind /v2/tickets/analytics.

    Returns org-wide service KPIs (totals, per-day volume, first-response
    attainment by priority, case-type mix, per-agent table). Unlike the
    per-metric endpoints above, which narrow a non-admin to their own cases
    and would therefore show a personal slice under org-wide headings. This
    view is an org-aggregate management surface, so it 403s non-admins outright
    (mirrors the invoices/reports policy for whole-org figures).
    """

    @extend_schema(
        tags=["cases-analytics"],
        parameters=[
            OpenApiParameter(
                "days",
                int,
                description="Window length in whole days (default 14, max 90)",
            )
        ],
    )
    def get(self, request):
        if not is_org_admin(request.profile):
            return Response(
                {"error": True, "errors": "Admin access required"}, status=403
            )
        days = _parse_days(request.query_params.get("days"))
        qs = Case.objects.filter(
            org=request.profile.org, is_active=True, merged_into__isnull=True
        ).exclude(status="Duplicate")
        data = analytics.compute_service_overview(qs, request.profile.org_id, days)
        return Response(data)


# ---------------------------------------------------------------------------
# Drilldown + CSV export


class AnalyticsDrilldownView(_AnalyticsBaseView):
    @extend_schema(
        tags=["cases-analytics"],
        parameters=_FILTER_PARAMS
        + [
            OpenApiParameter("metric", str, required=True),
            OpenApiParameter(
                "bucket", str, description="Bucket selector (metric-specific)"
            ),
        ],
    )
    def get(self, request):
        metric = request.query_params.get("metric", "")
        bucket = request.query_params.get("bucket")
        qs, from_dt, to_dt = _filtered_qs(request)
        try:
            ids = list(
                analytics.case_ids_for_metric(metric, qs, from_dt, to_dt, bucket)
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        cases = Case.objects.filter(org=request.profile.org, id__in=ids).order_by(
            "-created_at"
        )
        data = CaseSerializer(cases, many=True).data
        return Response({"count": len(data), "results": data})


# CSV columns kept tight on purpose: analyst pivots externally.
_CSV_COLUMNS = (
    ("id", "ID"),
    ("name", "Name"),
    ("status", "Status"),
    ("priority", "Priority"),
    ("created_at", "Created"),
    ("first_response_at", "First response"),
    ("resolved_at", "Resolved"),
    ("sla_first_response_hours", "FRT SLA hrs"),
    ("sla_resolution_hours", "Resolution SLA hrs"),
)


class _Echo:
    """File-like target for csv.writer used by StreamingHttpResponse."""

    def write(self, value):
        return value


def _iso_or_blank(value) -> str:
    return value.isoformat() if value else ""


class AnalyticsExportView(_AnalyticsBaseView):
    # This endpoint answers in CSV, so it has to say it accepts CSV: content
    # negotiation runs before the handler, and `src/routes/api/cases/analytics/
    # export/+server.js` sends `Accept: text/csv`, which was answered 406.
    renderer_classes = CSV_RENDERERS

    @extend_schema(
        tags=["cases-analytics"],
        parameters=_FILTER_PARAMS
        + [
            OpenApiParameter("metric", str, required=True),
            OpenApiParameter("bucket", str),
            OpenApiParameter(
                "fmt",
                str,
                description="csv (only). Named `fmt` to avoid colliding with DRF's "
                "`?format=` content-negotiation kwarg, which would 404 the endpoint.",
            ),
        ],
    )
    def get(self, request):
        metric = request.query_params.get("metric", "")
        bucket = request.query_params.get("bucket")
        fmt = request.query_params.get("fmt", "csv")
        if fmt != "csv":
            return Response({"error": "only fmt=csv is supported"}, status=400)

        qs, from_dt, to_dt = _filtered_qs(request)
        try:
            ids_iter = analytics.case_ids_for_metric(metric, qs, from_dt, to_dt, bucket)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)

        ids = [UUID(str(cid)) for cid in ids_iter]
        cases_qs = (
            Case.objects.filter(org=request.profile.org, id__in=ids)
            .order_by("-created_at")
            .iterator(chunk_size=200)
        )
        writer = csv.writer(_Echo())

        def stream():
            yield writer.writerow([label for _key, label in _CSV_COLUMNS])
            for c in cases_qs:
                row = [
                    str(c.id),
                    c.name,
                    c.status,
                    c.priority,
                    _iso_or_blank(c.created_at),
                    _iso_or_blank(c.first_response_at),
                    _iso_or_blank(c.resolved_at),
                    c.sla_first_response_hours,
                    c.sla_resolution_hours,
                ]
                yield writer.writerow(row)

        response = StreamingHttpResponse(stream(), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="cases-{metric}-{bucket or "all"}.csv"'
        )
        return response
