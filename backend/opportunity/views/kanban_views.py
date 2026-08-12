"""Kanban views for opportunities.

Status-based only (Opportunity has no Pipeline/Stage model. It groups by the
flat `stage` CharField). The layout mirrors tasks/views/kanban_views.py so the
frontend KanbanBoard component can consume both with the same shape.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from common.validators import date_param, uuid_list_param, uuid_param
from opportunity.models import Opportunity, StageAgingConfig
from opportunity.serializer import (
    OpportunityKanbanCardSerializer,
    OpportunityMoveSerializer,
)

# Column display config. Keys must match the stage choices in
# common.utils.STAGES. Extra/unknown stages get the fallback.
STAGE_CONFIG = {
    "PROSPECTING": {
        "order": 1,
        "color": "#3B82F6",
        "type": "open",
        "label": "Prospecting",
    },
    "QUALIFICATION": {
        "order": 2,
        "color": "#8B5CF6",
        "type": "open",
        "label": "Qualification",
    },
    "PROPOSAL": {
        "order": 3,
        "color": "#F59E0B",
        "type": "in_progress",
        "label": "Proposal",
    },
    "NEGOTIATION": {
        "order": 4,
        "color": "#EF4444",
        "type": "in_progress",
        "label": "Negotiation",
    },
    "CLOSED_WON": {"order": 5, "color": "#22C55E", "type": "completed", "label": "Won"},
    "CLOSED_LOST": {
        "order": 6,
        "color": "#6B7280",
        "type": "completed",
        "label": "Lost",
    },
}


class OpportunityKanbanView(APIView):
    """GET /api/opportunities/kanban/, columns grouped by stage."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Opportunities Kanban"],
        operation_id="opportunities_kanban",
        parameters=[
            OpenApiParameter(name="search", required=False, type=str),
            OpenApiParameter(name="account", required=False, type=str),
            OpenApiParameter(name="assigned_to", required=False, type=str),
            OpenApiParameter(name="tags", required=False, type=str),
            OpenApiParameter(name="closed_on__gte", required=False, type=str),
            OpenApiParameter(name="closed_on__lte", required=False, type=str),
        ],
    )
    def get(self, request):
        org = request.profile.org

        queryset = (
            Opportunity.objects.filter(org=org)
            .select_related("account")
            .prefetch_related("assigned_to", "tags")
        )

        # Match the list view's RBAC scoping so users only see opps they own
        # or are assigned to. Kanban shouldn't reveal more than the table.
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            queryset = queryset.filter(
                Q(created_by=request.profile.user) | Q(assigned_to=request.profile)
            ).distinct()

        queryset = self._apply_filters(queryset, request.query_params)

        # Aging configs prefetched once and passed via serializer context so
        # each card doesn't re-query StageAgingConfig.
        aging_configs = {c.stage: c for c in StageAgingConfig.objects.filter(org=org)}

        columns = []
        stage_choices = Opportunity._meta.get_field("stage").choices
        for stage_value, _label in stage_choices:
            cfg = STAGE_CONFIG.get(
                stage_value,
                {"order": 99, "color": "#6B7280", "type": "open", "label": stage_value},
            )
            opps = queryset.filter(stage=stage_value).order_by(
                "kanban_order", "-created_at"
            )
            columns.append(
                {
                    "id": stage_value,
                    "name": cfg["label"],
                    "order": cfg["order"],
                    "color": cfg["color"],
                    "stage_type": cfg["type"],
                    "is_status_column": True,
                    "wip_limit": None,
                    "item_count": opps.count(),
                    # Cap at 100 per column to keep the payload bounded. Same
                    # cap tasks uses.
                    "items": OpportunityKanbanCardSerializer(
                        opps[:100], many=True, context={"aging_configs": aging_configs}
                    ).data,
                }
            )

        columns.sort(key=lambda c: c["order"])

        return Response(
            {
                "mode": "status",
                "pipeline": None,
                "columns": columns,
                "total_items": queryset.count(),
            }
        )

    def _apply_filters(self, queryset, params):
        if params.get("search"):
            queryset = queryset.filter(name__icontains=params.get("search"))
        account = uuid_param(params, "account")
        if account:
            queryset = queryset.filter(account_id=account)
        assigned_to = uuid_list_param(params, "assigned_to")
        if assigned_to:
            queryset = queryset.filter(assigned_to__id__in=assigned_to).distinct()
        tags = uuid_list_param(params, "tags")
        if tags:
            queryset = queryset.filter(tags__id__in=tags).distinct()
        closed_on_gte = date_param(params, "closed_on__gte")
        if closed_on_gte:
            queryset = queryset.filter(closed_on__gte=closed_on_gte)
        closed_on_lte = date_param(params, "closed_on__lte")
        if closed_on_lte:
            queryset = queryset.filter(closed_on__lte=closed_on_lte)
        return queryset


