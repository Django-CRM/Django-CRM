"""Escalation policy admin endpoints.

See docs/cases/tier1/escalation.md.
"""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from business_hours.calendar import add_business_hours, get_default_calendar
from cases.models import Case, EscalationPolicy
from cases.serializer import EscalationPolicySerializer
from common.permissions import HasOrgContext, may_configure

# Cases opened within this many days form the cohort the breach counts report on.
BREACH_WINDOW_DAYS = 30


def _sla_deadline(created_at, hours, calendar, paused_at, paused_seconds, now):
    """Business-hours deadline for one SLA half, with customer wait added back.

    A hand-inlined mirror of ``Case._sla_deadline`` that takes an already-
    resolved ``calendar`` so a whole cohort can be scored with ONE
    ``get_default_calendar`` query instead of one per case (the property re-
    resolves it every call). Keep the pause arithmetic in step with the model.
    """
    if created_at is None or hours is None:
        return None
    deadline = add_business_hours(created_at, hours, calendar)
    paused = paused_seconds or 0
    if paused_at is not None:
        paused += max(int((now - paused_at).total_seconds()), 0)
    if paused:
        deadline = deadline + timedelta(seconds=paused)
    return deadline


def _breach_counts_last_30d(org):
    """Count SLA breaches per priority among cases OPENED in the last 30 days.

    The escalation page shows these under each priority's policy AND, crucially,
    keeps showing them when the policy is dead (off, or no recipient). That is
    the entire point of the screen ("breaches told nobody"). So this cannot be
    sourced from the ``ESCALATED`` Activity log: ``scan_for_breached_cases``
    records an activity only when a policy actually acts (active + a target set),
    so a dead policy leaves no event and would read as zero breaches. We count
    the breach *condition* on the case itself instead.

    A case breached first response iff its business-hours deadline has passed and
    the first response either never came or came after it; likewise resolution
    against ``resolved_at``. That definition counts breached-then-resolved-late
    cases, which the live ``is_sla_*_breached`` properties drop the moment a case
    is answered or closed.

    Anchored on ``created_at`` (a standard SLA cohort framing) so the scan stays
    bounded and index-friendly. Not captured: a case opened before the window
    whose deadline slipped inside it via a long Pending pause, rare; noted, not
    counted.
    """
    now = timezone.now()
    cutoff = now - timedelta(days=BREACH_WINDOW_DAYS)
    # One calendar for the whole org: resolved once, not per case.
    calendar = get_default_calendar(org.id)
    counts = {}
    qs = Case.objects.filter(org=org, is_active=True, created_at__gte=cutoff)
    for case in qs.iterator():
        bucket = counts.setdefault(
            case.priority, {"first_response": 0, "resolution": 0}
        )
        fr_deadline = _sla_deadline(
            case.created_at,
            case.sla_first_response_hours,
            calendar,
            case.sla_paused_at,
            case.sla_paused_seconds,
            now,
        )
        if fr_deadline is not None and (
            (
                case.first_response_at is not None
                and case.first_response_at > fr_deadline
            )
            or (case.first_response_at is None and now > fr_deadline)
        ):
            bucket["first_response"] += 1
        res_deadline = _sla_deadline(
            case.created_at,
            case.sla_resolution_hours,
            calendar,
            case.sla_paused_at,
            case.sla_paused_seconds,
            now,
        )
        if res_deadline is not None and (
            (case.resolved_at is not None and case.resolved_at > res_deadline)
            or (case.resolved_at is None and now > res_deadline)
        ):
            bucket["resolution"] += 1
    return counts


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


class EscalationBreachCountsSerializer(serializers.Serializer):
    first_response = serializers.IntegerField()
    resolution = serializers.IntegerField()


class EscalationPolicyListRowSerializer(EscalationPolicySerializer):
    """Schema only: a policy as the list returns it, with its breach counts."""

    breaches_last_30d = EscalationBreachCountsSerializer(read_only=True)

    class Meta(EscalationPolicySerializer.Meta):
        fields = (*EscalationPolicySerializer.Meta.fields, "breaches_last_30d")


class EscalationPolicyListCreateView(APIView):
    """GET lists policies for the org; POST creates one (admin)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Escalation"],
        responses={
            200: inline_serializer(
                name="EscalationPolicyListResponse",
                fields={"policies": EscalationPolicyListRowSerializer(many=True)},
            )
        },
    )
    def get(self, request, *args, **kwargs):
        org = request.profile.org
        qs = EscalationPolicy.objects.filter(org=org).order_by("priority")
        policies = EscalationPolicySerializer(
            qs, many=True, context={"request": request, "org": org}
        ).data
        counts = _breach_counts_last_30d(org)
        for policy in policies:
            policy["breaches_last_30d"] = counts.get(
                policy["priority"], {"first_response": 0, "resolution": 0}
            )
        return Response({"policies": policies})

    @extend_schema(
        tags=["Escalation"],
        request=EscalationPolicySerializer,
        responses={201: EscalationPolicySerializer},
    )
    def post(self, request, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        serializer = EscalationPolicySerializer(
            data=request.data, context={"request": request, "org": org}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save(org=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EscalationPolicyDetailView(APIView):
    """GET / PUT / DELETE one policy. DELETE is hard-delete (policies are config)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_object(self, pk, org):
        return EscalationPolicy.objects.filter(pk=pk, org=org).first()

    @extend_schema(tags=["Escalation"], responses={200: EscalationPolicySerializer})
    def get(self, request, pk, *args, **kwargs):
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Escalation policy not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            EscalationPolicySerializer(
                obj, context={"request": request, "org": request.profile.org}
            ).data
        )

    @extend_schema(
        tags=["Escalation"],
        request=EscalationPolicySerializer(partial=True),
        responses={200: EscalationPolicySerializer},
    )
    def put(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        obj = self._get_object(pk, org)
        if not obj:
            return Response(
                {"error": True, "errors": "Escalation policy not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        # `priority` is the natural key. Disallow changing it post-create
        # (would collide with the (org, priority) unique constraint anyway).
        data = {k: v for k, v in request.data.items() if k != "priority"}
        serializer = EscalationPolicySerializer(
            obj,
            data=data,
            partial=True,
            context={"request": request, "org": org},
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(serializer.data)

    @extend_schema(
        tags=["Escalation"],
        responses={
            200: inline_serializer(
                name="EscalationPolicyDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Escalation policy not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        obj.delete()
        return Response(
            {"error": False, "message": "Escalation policy deleted"},
            status=status.HTTP_200_OK,
        )
