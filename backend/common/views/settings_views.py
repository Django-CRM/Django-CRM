import json

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common import swagger_params
from common.models import APISettings, Profile, Tags
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    APISettingsListSerializer,
    APISettingsSerializer,
    APISettingsSwaggerSerializer,
    ProfileSerializer,
)
from common.utils import get_or_create_tags, handle_m2m_assignment
from common.validators import payload_id_list


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _tag_names(value):
    """Tag NAMES from the body: a list of strings, or that list as JSON text.

    Names, not ids, because that is the contract this endpoint has always had.
    A bare string is one name, as `payload_id_list` treats a bare id. Each name
    is checked with `Tags.name_error`, the rule the tags API applies, so a name
    that cannot be stored is a 400 rather than silently dropped.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text.startswith("["):
            value = [text]
        else:
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = None
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        raise DRFValidationError({"tags": ["tags must be a list of tag names."]})
    names = [name.strip() for name in value]
    for name in names:
        error = Tags.name_error(name)
        if error:
            raise DRFValidationError({"tags": [error]})
    return names


def _parse_relations(params):
    """(tag names, profile ids) from the body, or a 400 response.

    Parsed before anything is saved, so a malformed value leaves no
    half-written setting behind.
    """
    try:
        names = _tag_names(params.get("tags"))
        ids = payload_id_list(params.get("lead_assigned_to"), "lead_assigned_to")
    except DRFValidationError as exc:
        return (
            None,
            None,
            Response(
                {"error": True, "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )
    return names, ids, None


def _attach_relations(setting, names, ids, org):
    """Attach tags and assignees, both resolved only inside `org`.

    Tags are found or created by `Tags.slug_for` within the org. Assignees are
    the org's active profiles among `ids`; any other id is ignored, as on every
    other M2M write. `webforms/legacy.py` hands leads to these profiles, so a
    profile from another org here would receive that org's leads.
    """
    setting.tags.add(*get_or_create_tags(names, org))
    handle_m2m_assignment(
        setting,
        "lead_assigned_to",
        ids,
        Profile,
        org,
        extra_filters={"is_active": True},
    )


class DomainList(APIView):
    model = APISettings
    permission_classes = (IsAuthenticated, HasOrgContext)

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
        tag_names, assignee_ids, error = _parse_relations(params)
        if error:
            return error
        serializer = APISettingsSerializer(data=params)
        if serializer.is_valid():
            settings_obj = serializer.save(
                created_by=request.profile.user, org=request.profile.org
            )
            _attach_relations(
                settings_obj, tag_names, assignee_ids, request.profile.org
            )
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
    permission_classes = (IsAuthenticated, HasOrgContext)

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
        tag_names, assignee_ids, error = _parse_relations(params)
        if error:
            return error
        serializer = APISettingsSerializer(data=params, instance=api_setting)
        if serializer.is_valid():
            api_setting = serializer.save()
            api_setting.tags.clear()
            api_setting.lead_assigned_to.clear()
            _attach_relations(api_setting, tag_names, assignee_ids, request.profile.org)
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
