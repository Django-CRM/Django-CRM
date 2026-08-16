"""Agent-facing knowledge-base helpers.

This is the suggester behind the comment composer's typeahead. Its
customer-facing counterpart lives in `portal_views` and answers to a stricter
rule: this endpoint shows an agent anything published, while the portal also
requires `approved`. Text matching and truncation are shared, in `kb_text`.

Remaining pieces of the module are tracked on issue #589: categories,
versioning, view counts, helpfulness feedback, slugs.
"""

from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.kb_text import snippet, text_match
from cases.models import Case, Solution
from common.permissions import HasOrgContext

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20


def _seed_terms(case: Case) -> list[str]:
    """Seed terms when q is empty: pull a handful of meaningful words from
    the case name + description so the agent gets useful suggestions on
    first focus instead of an empty list.
    """
    pieces: list[str] = []
    for raw in (case.name or "", case.description or ""):
        for word in raw.split():
            cleaned = "".join(ch for ch in word if ch.isalnum() or ch in "-_").lower()
            if len(cleaned) >= 4:
                pieces.append(cleaned)
            if len(pieces) >= 6:
                break
        if len(pieces) >= 6:
            break
    return pieces


class SolutionSuggestionsView(APIView):
    """`GET /api/cases/<pk>/solution-suggestions/?q=&limit=`.

    Returns the top N published solutions in the same org whose title or
    description matches the search term. `?q=` is optional, when blank,
    the case's own name + description seed the search so the panel is
    useful on first focus.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases"],
        parameters=[
            OpenApiParameter("q", str, description="Search query"),
            OpenApiParameter(
                "limit", int, description="Max results, default 5, capped at 20"
            ),
        ],
    )
    def get(self, request, pk):
        org = request.profile.org
        case = Case.objects.filter(pk=pk, org=org).first()
        if case is None:
            return Response({"error": "Case not found"}, status=404)

        try:
            limit = int(request.query_params.get("limit", _DEFAULT_LIMIT))
        except ValueError:
            limit = _DEFAULT_LIMIT
        limit = max(1, min(_MAX_LIMIT, limit))

        q = (request.query_params.get("q") or "").strip()
        published = Solution.objects.filter(org=org, is_published=True)

        if q:
            results = published.filter(text_match(q)).order_by("-updated_at")[:limit]
        else:
            # Seed from the case so the panel is useful on first focus. If
            # there are no seed terms, or the seed-term filter produces zero
            # matches, fall back to the most-recent published solutions.
            # The agent should always see *something* on first focus.
            terms = _seed_terms(case)
            results = []
            if terms:
                seed_filter = Q()
                for term in terms:
                    seed_filter |= text_match(term)
                results = list(
                    published.filter(seed_filter).order_by("-updated_at")[:limit]
                )
            if not results:
                results = list(published.order_by("-updated_at")[:limit])

        data = [
            {
                "id": str(s.id),
                "title": s.title,
                "snippet": snippet(s.description),
                # Full body included so the picker can paste without a
                # second roundtrip. Keeps the widget snappy on a slow link.
                "body": s.description or "",
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in results
        ]
        return Response({"results": data, "count": len(data), "q": q})
