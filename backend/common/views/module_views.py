from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.module_settings import change_modules, modules_payload
from common.permissions import HasOrgContext, may_configure

_ModulesResponse = inline_serializer(
    name="OrgModulesResponse",
    fields={
        "modules": inline_serializer(
            name="OrgModuleRow",
            many=True,
            fields={
                "key": serializers.CharField(),
                "label": serializers.CharField(),
                "group": serializers.ChoiceField(choices=["sell", "serve", "bill"]),
                "covers": serializers.CharField(),
                "stops": serializers.CharField(),
                "enabled": serializers.BooleanField(),
                "available": serializers.BooleanField(),
                "needs": serializers.CharField(allow_null=True),
            },
        ),
        "can_edit": serializers.BooleanField(),
    },
)


class OrgModulesView(APIView):
    """Which parts of the CRM an org uses. Any member reads, only an admin
    writes. The org is always ``request.profile.org``."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["organization"],
        operation_id="org_modules_retrieve",
        responses={200: _ModulesResponse},
    )
    def get(self, request):
        return Response(modules_payload(request.profile.org, request.profile))

    @extend_schema(
        tags=["organization"],
        operation_id="org_modules_update",
        request=dict,
        responses={200: _ModulesResponse},
    )
    def put(self, request):
        profile = request.profile
        if not may_configure(profile):
            return Response(
                {"error": True, "errors": "Only admins can switch modules on or off."},
                status=status.HTTP_403_FORBIDDEN,
            )
        errors = change_modules(profile.org, profile, request.data, request=request)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        org = profile.org
        org.refresh_from_db(fields=["modules_off", "module_order", "modules_on_at"])
        return Response(modules_payload(org, profile))
