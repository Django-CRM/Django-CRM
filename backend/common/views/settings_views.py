from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common import swagger_params
from common.models import APISettings, Profile, Tags
from common.permissions import is_org_admin
from common.serializer import (
    APISettingsListSerializer,
    APISettingsSerializer,
    APISettingsSwaggerSerializer,
    ProfileSerializer,
)


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


class DomainList(APIView):
    model = APISettings
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=["Settings"],
        operation_id="api_settings_list",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="DomainListResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "api_settings": APISettingsListSerializer(many=True),
                    "users": ProfileSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        api_settings = APISettings.objects.filter(org=request.profile.org)
        users = Profile.objects.filter(
            is_active=True, org=request.profile.org
        ).order_by("user__email")
        return Response(
            {
                "error": False,
                # Context carries the request so the serializer can decide
                # whether this caller may read `apikey` back. Without it the
                # key is withheld from everyone, admins included.
                "api_settings": APISettingsListSerializer(
                    api_settings, many=True, context={"request": request}
                ).data,
                "users": ProfileSerializer(users, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Settings"],
        operation_id="api_settings_create",
        parameters=swagger_params.organization_params,
        request=APISettingsSwaggerSerializer,
        responses={
            201: inline_serializer(
                name="DomainCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        # Creating a setting mints an API key that posts leads into the org, so
        # the write half of this endpoint is admin-only while the read half
        # stays open to every member.
        if not is_org_admin(request.profile):
            return _admin_required()
        params = request.data
        assign_to_list = []
        if params.get("lead_assigned_to"):
            assign_to_list = params.get("lead_assigned_to")
        serializer = APISettingsSerializer(data=params)
        if serializer.is_valid():
            settings_obj = serializer.save(
                created_by=request.profile.user, org=request.profile.org
            )
            if params.get("tags"):
                tags = params.get("tags")
                for tag in tags:
                    tag_obj = Tags.objects.filter(name=tag).first()
                    if not tag_obj:
                        tag_obj = Tags.objects.create(name=tag)
                    settings_obj.tags.add(tag_obj)
            if assign_to_list:
                settings_obj.lead_assigned_to.add(*assign_to_list)
            return Response(
                {"error": False, "message": "API key added sucessfully"},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class DomainDetailView(APIView):
    model = APISettings
    permission_classes = (IsAuthenticated,)

    def get_object(self, pk):
        return get_object_or_404(APISettings, pk=pk, org=self.request.profile.org)

    @extend_schema(
        tags=["Settings"],
        operation_id="api_settings_retrieve",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="DomainDetailResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "domain": APISettingsListSerializer(),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        api_setting = self.get_object(pk)
        return Response(
            {
                "error": False,
                "domain": APISettingsListSerializer(
                    api_setting, context={"request": request}
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Settings"],
        operation_id="api_settings_update",
        parameters=swagger_params.organization_params,
        request=APISettingsSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="DomainUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, **kwargs):
        if not is_org_admin(request.profile):
            return _admin_required()
        api_setting = self.get_object(pk)
        params = request.data
        assign_to_list = []
        if params.get("lead_assigned_to"):
            assign_to_list = params.get("lead_assigned_to")
        serializer = APISettingsSerializer(data=params, instance=api_setting)
        if serializer.is_valid():
            api_setting = serializer.save()
            api_setting.tags.clear()
            api_setting.lead_assigned_to.clear()
            if params.get("tags"):
                tags = params.get("tags")
                for tag in tags:
                    tag_obj = Tags.objects.filter(name=tag).first()
                    if not tag_obj:
                        tag_obj = Tags.objects.create(name=tag)
                    api_setting.tags.add(tag_obj)
            if assign_to_list:
                api_setting.lead_assigned_to.add(*assign_to_list)
            return Response(
                {"error": False, "message": "API setting Updated sucessfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Settings"],
        parameters=swagger_params.organization_params,
        request=APISettingsSwaggerSerializer,
        description="Partial API Settings Update",
        responses={
            200: inline_serializer(
                name="DomainPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, **kwargs):
        """Handle partial updates to API settings."""
        if not is_org_admin(request.profile):
            return _admin_required()
        api_setting = self.get_object(pk)
        params = request.data
        serializer = APISettingsSerializer(
            data=params, instance=api_setting, partial=True
        )
        if serializer.is_valid():
            api_setting = serializer.save()
            return Response(
                {"error": False, "message": "API setting Updated successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Settings"],
        operation_id="api_settings_destroy",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="DomainDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, **kwargs):
        if not is_org_admin(request.profile):
            return _admin_required()
        api_setting = self.get_object(pk)
        if api_setting:
            api_setting.delete()
        return Response(
            {"error": False, "message": "API setting deleted sucessfully"},
            status=status.HTTP_200_OK,
        )
