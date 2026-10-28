"""``GET /api/leads/outcomes/``: how many leads each person wins and loses."""

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from leads.outcomes import lead_outcomes
from leads.views.response_views import DAYS_PARAMETER, _days, _people


def _numbers():
    return {
        "won": serializers.IntegerField(),
        "lost": serializers.IntegerField(),
        "win_rate": serializers.IntegerField(allow_null=True),
        "median_seconds_to_win": serializers.IntegerField(allow_null=True),
        "new_leads": serializers.IntegerField(),
    }


class LeadOutcomesView(APIView):
    """Per person: leads won and lost inside the window among those assigned
    to them now, the win rate, the median time from arrival to won, and the
    leads that arrived inside the window.

    Comparing colleagues is an org aggregate, so an admin gets a row for every
    active member and the org's totals, and anyone else gets only their own
    row and no totals.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        operation_id="leads_outcomes",
        parameters=[DAYS_PARAMETER],
        responses={
            200: inline_serializer(
                name="LeadOutcomesResponse",
                fields={
                    "since": serializers.DateField(),
                    "measured_since": serializers.DateField(),
                    "rows": inline_serializer(
                        name="LeadOutcomesRow",
                        many=True,
                        fields={
                            "profile": serializers.UUIDField(),
                            "name": serializers.CharField(),
                            **_numbers(),
                        },
                    ),
                    "totals": inline_serializer(
                        name="LeadOutcomesTotals", allow_null=True, fields=_numbers()
                    ),
                },
            )
        },
    )
    def get(self, request):
        days = _days(request.query_params.get("days"))
        profile = request.profile
        stats = lead_outcomes(
            profile.org, _people(profile), days, totals=is_org_admin(profile)
        )
        return Response(
            {
                "since": stats["since"].isoformat(),
                "measured_since": timezone.localdate(
                    profile.org.lead_outcomes_since
                ).isoformat(),
                "rows": stats["rows"],
                "totals": stats["totals"],
            }
        )
