"""`/api/devices/`: the phone app registers its push token for the member
signed in on it, and drops it on sign-out. Read by `common.push`.

The token stays on the server: neither method answers with it.
"""

from django.core.validators import RegexValidator
from django.utils import timezone
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from common.models import DeviceToken
from common.permissions import HasOrgContext
from common.push import DEVICES_PER_MEMBER

# Far above an FCM token (about 160 characters). FCM's alphabet is ASCII, so
# this many characters are as many bytes, and the unique (org, token) index
# entry always fits a Postgres B-tree page (2,704 bytes).
TOKEN_MAX = 2048
FCM_TOKEN = RegexValidator(r"\A[A-Za-z0-9_:\-]+\Z", "This is not a push token.")


class DeviceTokenDeleteSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=TOKEN_MAX, validators=[FCM_TOKEN])


class DeviceTokenSerializer(DeviceTokenDeleteSerializer):
    platform = serializers.ChoiceField(choices=DeviceToken.PLATFORMS)
    app_version = serializers.CharField(
        max_length=32, required=False, allow_blank=True, default=""
    )


class DeviceThrottle(UserRateThrottle):
    """A phone registers after each sign-in, org switch and token refresh;
    this is the backstop for one that loops."""

    scope = "device_token"
    rate = "60/minute"


class _BodyOnDelete(AutoSchema):
    """drf-spectacular drops a DELETE body; this one is the request's contract."""

    def _get_request_body(self, direction="request"):
        if self.method != "DELETE":
            return super()._get_request_body(direction)
        self.method = "POST"
        try:
            return super()._get_request_body(direction)
        finally:
            self.method = "DELETE"


class DeviceTokenView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)
    throttle_classes = (DeviceThrottle,)
    schema = _BodyOnDelete()

    @extend_schema(
        tags=["profile"],
        operation_id="devices_register",
        request=DeviceTokenSerializer,
        responses={204: None},
    )
    def post(self, request):
        """Register or refresh this phone's push token for the caller. A token
        another member registered in this org moves to the caller, and the
        caller keeps only their newest `DEVICES_PER_MEMBER` phones here."""
        body = DeviceTokenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        org = request.profile.org
        DeviceToken.objects.update_or_create(
            org=org,
            token=body.validated_data["token"],
            defaults={
                "user": request.user,
                "platform": body.validated_data["platform"],
                "app_version": body.validated_data["app_version"],
                "last_seen_at": timezone.now(),
            },
        )
        mine = DeviceToken.objects.filter(org=org, user=request.user)
        newest = mine.order_by("-last_seen_at", "-created_at").values_list(
            "pk", flat=True
        )[:DEVICES_PER_MEMBER]
        mine.exclude(pk__in=list(newest)).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["profile"],
        operation_id="devices_forget",
        request=DeviceTokenDeleteSerializer,
        responses={204: None},
    )
    def delete(self, request):
        """Drop the caller's own row for this token in this org, if any."""
        body = DeviceTokenDeleteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        DeviceToken.objects.filter(
            org=request.profile.org,
            user=request.user,
            token=body.validated_data["token"],
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
