"""Deal pipelines and their stages: read by every member, written by admins.

Shared config, so the read is wide (every member's board and deal form needs
the stages) and every write is admin-only. Each pipeline has to keep at least
one open, one won and one lost stage, because a deal must always have somewhere
to be worked, won and lost; every write that could break that holds a row lock
on the pipeline so two admins cannot each remove "the other" last stage.
"""

import re

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from opportunity.models import DealPipeline, DealStage, Opportunity
from opportunity.serializer import DealPipelineSerializer, DealStageSerializer
from opportunity.workflow import STAGE_KINDS

_ADMIN_ONLY = "Only admins can change deal pipelines."


def _require_admin(request):
    if not (is_org_admin(request.profile) or request.user.is_superuser):
        raise PermissionDenied(_ADMIN_ONLY)


def _pipelines(org):
    return DealPipeline.objects.filter(org=org).prefetch_related("stages")


def _locked_pipeline(org, pk):
    """The org's pipeline `pk`, row-locked for the rest of the transaction."""
    return get_object_or_404(DealPipeline.objects.select_for_update(), pk=pk, org=org)


def _pipeline_response(pipeline, org, status_code=status.HTTP_200_OK):
    fresh = _pipelines(org).get(pk=pipeline.pk)
    return Response(
        DealPipelineSerializer(fresh, context={"org": org}).data, status=status_code
    )


def _deals_in(stage):
    return Opportunity.objects.filter(pipeline_id=stage.pipeline_id, stage=stage.code)


def _last_of_kind(stage):
    """Whether `stage` is the only one of its kind left in its pipeline."""
    return not (
        DealStage.objects.filter(pipeline_id=stage.pipeline_id, kind=stage.kind)
        .exclude(pk=stage.pk)
        .exists()
    )


def _kind_label(kind):
    return dict(STAGE_KINDS)[kind].lower()


def _new_code(label, taken):
    """A stage code derived from `label`, unique among `taken`."""
    base = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")[:56] or "STAGE"
    code, suffix = base, 2
    while code in taken:
        code = f"{base}_{suffix}"
        suffix += 1
    return code


def _refuse(message):
    return Response(
        {"error": True, "errors": message}, status=status.HTTP_400_BAD_REQUEST
    )


