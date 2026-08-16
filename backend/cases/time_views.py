"""
Time-tracking endpoints (Tier 3 time-tracking).

Case-scoped:

* ``GET  /api/cases/<pk>/time-entries/``: list (visible to actor)
* ``POST /api/cases/<pk>/time-entries/``: manual entry
* ``POST /api/cases/<pk>/time-entries/start/``: start a running timer (409 if one active)
* ``GET  /api/cases/<pk>/time-summary/``: totals + by-profile breakdown

Entry-scoped (registered at the project root under ``/api/time-entries/``):

* ``POST   /api/time-entries/<pk>/stop/``: stop a running timer
* ``PUT    /api/time-entries/<pk>/``: owner or admin
* ``DELETE /api/time-entries/<pk>/``: owner or admin
* ``GET    /api/time-entries/timesheet/``: week view, grouped by day
* ``GET    /api/time-entries/report/``: totals by agent, ticket or account
* ``GET    /api/time-entries/report/export/``: the same window as ``text/csv``
"""

import csv
from collections import OrderedDict
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases import time_reports

# The file-like shim `csv.writer` needs to stream instead of buffer. Imported
# rather than copied: `cases/analytics_views.py` already exports its CSV the
# same way, and two of these would be two places to fix a streaming bug.
from cases.analytics_views import _Echo
from cases.models import Case, TimeEntry
from cases.serializer import (
    TimeEntryCreateSerializer,
    TimeEntrySerializer,
    TimeEntryUpdateSerializer,
)
from cases.time_reports import TimeReportParamError
from common.models import Profile
from common.permissions import HasOrgContext, is_org_admin
from common.renderers import CSV_RENDERERS
from common.validators import uuid_param

# How far back a report reaches when it is asked for without a window. Long
# enough to cover "what did we do this month", short enough that a stray click
# does not scan a year.
DEFAULT_REPORT_DAYS = 30


def _visible_entry_qs(profile):
    """Org-scoped queryset honouring agent-vs-admin visibility."""
    qs = TimeEntry.objects.filter(org=profile.org)
    if not is_org_admin(profile):
        qs = qs.filter(profile=profile)
    return qs.select_related("profile", "profile__user", "case")


def _parse_date(value):
    """``YYYY-MM-DD`` to a date, or None when absent. Raises on anything else.

    Shared by the timesheet and the reports so a malformed date is answered
    the same way on both, rather than one of them reading it as today.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc


def _report_entries(request):
    """Return ``(entries, start, end)`` for a report request.

    Only stopped entries, inside the window, narrowed by the optional
    ``profile``, ``account`` and ``billable`` filters. Visibility is
    `_visible_entry_qs`'s: an agent reports on their own time, an admin on the
    org's, and asking for somebody else's without being an admin is a 403
    rather than an empty report, because a silent empty answer reads as "that
    person logged nothing".

    Raises :class:`TimeReportParamError`, which the views turn into a response.
    """
    profile = request.profile
    entries = _visible_entry_qs(profile).filter(ended_at__isnull=False)

    target = uuid_param(request.query_params, "profile")
    if target:
        if target != str(profile.id) and not is_org_admin(profile):
            raise TimeReportParamError(
                "Only admins can report on another profile's time.",
                status.HTTP_403_FORBIDDEN,
            )
        entries = entries.filter(profile_id=target)

    account = uuid_param(request.query_params, "account")
    if account:
        entries = entries.filter(case__account_id=account)

    billable = request.query_params.get("billable")
    if billable is not None:
        if billable not in ("true", "false"):
            raise TimeReportParamError("billable must be 'true' or 'false'.")
        entries = entries.filter(billable=(billable == "true"))

    try:
        start = _parse_date(request.query_params.get("start"))
        end = _parse_date(request.query_params.get("end"))
    except ValueError as exc:
        raise TimeReportParamError(str(exc)) from exc

    # Both or neither, the same rule the timesheet uses: half a window is
    # more likely a mistake than a request to run from a date to whenever.
    if start is None or end is None:
        end = timezone.localdate()
        start = end - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    if end < start:
        raise TimeReportParamError("end must be on or after start.")

    entries = entries.filter(started_at__date__gte=start, started_at__date__lte=end)
    return entries, start, end


class TimeEntryListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, pk):
        case = get_object_or_404(Case, id=pk, org=request.profile.org)
        entries = (
            _visible_entry_qs(request.profile).filter(case=case).order_by("-started_at")
        )
        return Response(TimeEntrySerializer(entries, many=True).data)

    def post(self, request, pk):
        case = get_object_or_404(Case, id=pk, org=request.profile.org)
        serializer = TimeEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            entry = TimeEntry.objects.create(
                org=request.profile.org,
                case=case,
                profile=request.profile,
                **serializer.validated_data,
            )
        except IntegrityError as exc:
            # Most likely the partial unique on (profile) for active timers,
            # or the end-after-start CheckConstraint as a defense-in-depth.
            return Response(
                {"detail": str(exc).splitlines()[0][:200]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class TimeEntryStartView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        case = get_object_or_404(Case, id=pk, org=request.profile.org)

        # Reject if this profile already has a running timer (anywhere). The
        # one_active_timer_per_profile partial unique would also catch this
        # at INSERT time, but a 409 with the offending case id is friendlier
        # than a 400 from IntegrityError.
        running = (
            TimeEntry.objects.select_for_update()
            .filter(profile=request.profile, ended_at__isnull=True)
            .first()
        )
        if running is not None:
            return Response(
                {
                    "detail": "You already have a running timer.",
                    "running_entry_id": str(running.id),
                    "running_case_id": str(running.case_id),
                },
                status=status.HTTP_409_CONFLICT,
            )

        entry = TimeEntry.objects.create(
            org=request.profile.org,
            case=case,
            profile=request.profile,
            started_at=timezone.now(),
            description=(request.data.get("description") or "").strip(),
            billable=bool(request.data.get("billable", False)),
        )
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class TimeSummaryView(APIView):
    """``GET /api/cases/<pk>/time-summary/``, totals + per-profile breakdown.

    Same shape as ``CaseSerializer.time_summary`` for clients that don't
    want to refetch the full case envelope.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, pk):
        case = get_object_or_404(Case, id=pk, org=request.profile.org)
        qs = case.time_entries.filter(ended_at__isnull=False)
        total = qs.aggregate(total=Sum("duration_minutes"))["total"] or 0
        billable = (
            qs.filter(billable=True).aggregate(s=Sum("duration_minutes"))["s"] or 0
        )
        last_entry_at = (
            qs.order_by("-started_at").values_list("started_at", flat=True).first()
        )
        by_profile = []
        for row in (
            qs.values("profile_id", "profile__user__email")
            .annotate(minutes=Sum("duration_minutes"))
            .order_by("-minutes")
        ):
            by_profile.append(
                {
                    "profile_id": str(row["profile_id"]),
                    "name": row.get("profile__user__email") or "",
                    "minutes": row["minutes"] or 0,
                }
            )
        return Response(
            {
                "total_minutes": total,
                "billable_minutes": billable,
                "last_entry_at": last_entry_at,
                "by_profile": by_profile,
            }
        )


