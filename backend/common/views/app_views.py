"""Industry apps: ``/api/apps/``.

Any member lists the apps; only an org admin previews, installs, updates or
removes one. The org is always ``request.profile.org``, and an app id is a key
of the pack registry, never a path, so an unknown one is a 404.
"""

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.apps_catalogue import (
    AppConflict,
    AppNotFound,
    app_payload,
    apps_payload,
    install_app,
    remove_app,
    upgrade_app,
)
from common.packs.loader import get_pack
from common.packs.planner import ACTIONS, plan_app
from common.permissions import HasOrgContext, may_admin


def _refusal(message, code):
    return Response({"error": True, "errors": message}, status=code)


def _admin_only(request):
    if not may_admin(request):
        return _refusal(
            "Only an organization admin can do this.", status.HTTP_403_FORBIDDEN
        )
    return None


def _unknown():
    return _refusal("Unknown app.", status.HTTP_404_NOT_FOUND)


class AppInstalledBySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class IndustryAppSerializer(serializers.Serializer):
    """Response only: one app as ``app_payload`` builds it."""

    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    version = serializers.IntegerField()
    # "installed", "removed" or "not_installed".
    status = serializers.CharField()
    installed_version = serializers.IntegerField(allow_null=True)
    update_available = serializers.BooleanField()
    skipped_modules = serializers.ListField(child=serializers.CharField())
    installed_at = serializers.DateTimeField(allow_null=True)
    installed_by_details = AppInstalledBySerializer(allow_null=True)


class AppPreviewItemSerializer(serializers.Serializer):
    type = serializers.CharField()
    target = serializers.CharField(allow_null=True)
    label = serializers.CharField()
    effect = serializers.CharField()


class AppSampleDataSerializer(serializers.Serializer):
    offered = serializers.BooleanField()
    default_on = serializers.BooleanField()
    records = serializers.IntegerField()


AppListResponse = inline_serializer(
    name="AppListResponse", fields={"apps": IndustryAppSerializer(many=True)}
)
AppPreviewResponse = inline_serializer(
    name="AppPreviewResponse",
    fields={
        "action": serializers.CharField(),
        "items": AppPreviewItemSerializer(many=True),
        "sample_data": AppSampleDataSerializer(),
    },
)
AppActionResponse = inline_serializer(
    name="AppActionResponse", fields={"app": IndustryAppSerializer()}
)


class AppListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Apps"], operation_id="apps_list", responses={200: AppListResponse}
    )
    def get(self, request):
        return Response(apps_payload(request.profile.org))


class AppPreviewView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Apps"],
        operation_id="apps_preview",
        parameters=[OpenApiParameter("action", str, OpenApiParameter.QUERY)],
        responses={200: AppPreviewResponse},
    )
    def get(self, request, app_id):
        denied = _admin_only(request)
        if denied:
            return denied
        if get_pack(app_id) is None:
            return _unknown()
        action = request.query_params.get("action")
        if action not in ACTIONS:
            return _refusal(
                "Send action=install, upgrade or remove.",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(plan_app(request.profile.org, app_id, action))
        except AppConflict as exc:
            return _refusal(str(exc), status.HTTP_409_CONFLICT)


class _InstallBody(serializers.Serializer):
    sample_data = serializers.BooleanField(required=False)


class _AppActionView(APIView):
    """POST runs the action and answers the app as the list shows it."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def act(self, request, profile, app_id):
        raise NotImplementedError

    @extend_schema(tags=["Apps"], request=dict, responses={200: AppActionResponse})
    def post(self, request, app_id):
        denied = _admin_only(request)
        if denied:
            return denied
        pack = get_pack(app_id)
        if pack is None:
            return _unknown()
        profile = request.profile
        try:
            row = self.act(request, profile, app_id)
        except AppNotFound:
            return _unknown()
        except AppConflict as exc:
            return _refusal(str(exc), status.HTTP_409_CONFLICT)
        return Response({"app": app_payload(profile.org, pack, row)})


class AppInstallView(_AppActionView):
    def act(self, request, profile, app_id):
        body = _InstallBody(data=request.data)
        body.is_valid(raise_exception=True)
        sample = body.validated_data.get("sample_data")
        if sample is None:
            preview = plan_app(profile.org, app_id, "install")
            sample = preview["sample_data"]["default_on"]
        return install_app(profile.org, app_id, profile, sample, request=request)


class AppUpgradeView(_AppActionView):
    def act(self, request, profile, app_id):
        return upgrade_app(profile.org, app_id, profile, request=request)


class AppRemoveView(_AppActionView):
    def act(self, request, profile, app_id):
        return remove_app(profile.org, app_id, profile, request=request)