class DealPipelineListView(APIView):
    """GET every pipeline with its stages; POST (admin) a new one."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_pipelines_list",
        responses={
            200: inline_serializer(
                name="DealPipelineListResponse",
                fields={"pipelines": DealPipelineSerializer(many=True)},
            )
        },
    )
    def get(self, request):
        org = request.profile.org
        # An org created after the data migration gets its default here, on
        # the first read, the same way the business-hours calendar does.
        DealPipeline.default_for(org)
        return Response(
            {
                "pipelines": DealPipelineSerializer(
                    _pipelines(org), many=True, context={"org": org}
                ).data
            }
        )

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_pipelines_create",
        request=DealPipelineSerializer,
        responses={201: DealPipelineSerializer},
    )
    def post(self, request):
        _require_admin(request)
        org = request.profile.org
        serializer = DealPipelineSerializer(data=request.data, context={"org": org})
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            pipeline = serializer.save(org=org)
            # Born with the default stages, so it satisfies the open/won/lost
            # rule from its first moment and old clients' codes mean something.
            pipeline.seed_stages()
        return _pipeline_response(pipeline, org, status.HTTP_201_CREATED)


class DealPipelineDetailView(APIView):
    """GET one pipeline; PATCH (admin) its name; DELETE (admin) it."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_pipelines_retrieve",
        responses={200: DealPipelineSerializer},
    )
    def get(self, request, pk):
        org = request.profile.org
        pipeline = get_object_or_404(_pipelines(org), pk=pk)
        return Response(DealPipelineSerializer(pipeline, context={"org": org}).data)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_pipelines_update",
        request=DealPipelineSerializer,
        responses={200: DealPipelineSerializer},
    )
    def patch(self, request, pk):
        _require_admin(request)
        org = request.profile.org
        pipeline = get_object_or_404(DealPipeline, pk=pk, org=org)
        serializer = DealPipelineSerializer(
            pipeline, data=request.data, partial=True, context={"org": org}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return _pipeline_response(pipeline, org)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_pipelines_destroy",
        responses={204: None},
    )
    @transaction.atomic
    def delete(self, request, pk):
        _require_admin(request)
        org = request.profile.org
        pipeline = _locked_pipeline(org, pk)
        if pipeline.is_default:
            return _refuse(
                "The default pipeline cannot be deleted: deals that name no "
                "pipeline land in it."
            )
        deal_count = Opportunity.objects.filter(pipeline=pipeline).count()
        if deal_count:
            return _refuse(
                f"This pipeline still holds {deal_count} deal(s). Move them to "
                "another pipeline first."
            )
        pipeline.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DealStageCreateView(APIView):
    """POST (admin) a stage onto the end of a pipeline."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_stages_create",
        request=DealStageSerializer,
        responses={201: DealPipelineSerializer},
    )
    @transaction.atomic
    def post(self, request, pk):
        _require_admin(request)
        org = request.profile.org
        pipeline = _locked_pipeline(org, pk)
        serializer = DealStageSerializer(
            data=request.data, context={"pipeline": pipeline}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        existing = list(pipeline.stages.values_list("code", "order"))
        serializer.save(
            org=org,
            pipeline=pipeline,
            code=_new_code(
                serializer.validated_data["label"], {code for code, _o in existing}
            ),
            order=max((order for _c, order in existing), default=0) + 1,
        )
        return _pipeline_response(pipeline, org, status.HTTP_201_CREATED)


class DealStageReorderView(APIView):
    """POST (admin) the pipeline's complete stage list in its new order."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_stages_reorder",
        request=inline_serializer(
            name="DealStageReorderRequest",
            fields={"stage_ids": serializers.ListField(child=serializers.UUIDField())},
        ),
        responses={200: DealPipelineSerializer},
    )
    @transaction.atomic
    def post(self, request, pk):
        _require_admin(request)
        org = request.profile.org
        pipeline = _locked_pipeline(org, pk)
        field = serializers.ListField(child=serializers.UUIDField())
        try:
            stage_ids = field.run_validation(request.data.get("stage_ids"))
        except serializers.ValidationError as exc:
            return Response(
                {"error": True, "errors": {"stage_ids": exc.detail}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        stages = {stage.id: stage for stage in pipeline.stages.all()}
        # Exactly the pipeline's stages, each once. A partial list would leave
        # the rest at stale positions that collide with the new ones, and an id
        # from elsewhere (another pipeline, another org) is simply not here.
        if len(stage_ids) != len(set(stage_ids)) or set(stage_ids) != set(stages):
            return _refuse(
                "Send every stage of this pipeline exactly once, in the new order."
            )
        for position, stage_id in enumerate(stage_ids, start=1):
            stage = stages[stage_id]
            if stage.order != position:
                stage.order = position
                stage.save(update_fields=["order", "updated_at", "updated_by"])
        return _pipeline_response(pipeline, org)


class DealStageDetailView(APIView):
    """PATCH (admin) a stage's label, kind or rotting days; DELETE (admin) it."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _locked_stage(self, request, pk):
        org = request.profile.org
        stage = get_object_or_404(DealStage, pk=pk, org=org)
        # Lock the pipeline, not only the stage: the at-least-one-of-each-kind
        # rule is about the stage's siblings. Re-read once the lock is held,
        # so the checks see what a concurrent edit left behind.
        _locked_pipeline(org, stage.pipeline_id)
        stage.refresh_from_db()
        return stage

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_stages_update",
        request=DealStageSerializer,
        responses={200: DealPipelineSerializer},
    )
    @transaction.atomic
    def patch(self, request, pk):
        _require_admin(request)
        stage = self._locked_stage(request, pk)
        serializer = DealStageSerializer(
            stage, data=request.data, partial=True, context={"pipeline": stage.pipeline}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_kind = serializer.validated_data.get("kind", stage.kind)
        if new_kind != stage.kind:
            if _last_of_kind(stage):
                return _refuse(
                    f"A pipeline needs at least one {_kind_label(stage.kind)} "
                    "stage, and this is its last."
                )
            # Changing what a stage means would silently win, lose or reopen
            # every deal in it, with no close date, amount or closer recorded.
            if _deals_in(stage).exists():
                return _refuse(
                    "Move the deals out of this stage before changing its kind."
                )
        serializer.save()
        return _pipeline_response(stage.pipeline, request.profile.org)

    @extend_schema(
        tags=["Deal Pipelines"],
        operation_id="deal_stages_destroy",
        responses={204: None},
    )
    @transaction.atomic
    def delete(self, request, pk):
        _require_admin(request)
        stage = self._locked_stage(request, pk)
        deal_count = _deals_in(stage).count()
        if deal_count:
            return _refuse(
                f"This stage still holds {deal_count} deal(s). Move them to "
                "another stage first."
            )
        if _last_of_kind(stage):
            return _refuse(
                f"A pipeline needs at least one {_kind_label(stage.kind)} "
                "stage, and this is its last."
            )
        stage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
