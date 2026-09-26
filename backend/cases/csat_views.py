"""CSAT endpoints, public (token-scoped) + internal (aggregate).

The public GET/POST pair is hit anonymously from a customer's email link.
There is no agent JWT, no portal session, and we deliberately do not
trust client-side state. Every request re-verifies the signed token,
resolves the org from the unscoped token lookup, sets the RLS context to
it, and only then loads the survey row by token_hash.

The internal aggregate endpoint feeds the ticket analytics pages, under the
same visibility rule and date window as the other analytics endpoints.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.signing import BadSignature, SignatureExpired
from django.db.models import Avg, Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status as drf_status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.analytics_views import _FILTER_PARAMS, _filtered_qs
from cases.models import CsatSurvey
from cases.tasks import (
    CSAT_RATING_MAX,
    CSAT_RATING_MIN,
    CSAT_TOKEN_TTL_DAYS,
    csat_signer,
    hash_csat_token,
)
from common.permissions import HasOrgContext
from common.portal_tokens import resolve_portal_org_by_hash
from common.tasks import set_rls_context

# How long after the first response a customer can edit their rating.
EDIT_WINDOW_HOURS = 24


def _load_survey(token: str) -> tuple[CsatSurvey | None, int | None, str | None]:
    """Verify the token, set the survey's org as the RLS context, load the row.

    Returns `(survey, http_status, error)`. Exactly one of `survey` or
    `(http_status, error)` is populated. The status code follows the spec:
      - 410 Gone for expired / unknown tokens (so search engines drop the
        link cleanly if they ever get one)
      - 400 for malformed tokens.

    The request arrives anonymously, so `app.current_org` is empty, and under
    the non-superuser role production runs as, RLS hides every `csat_survey`
    row from an empty context. Querying the survey first therefore found
    nothing and answered 410 to every customer. The org comes from the
    unscoped `PortalAccessToken` lookup that `send_csat_survey` registers when
    it mints the token; the survey is read only after the context is set, and
    only within that org. An unregistered token is a 410, like a missing row.
    """
    try:
        csat_signer().unsign(token, max_age=CSAT_TOKEN_TTL_DAYS * 24 * 3600)
    except SignatureExpired:
        return None, 410, "Survey link has expired."
    except BadSignature:
        return None, 400, "Invalid survey link."

    token_hash = hash_csat_token(token)
    org_id = resolve_portal_org_by_hash(token_hash, "csat")
    if org_id is None:
        return None, 410, "Survey link is no longer valid."
    set_rls_context(org_id)
    survey = CsatSurvey.objects.filter(token_hash=token_hash, org_id=org_id).first()
    if survey is None:
        return None, 410, "Survey link is no longer valid."
    if timezone.now() >= survey.expires_at:
        return None, 410, "Survey link has expired."
    # Lock submission after the edit window closes.
    return survey, None, None


def _agent_display_name(case) -> str:
    """Who handled `case`, as a stranger holding the survey link may see it.

    The first assignee's display name, never an address: this answer goes to
    anyone with the link, and an agent's email is a login identifier. A name
    that is blank or itself looks like an address falls back to the org's
    support label, the one reply emails already sign with.
    """
    assignee = case.assigned_to.select_related("user").first()
    name = (assignee.user.name or "").strip() if assignee and assignee.user else ""
    if name and "@" not in name:
        return name
    org_name = (case.org.name or "").strip()
    return f"{org_name} support" if org_name else "your support team"


class PublicCsatView(APIView):
    """Anonymous endpoint reached from the customer's email link."""

    permission_classes = (AllowAny,)
    authentication_classes: list = []  # No JWT/session/csrf needed.

    def get(self, request, token: str):
        survey, status, err = _load_survey(token)
        if survey is None:
            return Response({"error": err}, status=status)
        case = survey.case
        return Response(
            {
                "case_subject": case.name,
                "case_closed_on": (
                    case.closed_on.isoformat() if case.closed_on else None
                ),
                "org_name": case.org.name,
                "agent_name": _agent_display_name(case),
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
    """Average, count and 1-5 distribution of the ratings customers gave.

    Scoped like the other analytics endpoints, through the same
    `_filtered_qs`: an admin sees the org, anybody else only the tickets they
    raised, are assigned to or watch, and `team` / `agent` / `priority` narrow
    it further. The window (`from` / `to`, default the last 30 days) is when
    the customer answered. This used to be every rating in the org, all-time,
    for any member.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["cases-analytics"], parameters=_FILTER_PARAMS)
    def get(self, request):
        qs, from_dt, to_dt = _filtered_qs(request)
        responded = CsatSurvey.objects.filter(
            org=request.profile.org,
            case_id__in=qs.values("id"),
            rating__isnull=False,
            responded_at__gte=from_dt,
            responded_at__lt=to_dt,
        )
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
