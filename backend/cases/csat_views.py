"""CSAT endpoints, public (token-scoped) + internal (aggregate).

The public GET/POST pair is hit anonymously from a customer's email link.
There is no agent JWT, no portal session, and we deliberately do not
trust client-side state. Every request re-verifies the signed token,
re-loads the survey row by token_hash, and re-sets the RLS context to
the survey's org before any ORM write.

The internal aggregate endpoint feeds the analytics dashboard.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.signing import BadSignature, SignatureExpired
from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework import status as drf_status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import CsatSurvey
from cases.tasks import (
    CSAT_RATING_MAX,
    CSAT_RATING_MIN,
    CSAT_TOKEN_TTL_DAYS,
    csat_signer,
    hash_csat_token,
)
from common.permissions import HasOrgContext
from common.tasks import set_rls_context

# How long after the first response a customer can edit their rating.
EDIT_WINDOW_HOURS = 24


def _load_survey(token: str) -> tuple[CsatSurvey | None, int | None, str | None]:
    """Verify the token and load the survey row.

    Returns `(survey, http_status, error)`. Exactly one of `survey` or
    `(http_status, error)` is populated. The status code follows the spec:
      - 410 Gone for expired / unknown tokens (so search engines drop the
        link cleanly if they ever get one)
      - 400 for malformed tokens.
    """
    try:
        csat_signer().unsign(token, max_age=CSAT_TOKEN_TTL_DAYS * 24 * 3600)
    except SignatureExpired:
        return None, 410, "Survey link has expired."
    except BadSignature:
        return None, 400, "Invalid survey link."

    survey = CsatSurvey.objects.filter(token_hash=hash_csat_token(token)).first()
    if survey is None:
        return None, 410, "Survey link is no longer valid."
    if timezone.now() >= survey.expires_at:
        return None, 410, "Survey link has expired."
    # Lock submission after the edit window closes.
    return survey, None, None


class PublicCsatView(APIView):
    """Anonymous endpoint reached from the customer's email link."""

    permission_classes = (AllowAny,)
    authentication_classes: list = []  # No JWT/session/csrf needed.

    def get(self, request, token: str):
        survey, status, err = _load_survey(token)
        if survey is None:
            return Response({"error": err}, status=status)
        # Set RLS so a Postgres deployment can read the case via FK below.
        set_rls_context(survey.org_id)
        case = survey.case
        agent_email = ""
        first_assignee = case.assigned_to.first()
        if first_assignee is not None and first_assignee.user_id is not None:
            agent_email = first_assignee.user.email
        return Response(
            {
                "case_subject": case.name,
                "case_closed_on": (
                    case.closed_on.isoformat() if case.closed_on else None
                ),
                "org_name": case.org.name,
                "agent_name": agent_email or "your support team",
                "rating": survey.rating,
                "comment": survey.comment,
                "responded_at": (
                    survey.responded_at.isoformat() if survey.responded_at else None
                ),
                "edit_window_closes_at": (
                    (
                        survey.responded_at + timedelta(hours=EDIT_WINDOW_HOURS)
                    ).isoformat()
                    if survey.responded_at
                    else None
                ),
            }
        )

    def post(self, request, token: str):
        survey, status, err = _load_survey(token)
        if survey is None:
            return Response({"error": err}, status=status)

        # Edit window enforcement: first submit always allowed; subsequent
        # submits allowed only within 24h of the first response.
        if survey.responded_at is not None:
            window_close = survey.responded_at + timedelta(hours=EDIT_WINDOW_HOURS)
            if timezone.now() >= window_close:
                return Response(
                    {"error": "Survey is locked. Edit window has closed."},
                    status=drf_status.HTTP_409_CONFLICT,
                )

        rating = request.data.get("rating")
        comment = (request.data.get("comment") or "").strip()
        try:
            rating_int = int(rating)
        except (TypeError, ValueError):
            return Response(
                {
                    "error": f"rating must be an integer "
                    f"{CSAT_RATING_MIN}-{CSAT_RATING_MAX}"
                },
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        if rating_int < CSAT_RATING_MIN or rating_int > CSAT_RATING_MAX:
            return Response(
                {"error": f"rating must be {CSAT_RATING_MIN}..{CSAT_RATING_MAX}"},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        # Set RLS context so the UPDATE clears the policy on Postgres.
        set_rls_context(survey.org_id)
        survey.rating = rating_int
        survey.comment = comment
        if survey.responded_at is None:
            survey.responded_at = timezone.now()
        survey.save(update_fields=["rating", "comment", "responded_at", "updated_at"])

        return Response(
            {
                "rating": survey.rating,
                "comment": survey.comment,
                "responded_at": survey.responded_at.isoformat(),
            }
        )


class CsatAggregateView(APIView):
    """Org-scoped aggregate for the dashboard tile."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        org = request.profile.org
        responded = CsatSurvey.objects.filter(org=org, rating__isnull=False)
        total = responded.count()
        if total == 0:
            return Response(
                {
                    "average": None,
                    "count": 0,
                    "distribution": {str(i): 0 for i in range(1, 6)},
                }
            )
        avg = responded.aggregate(avg=Avg("rating"))["avg"]
        dist_rows = (
            responded.values("rating").annotate(n=Count("id")).order_by("rating")
        )
        distribution = {str(i): 0 for i in range(1, 6)}
        for row in dist_rows:
            distribution[str(row["rating"])] = row["n"]
        return Response(
            {
                "average": float(avg) if avg is not None else None,
                "count": total,
                "distribution": distribution,
            }
        )
