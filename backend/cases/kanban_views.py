"""
Kanban views for case management.
Supports both status-based (default) and custom pipeline-based kanban boards.
"""

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import assert_case_write_access, visible_cases_qs
from cases.approvals import close_refusal
from cases.models import Case, CasePipeline, CaseStage
from cases.serializer import (
    CaseKanbanCardSerializer,
    CaseMoveSerializer,
    CasePipelineListSerializer,
    CasePipelineSerializer,
    CaseStageSerializer,
)
from cases.workflow import duplicate_refusal
from common.kanban import place_in_column
from common.permissions import HasOrgContext, is_org_admin
from common.utils import STATUS_CHOICE
from common.validators import date_param, uuid_param


class CaseKanbanView(APIView):
    """
    Kanban board view for cases.

    Supports two modes:
    1. Status-based (default): Groups cases by Case.status field
    2. Pipeline-based: Groups cases by CaseStage when pipeline_id is provided
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases Kanban"],
        operation_id="cases_kanban",
        parameters=[
            OpenApiParameter(name="org", required=True, type=str),
            OpenApiParameter(
                name="pipeline_id",
                description="Pipeline ID. If not provided, uses status-based columns",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="assigned_to",
                description="Filter by assigned user ID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="priority",
                description="Filter by priority (Low/Normal/High/Urgent)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="case_type",
                description="Filter by case type (Question/Incident/Problem)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Search in name and description",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="account",
                description="Filter by account ID",
                required=False,
                type=str,
            ),
        ],
    )
    def get(self, request):
        """Get kanban board data."""
        org = request.profile.org
        pipeline_id = uuid_param(request.query_params, "pipeline_id")

        # Base queryset with filters. Merged duplicates are hidden by default
        # (matches the cases-list behavior); admins can pass show_merged=true.
        queryset = (
            Case.objects.filter(org=org, is_active=True)
            .select_related("created_by", "stage", "account")
            .prefetch_related("assigned_to", "tags", "contacts")
        )
        if request.query_params.get("show_merged") != "true":
            queryset = queryset.filter(merged_into__isnull=True).exclude(
                status="Duplicate"
            )

        # The ticket read rule itself, as the list and the detail view apply
        # it: watchers included, and no superuser clause, because the detail
        # view has none.
        queryset = queryset.filter(
            pk__in=visible_cases_qs(request.profile).values("pk")
        )

        # Apply search/filters
        queryset = self._apply_filters(queryset, request.query_params)

        if pipeline_id:
            return self._get_pipeline_kanban(queryset, pipeline_id, request)
        return self._get_status_kanban(queryset)

    def _apply_filters(self, queryset, params):
        """Apply common filters to queryset."""
        assigned_to = uuid_param(params, "assigned_to")
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)
        if params.get("priority"):
            queryset = queryset.filter(priority=params.get("priority"))
        if params.get("case_type"):
            queryset = queryset.filter(case_type=params.get("case_type"))
        if params.get("search"):
            search = params.get("search")
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        account = uuid_param(params, "account")
        if account:
            queryset = queryset.filter(account_id=account)
        tags = uuid_param(params, "tags")
        if tags:
            queryset = queryset.filter(tags__id=tags)
        created_at_gte = date_param(params, "created_at__gte")
        if created_at_gte:
            queryset = queryset.filter(created_at__date__gte=created_at_gte)
        created_at_lte = date_param(params, "created_at__lte")
        if created_at_lte:
            queryset = queryset.filter(created_at__date__lte=created_at_lte)
        return queryset.distinct()

    def _get_status_kanban(self, queryset):
        """Build kanban data using Case.status as columns."""
        # Define column order and colors matching case workflow
        status_config = {
            "New": {"order": 1, "color": "#3B82F6", "type": "open"},
            "Assigned": {"order": 2, "color": "#8B5CF6", "type": "open"},
            "Pending": {"order": 3, "color": "#F59E0B", "type": "open"},
            "Closed": {"order": 4, "color": "#22C55E", "type": "closed"},
            "Rejected": {"order": 5, "color": "#EF4444", "type": "rejected"},
            "Duplicate": {"order": 6, "color": "#6B7280", "type": "rejected"},
        }

        columns = []
        for status_value, label in STATUS_CHOICE:
            config = status_config.get(
                status_value, {"order": 99, "color": "#6B7280", "type": "open"}
            )
            cases = queryset.filter(status=status_value).order_by(
                "kanban_order", "-created_at"
            )

            columns.append(
                {
                    "id": status_value,
                    "name": label,
                    "order": config["order"],
                    "color": config["color"],
                    "stage_type": config["type"],
                    "is_status_column": True,
                    "wip_limit": None,
                    "case_count": cases.count(),
                    "cases": CaseKanbanCardSerializer(cases[:100], many=True).data,
                }
            )

        columns.sort(key=lambda x: x["order"])

        return Response(
            {
                "mode": "status",
                "pipeline": None,
                "columns": columns,
                "total_cases": queryset.count(),
            }
        )

    def _get_pipeline_kanban(self, queryset, pipeline_id, request):
        """Build kanban data using CasePipeline stages as columns."""
        pipeline = get_object_or_404(
            CasePipelineListSerializer.with_counts(
                CasePipeline.objects.all(), request.profile
            ),
            pk=pipeline_id,
            org=request.profile.org,
            is_active=True,
        )

        queryset = queryset.filter(stage__pipeline=pipeline)

        columns = []
        for stage in pipeline.stages.all().order_by("order"):
            cases = queryset.filter(stage=stage).order_by("kanban_order", "-created_at")

            columns.append(
                {
                    "id": str(stage.id),
                    "name": stage.name,
                    "order": stage.order,
                    "color": stage.color,
                    "stage_type": stage.stage_type,
                    "wip_limit": stage.wip_limit,
                    "maps_to_status": stage.maps_to_status,
                    "is_status_column": False,
                    "case_count": cases.count(),
                    "cases": CaseKanbanCardSerializer(cases[:100], many=True).data,
                }
            )

        return Response(
            {
                "mode": "pipeline",
                "pipeline": CasePipelineListSerializer(pipeline).data,
                "columns": columns,
                "total_cases": queryset.count(),
            }
        )


class CaseMoveView(APIView):
    """Move a case to a different stage/status and update order."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases Kanban"],
        operation_id="case_move",
        request=CaseMoveSerializer,
    )
    @transaction.atomic
    def patch(self, request, pk):
        """Move case to different column and/or position."""
        org = request.profile.org
        # Locked for the transaction: the move saves the whole row, so an
        # edit committing between this read and that save would be lost.
        case = get_object_or_404(Case.objects.select_for_update(), pk=pk, org=org)

        # A move rewrites the ticket's status, stage and order, so it takes the
        # ticket's own write rule and nothing wider. This used to add a Django
        # superuser clause, which let a superuser who is a plain member of the
        # org move any ticket here that `CaseDetailView.patch` refuses them.
        assert_case_write_access(request.profile, case)

        serializer = CaseMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        new_stage = case.stage
        new_status = case.status

        # Handle stage change
        if "stage_id" in data:
            if data["stage_id"]:
                stage = get_object_or_404(CaseStage, pk=data["stage_id"], org=org)

                # Check WIP limit
                if stage.wip_limit:
                    current_count = stage.cases.exclude(pk=case.pk).count()
                    if current_count >= stage.wip_limit:
                        return Response(
                            {
                                "error": f"Stage '{stage.name}' has reached its WIP limit of {stage.wip_limit}"
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                new_stage = stage

                # Auto-update status if stage has maps_to_status
                if stage.maps_to_status:
                    new_status = stage.maps_to_status
            else:
                new_stage = None

        # Handle status change (for status-based kanban)
        if "status" in data:
            new_status = data["status"]

        # A drag into Closed is a close, so it takes the gate PATCH takes and
        # answers a refusal the same way, before anything is written. The board
        # has no date field, so the closing date is today, as it is for the
        # ticket page's quick status change. Moving back out of Closed needs
        # nothing here: the pre_save signal clears `closed_on` and
        # `resolved_at` on every save that leaves Closed, this one included.
        closed_on = case.closed_on
        if new_status == "Closed" and case.status != "Closed":
            closed_on = timezone.localdate()
        refusal = duplicate_refusal(case.status, new_status) or close_refusal(
            case,
            status=new_status,
            closed_on=closed_on,
            priority=case.priority,
            case_type=case.case_type,
        )
        if refusal:
            return Response(
                {
                    "error": True,
                    "errors": {field: [msg] for field, msg in refusal.items()},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        case.stage = new_stage
        case.status = new_status
        case.closed_on = closed_on

        # Calculate new order
        # `case.stage`/`case.status` are already the destination by this
        # point, so the column queryset describes where the card is landing.
        case.kanban_order = place_in_column(
            self._column_qs(case, org),
            above_id=data.get("above_case_id"),
            below_id=data.get("below_case_id"),
            explicit=data.get("kanban_order"),
            exclude_pk=case.pk,
        )

        case.save()

        return Response(
            {
                "error": False,
                "message": "Case moved successfully",
                "case": CaseKanbanCardSerializer(case).data,
            }
        )

    def _column_qs(self, case, org):
        """The destination column, as a queryset.

        A board runs in one of two modes. With a pipeline, a column is a stage;
        without one, it is a status bucket among the rows that have no stage.
        Narrowing to the destination here is what lets
        ``common.kanban.place_in_column`` resolve the neighbour hints inside
        the column and ignore an id naming a card somewhere else.
        """
        if case.stage:
            return Case.objects.filter(org=org, stage=case.stage)
        return Case.objects.filter(org=org, status=case.status, stage__isnull=True)


class CasePipelineListCreateView(APIView):
    """List and create case pipelines."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Case Pipelines"], responses={200: CasePipelineListSerializer(many=True)}
    )
    def get(self, request):
        """List all pipelines for the organization."""
        org = request.profile.org
        pipelines = CasePipelineListSerializer.with_counts(
            CasePipeline.objects.filter(org=org, is_active=True), request.profile
        )
        serializer = CasePipelineListSerializer(pipelines, many=True)
        return Response({"pipelines": serializer.data})

    @extend_schema(
        tags=["Case Pipelines"],
        request=CasePipelineSerializer,
        responses={201: CasePipelineSerializer},
    )
    def post(self, request):
        """Create a new pipeline."""
        org = request.profile.org

        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Only admins can create pipelines"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CasePipelineSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline = serializer.save(org=org, created_by=request.user)

        # Create default stages if requested
        if request.data.get("create_default_stages", True):
            default_stages = [
                {
                    "name": "New",
                    "order": 1,
                    "color": "#3B82F6",
                    "stage_type": "open",
                    "maps_to_status": "New",
                },
                {
                    "name": "Assigned",
                    "order": 2,
                    "color": "#8B5CF6",
                    "stage_type": "open",
                    "maps_to_status": "Assigned",
                },
                {
                    "name": "In Progress",
                    "order": 3,
                    "color": "#F59E0B",
                    "stage_type": "open",
                    "maps_to_status": "Pending",
                },
                {
                    "name": "Resolved",
                    "order": 4,
                    "color": "#22C55E",
                    "stage_type": "closed",
                    "maps_to_status": "Closed",
                },
                {
                    "name": "Rejected",
                    "order": 5,
                    "color": "#EF4444",
                    "stage_type": "rejected",
                    "maps_to_status": "Rejected",
                },
            ]
            for stage_data in default_stages:
                CaseStage.objects.create(
                    pipeline=pipeline, org=org, created_by=request.user, **stage_data
                )

        # Refresh to include created stages
        pipeline.refresh_from_db()
        return Response(
            CasePipelineSerializer(pipeline, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class CasePipelineDetailView(APIView):
    """Retrieve, update, delete a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk, org):
        return get_object_or_404(CasePipeline, pk=pk, org=org)

    @extend_schema(tags=["Case Pipelines"], responses={200: CasePipelineSerializer})
    def get(self, request, pk):
        pipeline = self.get_object(pk, request.profile.org)
        return Response(
            CasePipelineSerializer(pipeline, context={"request": request}).data
        )

    @extend_schema(
        tags=["Case Pipelines"],
        request=CasePipelineSerializer,
        responses={200: CasePipelineSerializer},
    )
    def put(self, request, pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        pipeline = self.get_object(pk, request.profile.org)
        serializer = CasePipelineSerializer(pipeline, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline = serializer.save(updated_by=request.user)
        return Response(
            CasePipelineSerializer(pipeline, context={"request": request}).data
        )

    @extend_schema(tags=["Case Pipelines"], responses={204: None})
    def delete(self, request, pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        pipeline = self.get_object(pk, request.profile.org)

        case_count = Case.objects.filter(stage__pipeline=pipeline).count()
        if case_count > 0:
            return Response(
                {
                    "error": f"Cannot delete pipeline with {case_count} cases. Move cases first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline.is_active = False
        pipeline.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CaseStageCreateView(APIView):
    """Create a stage in a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Case Stages"],
        request=CaseStageSerializer,
        responses={201: CaseStageSerializer},
    )
    def post(self, request, pipeline_pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = get_object_or_404(CasePipeline, pk=pipeline_pk, org=org)

        serializer = CaseStageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage = serializer.save(pipeline=pipeline, org=org, created_by=request.user)
        return Response(
            CaseStageSerializer(stage, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class CaseStageDetailView(APIView):
    """Update or delete a stage."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Case Stages"],
        request=CaseStageSerializer,
        responses={200: CaseStageSerializer},
    )
    def put(self, request, pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        stage = get_object_or_404(CaseStage, pk=pk, org=request.profile.org)
        serializer = CaseStageSerializer(stage, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage = serializer.save(updated_by=request.user)
        return Response(CaseStageSerializer(stage, context={"request": request}).data)

    @extend_schema(tags=["Case Stages"], responses={204: None})
    def delete(self, request, pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        stage = get_object_or_404(CaseStage, pk=pk, org=request.profile.org)

        case_count = stage.cases.count()
        if case_count > 0:
            return Response(
                {
                    "error": f"Cannot delete stage with {case_count} cases. Move cases first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CaseStageReorderSerializer(serializers.Serializer):
    stage_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class CaseStageReorderView(APIView):
    """Bulk reorder stages in a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Case Stages"],
        request=CaseStageReorderSerializer,
    )
    @transaction.atomic
    def post(self, request, pipeline_pk):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = get_object_or_404(
            CasePipeline, pk=pipeline_pk, org=org, is_active=True
        )

        body = CaseStageReorderSerializer(data=request.data)
        if not body.is_valid():
            return Response(
                {"error": True, "errors": body.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        stage_ids = body.validated_data["stage_ids"]

        # Exactly this pipeline's stages, each once. A partial list left the
        # stages it omitted on their old numbers, colliding with the new ones,
        # and a repeated id took two positions.
        current = set(pipeline.stages.values_list("id", flat=True))
        if len(stage_ids) != len(set(stage_ids)) or set(stage_ids) != current:
            return Response(
                {
                    "error": "Send every stage of this pipeline exactly once, "
                    "in the new order."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for order, stage_id in enumerate(stage_ids):
            pipeline.stages.filter(id=stage_id).update(order=order)

        return Response({"message": "Stages reordered successfully"})
