"""``GET /api/leads/response-times/``: how fast each person answers new leads."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Profile
from common.permissions import HasOrgContext, is_org_admin
from leads.response import response_times

DEFAULT_DAYS = 30
MIN_DAYS = 7
MAX_DAYS = 90


def _days(value):
    if value in (None, ""):
        return DEFAULT_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = None
    if days is None or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValidationError(
            {"days": [f"Choose a whole number of days from {MIN_DAYS} to {MAX_DAYS}."]}
        )
    return days


DAYS_PARAMETER = OpenApiParameter(
    "days",
    OpenApiTypes.INT,
    OpenApiParameter.QUERY,
    description=f"Window in days, {MIN_DAYS} to {MAX_DAYS} (default {DEFAULT_DAYS})",
)


def _people(profile):
    """Whose numbers ``profile`` may see: every active member for an admin,
    else only their own."""
    if is_org_admin(profile):
        return list(
            Profile.objects.filter(org=profile.org, is_active=True).select_related(
                "user"
            )
        )
    return [profile]


class LeadResponseTimesView(APIView):
    """Per person: leads answered first, the median time to that answer, the
    share answered within 5 minutes and within an hour, and the unanswered
    leads assigned to them now.

    Comparing colleagues is an org aggregate, so an admin gets a row for every
    active member and anyone else gets only their own.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_response_times",
        parameters=[DAYS_PARAMETER],
        responses={
            200: inline_serializer(
                name="LeadResponseTimesResponse",
                fields={
                    "since": serializers.DateField(),
                    "rows": inline_serializer(
                        name="LeadResponseTimesRow",
                        many=True,
                        fields={
                            "profile": serializers.UUIDField(),
                            "name": serializers.CharField(),
                            "answered": serializers.IntegerField(),
                            "median_seconds": serializers.IntegerField(allow_null=True),
                            "within_5_minutes": serializers.IntegerField(),
                            "within_1_hour": serializers.IntegerField(),
                            "unanswered_now": serializers.IntegerField(),
                        },
                    ),
                },
            )
        },
    )
    def get(self, request):
        days = _days(request.query_params.get("days"))
        stats = response_times(request.profile.org, _people(request.profile), days)
        return Response({"since": stats["since"].isoformat(), "rows": stats["rows"]})