class TimeEntryDetailView(APIView):
    """PUT/DELETE for a specific time entry. Owner or admin only."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_entry(self, request, pk):
        try:
            entry = TimeEntry.objects.get(id=pk, org=request.profile.org)
        except TimeEntry.DoesNotExist:
            return None
        if entry.profile_id != request.profile.id and not is_org_admin(request.profile):
            return False  # Sentinel: row exists but caller is not authorized.
        return entry

    def put(self, request, pk):
        entry = self._get_entry(request, pk)
        if entry is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if entry is False:
            return Response(
                {"detail": "Not authorized to edit this entry."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TimeEntryUpdateSerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # Apply allowed fields explicitly so we never overwrite invoice/profile/case.
        for field, value in serializer.validated_data.items():
            setattr(entry, field, value)
        entry.save()
        return Response(TimeEntrySerializer(entry).data)

    def delete(self, request, pk):
        entry = self._get_entry(request, pk)
        if entry is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if entry is False:
            return Response(
                {"detail": "Not authorized to delete this entry."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if entry.invoice_id is not None:
            return Response(
                {"detail": "Cannot delete an entry that has been invoiced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TimeEntryStopView(APIView):
    """``POST /api/time-entries/<pk>/stop/``. Stop a running timer."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        entry = (
            TimeEntry.objects.select_for_update()
            .filter(id=pk, org=request.profile.org)
            .first()
        )
        if entry is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if entry.profile_id != request.profile.id and not is_org_admin(request.profile):
            return Response(
                {"detail": "Not authorized to stop this entry."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if entry.ended_at is not None:
            return Response(
                {"detail": "Timer is already stopped."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entry.ended_at = timezone.now()
        entry.save()
        return Response(TimeEntrySerializer(entry).data)


class UnbilledEntriesView(APIView):
    """``GET /api/time-entries/unbilled/?account=<uuid>``: list billable,
    stopped, not-yet-invoiced entries for an account so the invoice picker
    can show them. Org-scoped; admins see all entries, agents only their own.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        account_id = uuid_param(request.query_params, "account")
        qs = (
            _visible_entry_qs(request.profile)
            .filter(billable=True, invoice__isnull=True, ended_at__isnull=False)
            .order_by("-started_at")
        )
        if account_id:
            qs = qs.filter(case__account_id=account_id)
        return Response(TimeEntrySerializer(qs, many=True).data)


class TimesheetView(APIView):
    """``GET /api/time-entries/timesheet/?profile=<id>&start=<date>&end=<date>``.

    Returns entries for ``profile`` (defaults to caller) between ``start`` and
    ``end`` inclusive, grouped into a list of day buckets with totals. Only
    admins may pass a ``profile`` other than their own.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        profile_param = uuid_param(request.query_params, "profile")
        target_profile_id = request.profile.id
        if profile_param and profile_param != str(request.profile.id):
            if not is_org_admin(request.profile):
                return Response(
                    {"detail": "Only admins can view another profile's timesheet."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            target_profile_id = profile_param

        try:
            start = _parse_date(request.query_params.get("start"))
            end = _parse_date(request.query_params.get("end"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if start is None or end is None:
            today = timezone.localdate()
            # Default to this Mon..Sun (ISO week).
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        if end < start:
            return Response(
                {"detail": "end must be on or after start."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Include running timers (ended_at IS NULL) so the timesheet shows
        # work-in-progress; the live duration is computed below.
        qs = (
            TimeEntry.objects.filter(
                org=request.profile.org,
                profile_id=target_profile_id,
                started_at__date__gte=start,
                started_at__date__lte=end,
            )
            .select_related("case", "profile", "profile__user", "invoice")
            .order_by("started_at")
        )
        days = OrderedDict()
        cursor = start
        while cursor <= end:
            days[cursor.isoformat()] = {
                "date": cursor.isoformat(),
                "entries": [],
                "total_minutes": 0,
                "billable_minutes": 0,
            }
            cursor += timedelta(days=1)

        now = timezone.now()
        running_count = 0
        for entry in qs:
            day_key = timezone.localdate(entry.started_at).isoformat()
            bucket = days.get(day_key)
            if bucket is None:
                # Entry's local date may fall outside the window when timezone
                # math straddles midnight; skip silently.
                continue
            data = TimeEntrySerializer(entry).data
            # The timesheet page shows each ticket's name and links a billed
            # entry to its invoice, so expand these two FKs from the bare ids
            # the serializer emits. Both are select_related above, so this adds
            # no per-row queries.
            data["case"] = {"id": str(entry.case_id), "name": entry.case.name}
            data["invoice"] = (
                {
                    "id": str(entry.invoice_id),
                    "invoice_number": entry.invoice.invoice_number,
                }
                if entry.invoice_id
                else None
            )
            if entry.ended_at is None:
                # Surface a server-side live duration so non-JS clients still
                # see the right number; the frontend re-ticks it locally.
                live = max(int((now - entry.started_at).total_seconds() // 60), 0)
                data["is_running"] = True
                data["live_duration_minutes"] = live
                bucket["total_minutes"] += live
                if entry.billable:
                    bucket["billable_minutes"] += live
                running_count += 1
            else:
                data["is_running"] = False
                bucket["total_minutes"] += entry.duration_minutes or 0
                if entry.billable:
                    bucket["billable_minutes"] += entry.duration_minutes or 0
            bucket["entries"].append(data)

        week_total = sum(d["total_minutes"] for d in days.values())
        billable_total = sum(d["billable_minutes"] for d in days.values())

        # Whose timesheet this is, named for the header. Reuse request.profile
        # when it's the caller's own week (the common case) to avoid a lookup;
        # otherwise resolve the org-scoped target profile. user.name is never
        # blank (it falls back to the email local-part on first save).
        if str(target_profile_id) == str(request.profile.id):
            target_profile = request.profile
        else:
            target_profile = (
                Profile.objects.filter(org=request.profile.org, id=target_profile_id)
                .select_related("user")
                .first()
            )
        profile_name = (
            target_profile.user.name
            if target_profile and target_profile.user_id
            else ""
        )

        return Response(
            {
                "profile_id": str(target_profile_id),
                "profile": {"id": str(target_profile_id), "name": profile_name},
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": list(days.values()),
                "total_minutes": week_total,
                "billable_minutes": billable_total,
                "running_count": running_count,
                "server_now": now.isoformat(),
            }
        )


class TimeReportView(APIView):
    """``GET /api/time-entries/report/``: where the time went.

    ``?start=&end=`` (YYYY-MM-DD, inclusive, defaulting to the last 30 days),
    ``?group_by=agent|ticket|account``, and the optional ``profile``,
    ``account`` and ``billable`` filters. One row per group with the minutes,
    the billable split, the value at each entry's own rate, and how many
    entries went into it.

    The productivity half of the answer. For the billing half, the same
    window comes out of ``report/export/`` as one row per entry.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        group_by = request.query_params.get("group_by") or "agent"
        try:
            entries, start, end = _report_entries(request)
            report = time_reports.build_report(entries, group_by)
        except TimeReportParamError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)

        return Response(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "group_by": group_by,
                **report,
            }
        )


class TimeReportExportView(APIView):
    """``GET /api/time-entries/report/export/``: the same window as CSV.

    One row per entry, not per group: an invoice is checked against the
    entries behind it, and a spreadsheet can group them again in whatever way
    the person opening it needs.

    Streams, so a year of entries does not have to be built in memory first.
    Same filters and the same visibility as the report above.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)
    # Content negotiation runs before the handler, so a download proxy asking
    # for `Accept: text/csv` was answered 406 without this.
    renderer_classes = CSV_RENDERERS

    def get(self, request):
        try:
            entries, start, end = _report_entries(request)
        except TimeReportParamError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)

        writer = csv.writer(_Echo())
        stream = (writer.writerow(row) for row in time_reports.csv_rows(entries))

        response = StreamingHttpResponse(stream, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="time-{start.isoformat()}-to-{end.isoformat()}.csv"'
        )
        return response
