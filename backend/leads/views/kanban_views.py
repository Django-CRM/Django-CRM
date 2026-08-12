"""
Kanban views for lead management.
Supports both status-based (default) and custom pipeline-based kanban boards.
"""

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.kanban import place_in_column
from common.permissions import HasOrgContext, is_org_admin
from common.utils import LEAD_STATUS
from common.validators import date_param, uuid_param
from leads.models import Lead, LeadPipeline, LeadStage
from leads.serializer import (
    LeadKanbanCardSerializer,
    LeadMoveSerializer,
    LeadPipelineListSerializer,
    LeadPipelineSerializer,
    LeadStageSerializer,
)


class LeadKanbanView(APIView):
    """
    Kanban board view for leads.

    Supports two modes:
    1. Status-based (default): Groups leads by Lead.status field
    2. Pipeline-based: Groups leads by LeadStage when pipeline_id is provided
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads Kanban"],
        operation_id="leads_kanban",
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
                name="rating",
                description="Filter by rating (HOT/WARM/COLD)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Search in name, company, email",
                required=False,
                type=str,
            ),
        ],
    )
    def get(self, request):
        """Get kanban board data."""
        org = request.profile.org
        pipeline_id = uuid_param(request.query_params, "pipeline_id")

        # Base queryset with filters
        queryset = (
            Lead.objects.filter(org=org, is_active=True)
            .exclude(status="converted")
            .select_related("created_by", "stage")
            .prefetch_related("assigned_to", "tags")
        )

        # Apply permission filtering
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            queryset = queryset.filter(
                Q(assigned_to=request.profile) | Q(created_by=request.profile.user)
            )

        # Apply search/filters
        queryset = self._apply_filters(queryset, request.query_params)

        if pipeline_id:
            # Pipeline-based kanban
            return self._get_pipeline_kanban(queryset, pipeline_id, org)
        # Status-based kanban
        return self._get_status_kanban(queryset)

    def _apply_filters(self, queryset, params):
        """Apply common filters to queryset."""
        assigned_to = uuid_param(params, "assigned_to")
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)
        if params.get("rating"):
            queryset = queryset.filter(rating=params.get("rating"))
        if params.get("search"):
            search = params.get("search")
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(company_name__icontains=search)
                | Q(email__icontains=search)
                | Q(title__icontains=search)
            )
        if params.get("source"):
            queryset = queryset.filter(source=params.get("source"))
        created_at_gte = date_param(params, "created_at__gte")
        if created_at_gte:
            queryset = queryset.filter(created_at__gte=created_at_gte)
        created_at_lte = date_param(params, "created_at__lte")
        if created_at_lte:
            queryset = queryset.filter(created_at__lte=created_at_lte)
        return queryset

    def _get_status_kanban(self, queryset):
        """Build kanban data using Lead.status as columns."""
        # Define column order and colors
        status_config = {
            "assigned": {"order": 1, "color": "#3B82F6", "type": "open"},
            "in process": {"order": 2, "color": "#F59E0B", "type": "open"},
            "recycled": {"order": 3, "color": "#F97316", "type": "lost"},
            "closed": {"order": 4, "color": "#6B7280", "type": "lost"},
        }

        columns = []
        for status_value, label in LEAD_STATUS:
            if status_value == "converted":
                continue  # Skip converted in kanban view

            config = status_config.get(
                status_value, {"order": 99, "color": "#6B7280", "type": "open"}
            )
            leads = queryset.filter(status=status_value).order_by(
                "kanban_order", "-created_at"
            )

            columns.append(
                {
                    "id": status_value,  # Use status as column ID
                    "name": label,
                    "order": config["order"],
                    "color": config["color"],
                    "stage_type": config["type"],
                    "is_status_column": True,
                    "wip_limit": None,
                    "lead_count": leads.count(),
                    "leads": LeadKanbanCardSerializer(leads[:100], many=True).data,
                }
            )

        columns.sort(key=lambda x: x["order"])

        return Response(
            {
                "mode": "status",
                "pipeline": None,
                "columns": columns,
                "total_leads": queryset.count(),
            }
        )

    def _get_pipeline_kanban(self, queryset, pipeline_id, org):
        """Build kanban data using LeadPipeline stages as columns."""
        pipeline = get_object_or_404(
            LeadPipeline, pk=pipeline_id, org=org, is_active=True
        )

        # Filter leads to this pipeline
        queryset = queryset.filter(stage__pipeline=pipeline)

        columns = []
        for stage in pipeline.stages.all().order_by("order"):
            leads = queryset.filter(stage=stage).order_by("kanban_order", "-created_at")

            columns.append(
                {
                    "id": str(stage.id),
                    "name": stage.name,
                    "order": stage.order,
                    "color": stage.color,
                    "stage_type": stage.stage_type,
                    "wip_limit": stage.wip_limit,
                    "win_probability": stage.win_probability,
                    "maps_to_status": stage.maps_to_status,
                    "is_status_column": False,
                    "lead_count": leads.count(),
                    "leads": LeadKanbanCardSerializer(leads[:100], many=True).data,
                }
            )

        return Response(
            {
                "mode": "pipeline",
                "pipeline": LeadPipelineListSerializer(pipeline).data,
                "columns": columns,
                "total_leads": queryset.count(),
            }
        )


class LeadMoveView(APIView):
    """Move a lead to a different stage/status and update order."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads Kanban"],
        operation_id="lead_move",
        request=LeadMoveSerializer,
    )
    @transaction.atomic
    def patch(self, request, pk):
        """Move lead to different column and/or position."""
        org = request.profile.org
        # Locked for the transaction: the move saves the whole row, so an
        # edit committing between this read and that save would be lost.
        lead = get_object_or_404(Lead.objects.select_for_update(), pk=pk, org=org)

        # Permission check
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            if not (
                request.profile.user == lead.created_by
                or request.profile in lead.assigned_to.all()
            ):
                return Response(
                    {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
                )

        serializer = LeadMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data

        # Handle stage change
        if "stage_id" in data:
            if data["stage_id"]:
                stage = get_object_or_404(LeadStage, pk=data["stage_id"], org=org)

                # Check WIP limit
                if stage.wip_limit:
                    current_count = stage.leads.exclude(pk=lead.pk).count()
                    if current_count >= stage.wip_limit:
                        return Response(
                            {
                                "error": f"Stage '{stage.name}' has reached its WIP limit of {stage.wip_limit}"
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                lead.stage = stage

                # Auto-update status if stage has maps_to_status
                if stage.maps_to_status:
                    lead.status = stage.maps_to_status

                # Auto-update probability if stage has win_probability
                if stage.win_probability and lead.probability == 0:
                    lead.probability = stage.win_probability
            else:
                lead.stage = None

        # Handle status change (for status-based kanban)
        if "status" in data:
            lead.status = data["status"]

        # `lead.stage`/`lead.status` are already the destination by this point,
        # so the column queryset below describes where the card is landing.
        lead.kanban_order = place_in_column(
            self._column_qs(lead, org),
            above_id=data.get("above_lead_id"),
            below_id=data.get("below_lead_id"),
            explicit=data.get("kanban_order"),
            exclude_pk=lead.pk,
        )

        lead.save()

        return Response(
            {
                "error": False,
                "message": "Lead moved successfully",
                "lead": LeadKanbanCardSerializer(lead).data,
            }
        )

    def _column_qs(self, lead, org):
        """The destination column, as a queryset.

        A board runs in one of two modes. With a pipeline, a column is a stage;
        without one, it is a status bucket among the rows that have no stage.
        Narrowing to the destination here is what lets
        ``common.kanban.place_in_column`` resolve the neighbour hints inside
        the column and ignore an id naming a card somewhere else.
        """
        if lead.stage:
            return Lead.objects.filter(org=org, stage=lead.stage)
        return Lead.objects.filter(org=org, status=lead.status, stage__isnull=True)


class LeadPipelineListCreateView(APIView):
    """List and create lead pipelines."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Lead Pipelines"], responses={200: LeadPipelineListSerializer(many=True)}
    )
    def get(self, request):
        """List all pipelines for the organization."""
        org = request.profile.org
        pipelines = LeadPipeline.objects.filter(org=org, is_active=True)
        serializer = LeadPipelineListSerializer(pipelines, many=True)
        return Response({"pipelines": serializer.data})

    @extend_schema(
        tags=["Lead Pipelines"],
        request=LeadPipelineSerializer,
        responses={201: LeadPipelineSerializer},
    )
    def post(self, request):
        """Create a new pipeline."""
        org = request.profile.org

        # Only admins can create pipelines
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Only admins can create pipelines"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LeadPipelineSerializer(data=request.data)
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
                    "maps_to_status": "assigned",
                },
                {
                    "name": "Contacted",
                    "order": 2,
                    "color": "#8B5CF6",
                    "stage_type": "open",
                    "maps_to_status": "in process",
                },
                {
                    "name": "Qualified",
                    "order": 3,
                    "color": "#F59E0B",
                    "stage_type": "open",
                    "maps_to_status": "in process",
                    "win_probability": 25,
                },
                {
                    "name": "Proposal",
                    "order": 4,
                    "color": "#10B981",
                    "stage_type": "open",
                    "maps_to_status": "in process",
                    "win_probability": 50,
                },
                {
                    "name": "Won",
                    "order": 5,
                    "color": "#22C55E",
                    "stage_type": "won",
                    "maps_to_status": "converted",
                    "win_probability": 100,
                },
                {
                    "name": "Lost",
                    "order": 6,
                    "color": "#EF4444",
                    "stage_type": "lost",
                    "maps_to_status": "closed",
                },
            ]
            for stage_data in default_stages:
                LeadStage.objects.create(pipeline=pipeline, org=org, **stage_data)

        return Response(
            LeadPipelineSerializer(pipeline).data, status=status.HTTP_201_CREATED
        )


class LeadPipelineDetailView(APIView):
    """Retrieve, update, delete a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk, org):
        return get_object_or_404(LeadPipeline, pk=pk, org=org)

    @extend_schema(tags=["Lead Pipelines"], responses={200: LeadPipelineSerializer})
    def get(self, request, pk):
        """Get pipeline details with all stages."""
        pipeline = self.get_object(pk, request.profile.org)
        return Response(LeadPipelineSerializer(pipeline).data)

    @extend_schema(
        tags=["Lead Pipelines"],
        request=LeadPipelineSerializer,
        responses={200: LeadPipelineSerializer},
    )
    def put(self, request, pk):
        """Update pipeline."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        pipeline = self.get_object(pk, request.profile.org)
        serializer = LeadPipelineSerializer(pipeline, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline = serializer.save(updated_by=request.user)
        return Response(LeadPipelineSerializer(pipeline).data)

    @extend_schema(tags=["Lead Pipelines"], responses={204: None})
    def delete(self, request, pk):
        """Delete pipeline (soft delete by setting is_active=False)."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        pipeline = self.get_object(pk, request.profile.org)

        # Check if pipeline has leads
        lead_count = Lead.objects.filter(stage__pipeline=pipeline).count()
        if lead_count > 0:
            return Response(
                {
                    "error": f"Cannot delete pipeline with {lead_count} leads. Move leads first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        pipeline.is_active = False
        pipeline.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LeadStageCreateView(APIView):
    """Create a stage in a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Lead Stages"],
        request=LeadStageSerializer,
        responses={201: LeadStageSerializer},
    )
    def post(self, request, pipeline_pk):
        """Add a new stage to pipeline."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = get_object_or_404(LeadPipeline, pk=pipeline_pk, org=org)

        serializer = LeadStageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage = serializer.save(pipeline=pipeline, org=org, created_by=request.user)
        return Response(LeadStageSerializer(stage).data, status=status.HTTP_201_CREATED)


class LeadStageDetailView(APIView):
    """Update or delete a stage."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Lead Stages"],
        request=LeadStageSerializer,
        responses={200: LeadStageSerializer},
    )
    def put(self, request, pk):
        """Update stage."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        stage = get_object_or_404(LeadStage, pk=pk, org=request.profile.org)
        serializer = LeadStageSerializer(stage, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage = serializer.save(updated_by=request.user)
        return Response(LeadStageSerializer(stage).data)

    @extend_schema(tags=["Lead Stages"], responses={204: None})
    def delete(self, request, pk):
        """Delete stage."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        stage = get_object_or_404(LeadStage, pk=pk, org=request.profile.org)

        # Check if stage has leads
        lead_count = stage.leads.count()
        if lead_count > 0:
            return Response(
                {
                    "error": f"Cannot delete stage with {lead_count} leads. Move leads first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LeadStageReorderView(APIView):
    """Bulk reorder stages in a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Lead Stages"],
        request=inline_serializer(
            name="StageReorderRequest",
            fields={"stage_ids": serializers.ListField(child=serializers.UUIDField())},
        ),
    )
    @transaction.atomic
    def post(self, request, pipeline_pk):
        """Reorder stages by providing ordered list of stage IDs."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = get_object_or_404(LeadPipeline, pk=pipeline_pk, org=org)

        stage_ids = request.data.get("stage_ids", [])

        # Validate all stages belong to this pipeline
        stages = LeadStage.objects.filter(pipeline=pipeline, id__in=stage_ids)
        if stages.count() != len(stage_ids):
            return Response(
                {"error": "Invalid stage IDs provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update order
        for order, stage_id in enumerate(stage_ids):
            LeadStage.objects.filter(id=stage_id).update(order=order)

        return Response({"message": "Stages reordered successfully"})