class OpportunityMoveView(APIView):
    """PATCH /api/opportunities/<pk>/move/, change stage and/or reorder.

    Mirrors TaskMoveView's fractional-order algorithm: if both neighbors are
    given we average them; with only one we offset by ±1000; falling through
    means append to the column.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Opportunities Kanban"],
        operation_id="opportunity_move",
        request=OpportunityMoveSerializer,
    )
    @transaction.atomic
    def patch(self, request, pk):
        org = request.profile.org
        opportunity = get_object_or_404(Opportunity, pk=pk, org=org)

        # Match Opportunity update/delete RBAC: admin OR creator OR assignee.
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            is_owner = request.profile.user_id == opportunity.created_by_id
            is_assignee = request.profile in opportunity.assigned_to.all()
            if not (is_owner or is_assignee):
                return Response(
                    {"error": "Permission denied"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = OpportunityMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data

        new_stage = data["stage"]
        # If the user is moving INTO a closed stage, stamp closed_by, mirrors
        # the OpportunityListView.post / OpportunityDetailView.put behavior so
        # kanban-driven close events look identical to form-driven ones.
        if (
            new_stage in ("CLOSED_WON", "CLOSED_LOST")
            and opportunity.stage != new_stage
        ):
            opportunity.closed_by = request.profile

        opportunity.stage = new_stage
        opportunity.kanban_order = self._calculate_order(data, opportunity, org)

        # Opportunity.save() auto-updates probability + stage_changed_at, no
        # need to pass update_fields here, the model handles its own bookkeeping.
        opportunity.save()

        aging_configs = {c.stage: c for c in StageAgingConfig.objects.filter(org=org)}
        return Response(
            {
                "error": False,
                "message": "Opportunity moved successfully",
                "opportunity": OpportunityKanbanCardSerializer(
                    opportunity, context={"aging_configs": aging_configs}
                ).data,
            }
        )

    def _calculate_order(self, data, opportunity, org):
        if "kanban_order" in data:
            return data["kanban_order"]

        above_id = data.get("above_id")
        below_id = data.get("below_id")

        if above_id and below_id:
            above = Opportunity.objects.filter(pk=above_id, org=org).first()
            below = Opportunity.objects.filter(pk=below_id, org=org).first()
            if above and below:
                return (above.kanban_order + below.kanban_order) / 2

        if above_id:
            above = Opportunity.objects.filter(pk=above_id, org=org).first()
            if above:
                next_opp = (
                    Opportunity.objects.filter(
                        org=org,
                        stage=opportunity.stage,
                        kanban_order__gt=above.kanban_order,
                    )
                    .exclude(pk=opportunity.pk)
                    .order_by("kanban_order")
                    .first()
                )
                if next_opp:
                    return (above.kanban_order + next_opp.kanban_order) / 2
                return above.kanban_order + Decimal("1000")

        if below_id:
            below = Opportunity.objects.filter(pk=below_id, org=org).first()
            if below:
                return below.kanban_order - Decimal("1000")

        # Append to end of the (new) column.
        last = (
            Opportunity.objects.filter(stage=opportunity.stage, org=org)
            .exclude(pk=opportunity.pk)
            .order_by("-kanban_order")
            .first()
        )
        if last:
            return last.kanban_order + Decimal("1000")
        return Decimal("1000")
