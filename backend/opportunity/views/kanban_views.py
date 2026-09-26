"""Kanban views for opportunities.

One board per deal pipeline: the columns are that pipeline's `DealStage` rows
in order, and a column's id is the stage `code` the deals store. The layout
mirrors tasks/views/kanban_views.py so the frontend KanbanBoard component can
consume both with the same shape.
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
from opportunity.models import DealPipeline, DealStage, Opportunity
from opportunity.serializer import (
    OpportunityKanbanCardSerializer,
    OpportunityMoveSerializer,
)
from opportunity.stages import stage_index
from opportunity.workflow import CLOSED_KINDS, WON, stage_probability

# Column colour by what the stage means; the board has no per-stage colour.
KIND_COLORS = {"open": "#3B82F6", "won": "#22C55E", "lost": "#6B7280"}


class OpportunityKanbanView(APIView):
    """GET /api/opportunities/kanban/?pipeline=<id>, columns grouped by stage.

    Without `pipeline` the board is the org's default pipeline. A pipeline id
    from another org is a 404, the same as one that does not exist.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Opportunities Kanban"],
        operation_id="opportunities_kanban",
        parameters=[
            OpenApiParameter(name="pipeline", required=False, type=str),
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

        pipeline_id = uuid_param(request.query_params, "pipeline")
        if pipeline_id:
            pipeline = get_object_or_404(DealPipeline, pk=pipeline_id, org=org)
        else:
            pipeline = DealPipeline.default_for(org)

        queryset = (
            Opportunity.objects.filter(org=org, pipeline=pipeline)
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

        # The org's stages read once and handed to every card, so no card
        # queries its own stage for its label, kind or aging.
        context = {"stages": stage_index(org.id)}

        columns = []
        for stage in pipeline.stages.all():
            opps = queryset.filter(stage=stage.code).order_by(
                "kanban_order", "-created_at"
            )
            columns.append(
                {
                    "id": stage.code,
                    "name": stage.label,
                    "order": stage.order,
                    "kind": stage.kind,
                    "color": KIND_COLORS[stage.kind],
                    "stage_type": (
                        "completed" if stage.kind in CLOSED_KINDS else "open"
                    ),
                    "expected_days": stage.expected_days,
                    "warning_days": stage.warning_days,
                    "is_status_column": True,
                    "wip_limit": None,
                    "item_count": opps.count(),
                    # Cap at 100 per column to keep the payload bounded. Same
                    # cap tasks uses.
                    "items": OpportunityKanbanCardSerializer(
                        opps[:100], many=True, context=context
                    ).data,
                }
            )

        return Response(
            {
                "mode": "status",
                "pipeline": {
                    "id": str(pipeline.id),
                    "name": pipeline.name,
                    "is_default": pipeline.is_default,
                },
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

        new_code = data["column_id"]
        stages = {
            s.code: s
            for s in DealStage.objects.filter(
                org=org, pipeline_id=opportunity.pipeline_id
            )
        }
        # A column of this deal's own pipeline, and nothing else: the board
        # moves a deal between stages, never between pipelines.
        new_stage = stages.get(new_code)
        if new_stage is None:
            return Response(
                {
                    "error": True,
                    "errors": {
                        "column_id": "That is not a stage of this deal's pipeline."
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_stage = stages.get(opportunity.stage)
        was_closed = old_stage is not None and old_stage.kind in CLOSED_KINDS
        entering_closed = new_stage.kind in CLOSED_KINDS and not was_closed

        # A won deal has to record what it was worth, the same rule
        # OpportunityCreateSerializer.validate() applies to the edit form. The
        # board cannot ask for a figure mid-drag, so it refuses and the client
        # opens the deal instead of silently booking a nil win.
        if new_stage.kind == WON and not opportunity.amount:
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
        elif was_closed and new_stage.kind not in CLOSED_KINDS:
            # Reopened. `closed_by` is now a lie and goes; `closed_on` stays,
            # because on an open deal it reads as the expected close date
            # again, and clearing it would discard a forecast the close did
            # not create.
            opportunity.closed_by = None

        if opportunity.stage != new_code:
            # save() only fills probability when it is 0/None, so a stage change
            # would otherwise keep forecasting at the old stage's odds.
            opportunity.probability = stage_probability(new_code, new_stage.kind)

        opportunity.stage = new_code
        opportunity.kanban_order = place_in_column(
            Opportunity.objects.filter(
                org=org, pipeline_id=opportunity.pipeline_id, stage=new_code
            ),
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
