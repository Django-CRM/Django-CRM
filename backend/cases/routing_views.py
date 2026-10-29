"""Routing-rule admin endpoints + dry-run test endpoint.

See docs/cases/tier1/auto-routing.md.
"""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import Case, RoutingRule, RoutingRuleState
from cases.routing import evaluate
from cases.serializer import RoutingRuleSerializer
from common.models import Activity
from common.permissions import HasOrgContext, is_org_admin, may_configure

# Window the routing page reports match / unrouted counts over.
ROUTING_WINDOW_DAYS = 30


def _routing_analytics(org):
    """Per-rule match counts + the org's unrouted count over the last 30 days.

    Both read from the ``ROUTED`` Activity log the engine writes on every match
    (``cases.routing._apply``), one row per rule that fired on a case, written
    even when the matched rule had an empty pool or a missing team. That log
    records the whole population the page needs (unlike escalation's ESCALATED
    log), so there is no fork: ``matched_last_30d`` is a straight count per
    ``rule_id`` and ``unrouted_last_30d`` is the cases that got no ROUTED row at
    all, i.e. literally no rule matched, which is what "nobody was assigned"
    on the page means. A rule that matched but could not assign is NOT unrouted.

    Anchored on ``created_at`` (routing runs once, at case creation, so a case's
    ROUTED row shares its creation time) and scoped to the org explicitly (RLS
    is inert in dev/test).
    """
    cutoff = timezone.now() - timedelta(days=ROUTING_WINDOW_DAYS)
    matched = {}
    routed_case_ids = set()
    for metadata, entity_id in Activity.objects.filter(
        org=org, action="ROUTED", entity_type="Case", created_at__gte=cutoff
    ).values_list("metadata", "entity_id"):
        rule_id = (metadata or {}).get("rule_id")
        if rule_id:
            matched[rule_id] = matched.get(rule_id, 0) + 1
        routed_case_ids.add(entity_id)
    window_case_ids = set(
        Case.objects.filter(
            org=org, is_active=True, created_at__gte=cutoff
        ).values_list("id", flat=True)
    )
    unrouted = len(window_case_ids - routed_case_ids)
    return matched, unrouted


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


class RoutingRuleRotationStateSerializer(serializers.Serializer):
    last_assigned_index = serializers.IntegerField()


class RoutingRuleListRowSerializer(RoutingRuleSerializer):
    """Schema only: a rule as the list returns it, with the keys the view adds."""

    matched_last_30d = serializers.IntegerField(read_only=True)
    state = RoutingRuleRotationStateSerializer(read_only=True, allow_null=True)

    class Meta(RoutingRuleSerializer.Meta):
        fields = (*RoutingRuleSerializer.Meta.fields, "matched_last_30d", "state")


class RoutingRuleListCreateView(APIView):
    """GET lists rules ordered by priority_order; POST creates one (admin)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Routing"],
        responses={
            200: inline_serializer(
                name="RoutingRuleListResponse",
                fields={
                    "rules": RoutingRuleListRowSerializer(many=True),
                    "totals": inline_serializer(
                        name="RoutingRuleListTotals",
                        fields={
                            "count": serializers.IntegerField(),
                            "active": serializers.IntegerField(),
                            "unrouted_last_30d": serializers.IntegerField(),
                        },
                    ),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        org = request.profile.org
        qs = (
            RoutingRule.objects.filter(org=org)
            .order_by("priority_order", "created_at")
            .prefetch_related("target_assignees")
        )
        rules = RoutingRuleSerializer(
            qs, many=True, context={"request": request, "org": org}
        ).data
        matched, unrouted = _routing_analytics(org)
        # round_robin cursor, so the page can name who is next in the rotation.
        states = {
            str(rule_id): idx
            for rule_id, idx in RoutingRuleState.objects.filter(org=org).values_list(
                "rule_id", "last_assigned_index"
            )
        }
        active = 0
        for rule in rules:
            rid = str(rule["id"])
            rule["matched_last_30d"] = matched.get(rid, 0)
            rule["state"] = (
                {"last_assigned_index": states[rid]} if rid in states else None
            )
            if rule["is_active"]:
                active += 1
        totals = {
            "count": len(rules),
            "active": active,
            "unrouted_last_30d": unrouted,
        }
        return Response({"rules": rules, "totals": totals})

    @extend_schema(
        tags=["Routing"],
        request=RoutingRuleSerializer,
        responses={201: RoutingRuleSerializer},
    )
    def post(self, request, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        serializer = RoutingRuleSerializer(
            data=request.data, context={"request": request, "org": org}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save(org=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RoutingRuleDetailView(APIView):
    """GET / PUT / DELETE one routing rule."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_object(self, pk, org):
        return RoutingRule.objects.filter(pk=pk, org=org).first()

    @extend_schema(tags=["Routing"], responses={200: RoutingRuleSerializer})
    def get(self, request, pk, *args, **kwargs):
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Routing rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            RoutingRuleSerializer(
                obj, context={"request": request, "org": request.profile.org}
            ).data
        )

    @extend_schema(
        tags=["Routing"],
        request=RoutingRuleSerializer(partial=True),
        responses={200: RoutingRuleSerializer},
    )
    def put(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        obj = self._get_object(pk, org)
        if not obj:
            return Response(
                {"error": True, "errors": "Routing rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = RoutingRuleSerializer(
            obj,
            data=request.data,
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
        tags=["Routing"],
        responses={
            200: inline_serializer(
                name="RoutingRuleDeleteResponse",
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
                {"error": True, "errors": "Routing rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        obj.delete()
        return Response(
            {"error": False, "message": "Routing rule deleted"},
            status=status.HTTP_200_OK,
        )


class RoutingRuleTestView(APIView):
    """Dry-run a routing rule against a sample Case (no state mutation)."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Routing"],
        request=inline_serializer(
            name="RoutingRuleTestRequest",
            fields={"case_id": serializers.UUIDField()},
        ),
        responses={
            200: inline_serializer(
                name="RoutingRuleTestResponse",
                fields={
                    "matched": serializers.BooleanField(),
                    "rule_id": serializers.CharField(allow_null=True),
                    "rule_name": serializers.CharField(allow_blank=True),
                    "strategy": serializers.CharField(allow_blank=True),
                    "would_assign_profile_ids": serializers.ListField(
                        child=serializers.CharField()
                    ),
                    "would_assign_team_id": serializers.CharField(allow_null=True),
                    "reason": serializers.CharField(allow_blank=True),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        if not is_org_admin(request.profile):
            return _admin_required()
        org = request.profile.org
        case_id = request.data.get("case_id")
        if not case_id:
            return Response(
                {"error": True, "errors": "case_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        case = Case.objects.filter(pk=case_id, org=org).first()
        if case is None:
            return Response(
                {"error": True, "errors": "Case not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        decision = evaluate(case, dry_run=True)
        return Response(
            {
                "matched": decision.matched_rule_id is not None,
                "rule_id": decision.matched_rule_id,
                "rule_name": decision.matched_rule_name,
                "strategy": decision.strategy,
                "would_assign_profile_ids": decision.assigned_profile_ids,
                "would_assign_team_id": decision.assigned_team_id,
                "reason": decision.reason,
            }
        )
