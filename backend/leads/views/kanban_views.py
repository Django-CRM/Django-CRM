"""
Kanban views for lead management.
Supports both status-based (default) and custom pipeline-based kanban boards.
"""

from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.kanban import place_in_column
from common.permissions import HasOrgContext, is_org_admin
from common.utils import LEAD_STATUS
from common.validators import date_param, uuid_param
from leads.access import has_lead_access, visible_leads_qs
from leads.models import Lead, LeadPipeline, LeadStage
from leads.serializer import (
    LeadKanbanCardSerializer,
    LeadMoveSerializer,
    LeadPipelineListSerializer,
    LeadPipelineSerializer,
    LeadStageSerializer,
)
from leads.workflow import IRREVERSIBLE_STATUSES


def _board_leads(leads):
    """The leads in ``leads`` that the board shows, and so can be moved.

    Removing a stage or a pipeline is refused while these remain, with a
    message telling the admin to move them. A converted lead can never be
    moved (see ``LeadMoveView``) and an inactive one is not on the board, so
    counting either refused a delete the admin had no way to satisfy.
    """
    return leads.filter(is_active=True).exclude(status__in=IRREVERSIBLE_STATUSES)


def _make_only_default(pipeline_qs, validated_data, keep_pk=None):
    """Clear the org's other default before this pipeline takes it.

    ``unique_default_pipeline_per_org`` allows one per org, so saving a second
    default was an IntegrityError and a 500. Taking it demotes the old one.
    """
    if validated_data.get("is_default"):
        pipeline_qs.filter(is_default=True).exclude(pk=keep_pk).update(is_default=False)


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
        pipeline_id = uuid_param(request.query_params, "pipeline_id")

        # Base queryset: the leads the list shows this caller, so the lane
        # counts agree with it.
        queryset = (
            visible_leads_qs(request.profile, request.user)
            .filter(is_active=True)
            .exclude(status="converted")
            .select_related("created_by", "stage")
            .prefetch_related("assigned_to", "tags")
        )

        # Apply search/filters
        queryset = self._apply_filters(queryset, request.query_params)

        if pipeline_id:
            # Pipeline-based kanban
            return self._get_pipeline_kanban(queryset, pipeline_id, request)
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
            queryset = queryset.filter(created_at__date__gte=created_at_gte)
        created_at_lte = date_param(params, "created_at__lte")
        if created_at_lte:
            queryset = queryset.filter(created_at__date__lte=created_at_lte)
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

    def _get_pipeline_kanban(self, queryset, pipeline_id, request):
        """Build kanban data using LeadPipeline stages as columns."""
        pipeline = get_object_or_404(
            LeadPipelineListSerializer.with_counts(
                LeadPipeline.objects.all(), request.profile, request.user
            ),
            pk=pipeline_id,
            org=request.profile.org,
            is_active=True,
        )

        # A lead is created with stage=NULL (nothing routes it into a
        # pipeline), so a pipeline board built only from staged leads is empty
        # for every lead an org adds after applying a pack. The unstaged leads
        # ride along as their own group, which is how a lead enters the
        # pipeline: moving it from there to a stage.
        unstaged = queryset.filter(stage__isnull=True).order_by(
            "kanban_order", "-created_at"
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
                "unstaged": {
                    "lead_count": unstaged.count(),
                    "leads": LeadKanbanCardSerializer(unstaged[:100], many=True).data,
                },
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
        lead = get_object_or_404(
            Lead.objects.select_for_update(of=("self",)).select_related(
                "stage__pipeline"
            ),
            pk=pk,
            org=org,
        )

        # The lead write rule, from the one place it is defined. A lead the
        # caller may not open is a 404, as if it did not exist, so a move
        # cannot confirm the id of a lead the board withholds.
        if not has_lead_access(request.profile, request.user, lead):
            raise Http404

        serializer = LeadMoveSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data

        # "converted" can only be reached through the conversion service,
        # which creates the account, contact and deal, and can never be left
        # (see leads/workflow.py). A board move is a status write that runs
        # neither that service nor LeadCreateSerializer.validate_status, so
        # without these two checks a drag set "converted" with nothing
        # downstream of it, or dragged a converted lead back into the working
        # list with its account, contact and deal already created.
        if lead.status in IRREVERSIBLE_STATUSES:
            return Response(
                {"error": True, "errors": f"A {lead.status} lead cannot be moved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if data.get("status") in IRREVERSIBLE_STATUSES:
            return Response(
                {
                    "error": True,
                    "errors": "Convert a lead from its own page, not from the board.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Handle stage change
        if "stage_id" in data:
            if data["stage_id"]:
                # Same org, and a pipeline that still exists: a soft-deleted
                # pipeline is gone from the picker and the board, so its
                # stages must not keep accepting leads by id.
                stage = get_object_or_404(
                    LeadStage.objects.select_related("pipeline"),
                    pk=data["stage_id"],
                    org=org,
                    pipeline__is_active=True,
                )

                # A lead already in a pipeline moves within that pipeline. An
                # unstaged lead may enter any one, which is how it gets onto a
                # board in the first place.
                if lead.stage_id and lead.stage.pipeline_id != stage.pipeline_id:
                    return Response(
                        {
                            "error": True,
                            "errors": f"This lead is in the {lead.stage.pipeline.name} "
                            "pipeline. Move it to one of that pipeline's stages.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if stage.maps_to_status in IRREVERSIBLE_STATUSES:
                    return Response(
                        {
                            "error": True,
                            "errors": f"Stage '{stage.name}' converts the lead. "
                            "Convert it from its own page instead.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Check WIP limit
                if stage.wip_limit:
                    current_count = stage.leads.exclude(pk=lead.pk).count()
                    if current_count >= stage.wip_limit:
                        return Response(
                            {
                                "error": True,
                                "errors": f"Stage '{stage.name}' has reached its WIP limit of {stage.wip_limit}",
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
        pipelines = LeadPipelineListSerializer.with_counts(
            LeadPipeline.objects.filter(org=org, is_active=True),
            request.profile,
            request.user,
        )
        serializer = LeadPipelineListSerializer(pipelines, many=True)
        return Response({"pipelines": serializer.data})

    @extend_schema(
        tags=["Lead Pipelines"],
        request=LeadPipelineSerializer,
        responses={201: LeadPipelineSerializer},
    )
    @transaction.atomic
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

        _make_only_default(
            LeadPipeline.objects.filter(org=org), serializer.validated_data
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
                # No `maps_to_status`: the board refuses a move into a stage
                # that maps to "converted", so seeding one built a Won column
                # nobody could enter.
                {
                    "name": "Won",
                    "order": 5,
                    "color": "#22C55E",
                    "stage_type": "won",
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
            LeadPipelineSerializer(pipeline, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class LeadPipelineDetailView(APIView):
    """Retrieve, update, delete a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk, org):
        # A deleted pipeline is gone from the list and the board, so it is gone
        # here too, rather than readable and editable by id.
        return get_object_or_404(LeadPipeline, pk=pk, org=org, is_active=True)

    @extend_schema(tags=["Lead Pipelines"], responses={200: LeadPipelineSerializer})
    def get(self, request, pk):
        """Get pipeline details with all stages."""
        pipeline = self.get_object(pk, request.profile.org)
        return Response(
            LeadPipelineSerializer(pipeline, context={"request": request}).data
        )

    @extend_schema(
        tags=["Lead Pipelines"],
        request=LeadPipelineSerializer,
        responses={200: LeadPipelineSerializer},
    )
    @transaction.atomic
    def put(self, request, pk):
        """Update pipeline."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = self.get_object(pk, org)
        serializer = LeadPipelineSerializer(pipeline, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _make_only_default(
            LeadPipeline.objects.filter(org=org),
            serializer.validated_data,
            keep_pk=pipeline.pk,
        )
        pipeline = serializer.save(updated_by=request.user)
        return Response(
            LeadPipelineSerializer(pipeline, context={"request": request}).data
        )

    @extend_schema(tags=["Lead Pipelines"], responses={204: None})
    def delete(self, request, pk):
        """Delete pipeline (soft delete by setting is_active=False)."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        pipeline = self.get_object(pk, request.profile.org)

        lead_count = _board_leads(
            Lead.objects.filter(org=request.profile.org, stage__pipeline=pipeline)
        ).count()
        if lead_count > 0:
            return Response(
                {
                    "error": f"This pipeline still has {lead_count} lead(s). "
                    "Move them out of its stages first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A deleted pipeline cannot keep the org's one default slot.
        pipeline.is_active = False
        pipeline.is_default = False
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
        pipeline = get_object_or_404(
            LeadPipeline, pk=pipeline_pk, org=org, is_active=True
        )

        serializer = LeadStageSerializer(
            data=request.data, context={"request": request, "pipeline": pipeline}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A stage added without an order goes last, not first.
        extra = {}
        if "order" not in serializer.validated_data:
            last = pipeline.stages.aggregate(last=Max("order"))["last"]
            extra["order"] = 0 if last is None else last + 1
        stage = serializer.save(
            pipeline=pipeline, org=org, created_by=request.user, **extra
        )
        return Response(
            LeadStageSerializer(stage, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


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

        stage = get_object_or_404(
            LeadStage, pk=pk, org=request.profile.org, pipeline__is_active=True
        )
        serializer = LeadStageSerializer(
            stage, data=request.data, partial=True, context={"request": request}
        )

        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage = serializer.save(updated_by=request.user)
        return Response(LeadStageSerializer(stage, context={"request": request}).data)

    @extend_schema(tags=["Lead Stages"], responses={204: None})
    def delete(self, request, pk):
        """Delete stage."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        stage = get_object_or_404(
            LeadStage, pk=pk, org=request.profile.org, pipeline__is_active=True
        )

        # The leads left out of the count lose their stage (SET_NULL).
        lead_count = _board_leads(stage.leads.all()).count()
        if lead_count > 0:
            return Response(
                {
                    "error": f"This stage still has {lead_count} lead(s). "
                    "Move them to another stage first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        stage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LeadStageReorderSerializer(serializers.Serializer):
    stage_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class LeadStageReorderView(APIView):
    """Bulk reorder stages in a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Lead Stages"],
        request=LeadStageReorderSerializer,
    )
    @transaction.atomic
    def post(self, request, pipeline_pk):
        """Reorder stages by providing ordered list of stage IDs."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        org = request.profile.org
        pipeline = get_object_or_404(
            LeadPipeline, pk=pipeline_pk, org=org, is_active=True
        )

        body = LeadStageReorderSerializer(data=request.data)
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
