from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.org_time import selectable_timezones
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    HELP_CENTER_SLUG_TAKEN,
    HelpCenterSettingsSerializer,
    OrgSettingsSerializer,
)


class OrgSettingsView(APIView):
    """
    API endpoint for org settings (currency, country, locale).

    GET: Returns current org settings
    PATCH: Updates org settings (admin only)
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        """Get current organization settings."""
        if not request.profile:
            return Response(
                {"error": "Organization context required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        org = request.profile.org
        serializer = OrgSettingsSerializer(org, context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        """Update organization settings (admin only)."""
        if not request.profile:
            return Response(
                {"error": "Organization context required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": "Only admins can update organization settings"},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = request.profile.org
        serializer = OrgSettingsSerializer(
            org, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class HelpCenterSettingsView(APIView):
    """The public help center switch and its address. Read wide, write narrow.

    Any member may read it, so the settings hub can show whether the help
    center is on. Only an admin (or a superuser) may change it, because turning
    it on publishes the org's approved articles to anyone on the internet.

    The org is always `request.profile.org`; nothing in the path or body names
    one, so a caller can only ever reach their own.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def _can_edit(self, request):
        return is_org_admin(request.profile) or request.user.is_superuser

    def _payload(self, request, org):
        data = HelpCenterSettingsSerializer(org).data
        data["can_edit"] = self._can_edit(request)
        return data

    @extend_schema(
        tags=["organization"],
        operation_id="help_center_settings_retrieve",
        responses={200: HelpCenterSettingsSerializer},
    )
    def get(self, request):
        return Response(self._payload(request, request.profile.org))

    @extend_schema(
        tags=["organization"],
        operation_id="help_center_settings_update",
        request=HelpCenterSettingsSerializer,
        responses={200: HelpCenterSettingsSerializer},
    )
    def patch(self, request):
        if not self._can_edit(request):
            return Response(
                {"error": True, "errors": "Only admins can change the help center."},
                status=status.HTTP_403_FORBIDDEN,
            )
        org = request.profile.org
        serializer = HelpCenterSettingsSerializer(org, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Two admins claiming one address at once both pass the serializer's
            # check; the unique constraint refuses the second, and this turns
            # that refusal into the same answer the check gives.
            with transaction.atomic():
                serializer.save()
        except IntegrityError:
            return Response(
                {"help_center_slug": [HELP_CENTER_SLUG_TAKEN]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self._payload(request, org))


class TimezoneListView(APIView):
    """The zone names a client may offer when creating or editing an org.

    Served rather than built client-side because the two clients would not
    otherwise agree: a browser's ``Intl.supportedValuesOf('timeZone')`` answers
    "Asia/Calcutta" where this database also knows "Asia/Kolkata", and a select
    that cannot find the stored value submits its first option instead. See
    ``common.org_time.selectable_timezones``.

    Each entry carries the zone's current UTC offset so a client can preselect a
    sensible default. Mobile cannot read the device's IANA name without a
    platform package, but it can always read the device's offset.

    Authenticated but deliberately org-free: the first caller is a user creating
    their very first organization, who has no org claim yet. The list is the same
    for every tenant and contains no tenant data, so there is nothing here to
    scope.
    """

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=["organization"],
        operation_id="timezone_list",
        responses={
            200: inline_serializer(
                name="TimezoneListResponse",
                fields={
                    "timezones": serializers.ListField(
                        child=inline_serializer(
                            name="TimezoneOption",
                            fields={
                                "name": serializers.CharField(),
                                "offset_minutes": serializers.IntegerField(),
                            },
                        )
                    )
                },
            )
        },
    )
    def get(self, request):
        return Response({"timezones": selectable_timezones()})
