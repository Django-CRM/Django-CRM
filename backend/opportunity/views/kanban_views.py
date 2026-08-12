"""Kanban views for opportunities.

Status-based only (Opportunity has no Pipeline/Stage model. It groups by the
flat `stage` CharField). The layout mirrors tasks/views/kanban_views.py so the
frontend KanbanBoard component can consume both with the same shape.
"""

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.kanban import place_in_column
from common.permissions import HasOrgContext, is_org_admin
from common.validators import date_param, uuid_list_param, uuid_param
from opportunity.access import assert_deal_access
from opportunity.models import Opportunity, StageAgingConfig
from opportunity.serializer import (
    OpportunityKanbanCardSerializer,
    OpportunityMoveSerializer,
)
from opportunity.workflow import (
    AMOUNT_REQUIRED_STAGES,
    CLOSED_STAGES,
    STAGE_PROBABILITIES,
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

    Placement is delegated to ``common.kanban.place_in_column``, which the
    leads, cases and tasks boards share. Closing a deal by dragging it into a
    won/lost column is a real close: it stamps the same fields the edit form
    stamps, and refuses on the same grounds.
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
        # Locked for the transaction: the move rewrites the whole row, so an
        # edit committing between the read and the save would be overwritten.
        opportunity = get_object_or_404(
            Opportunity.objects.select_for_update(), pk=pk, org=org
        )
        # Same policy PR #747 fixed inline (admin/superuser, creator, assignee),
        # asked once. That PR's one-line change was `request.profile ==
        # opportunity.created_by` -> `profile.user_id == created_by_id`, and
        # has_deal_access already compares it that way. Keeping the helper
        # keeps there being one copy: the inline version is what let the
        # creator branch sit dead long enough to need a PR.
        assert_deal_access(request.profile, request.user, opportunity)

        serializer = OpportunityMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data

        new_stage = data["column_id"]
        entering_closed = (
            new_stage in CLOSED_STAGES and opportunity.stage not in CLOSED_STAGES
        )

        # A won deal has to record what it was worth, the same rule
        # OpportunityCreateSerializer.validate() applies to the edit form. The
        # board cannot ask for a figure mid-drag, so it refuses and the client
        # opens the deal instead of silently booking a nil win.
        if new_stage in AMOUNT_REQUIRED_STAGES and not opportunity.amount:
            return Response(
                {
                    "error": True,
                    "errors": {"amount": "A won deal has to record what it was worth."},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if entering_closed:
            opportunity.closed_by = request.profile
            # `closed_on` is the deal's *expected* close date (see the field's
            # verbose name), which the close then reads as the actual one. So
            # it is filled only when the deal never carried an expectation:
            # stamping today unconditionally would overwrite a date the owner
            # chose, and the board cannot ask for one mid-drag.
            if not opportunity.closed_on:
                opportunity.closed_on = timezone.localdate()
        elif opportunity.stage in CLOSED_STAGES and new_stage not in CLOSED_STAGES:
            # Reopened. `closed_by` is now a lie and goes; `closed_on` stays,
            # because on an open deal it reads as the expected close date
            # again, and clearing it would discard a forecast the close did
            # not create.
            opportunity.closed_by = None

        if opportunity.stage != new_stage:
            # save() only fills probability when it is 0/None, so a stage change
            # would otherwise keep forecasting at the old stage's odds.
            opportunity.probability = STAGE_PROBABILITIES.get(new_stage, 0)

        opportunity.stage = new_stage
        opportunity.kanban_order = place_in_column(
            Opportunity.objects.filter(org=org, stage=new_stage),
            above_id=data.get("above_id"),
            below_id=data.get("below_id"),
            explicit=data.get("kanban_order"),
            exclude_pk=opportunity.pk,
        )
        opportunity.save()

        return Response(
            {
                "error": False,
                "message": "Opportunity moved successfully",
                "opportunity": OpportunityKanbanCardSerializer(opportunity).data,
            }
        )
