"""
Solution (Knowledge Base) Views
"""

import json

from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.kb_access import (
    assert_solution_delete_access,
    assert_solution_release_access,
    assert_solution_write_access,
    get_solution_or_404,
)
from cases.models import Solution
from cases.solution_serializers import (
    SolutionCreateUpdateSerializer,
    SolutionDetailSerializer,
    SolutionSerializer,
)
from common.models import Tags
from common.permissions import HasOrgContext
from common.validators import uuid_list_param


def _wants_release(data, instance=None):
    """True when this payload would approve or publish the article.

    Both are admin-only acts (see `cases.kb_access`), and both can arrive as
    ordinary fields on an ordinary create or update, which is exactly how
    they were being performed. Read as a transition, not as a value: a PATCH
    that repeats the `approved` an article already has is not an approval, so
    an author fixing a typo on their own approved draft is not stopped by a
    gate meant for the person who approves it.
    """
    moving_to_approved = (
        "status" in data
        and data.get("status") == "approved"
        and (instance is None or instance.status != "approved")
    )
    publishing = "is_published" in data and _as_bool(data.get("is_published")) != bool(
        getattr(instance, "is_published", False)
    )
    return moving_to_approved or publishing


def _as_bool(value):
    """Query params and form bodies arrive as strings; JSON bodies do not."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _apply_tags(solution, data, org):
    """Set an article's tags from a request body, org-safely.

    Set here rather than on `SolutionCreateUpdateSerializer` so the org filter
    is unavoidable: the ids arrive from the client, and resolving them against
    `org` means a tag belonging to another org is simply not found, rather than
    being attached and creating a cross-tenant link.

    Absent means unchanged, which is the difference between this and the case
    endpoints. `cases` clears and re-adds whenever it runs, and
    `AccountDetailView.put` clears tags outright; both mean a PATCH that never
    mentions tags silently drops them. An explicit `"tags": []` still clears.
    """
    if "tags" not in data:
        return

    ids = data.get("tags") or []
    if isinstance(ids, str):
        ids = json.loads(ids)
    # The web form posts ids; some clients post whole tag objects.
    ids = [item.get("id") if isinstance(item, dict) else item for item in ids]

    solution.tags.set(Tags.objects.filter(id__in=ids, org=org, is_active=True))


class SolutionListView(APIView, LimitOffsetPagination):
    """
    List and create solutions (Knowledge Base)
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="List all solutions with filters",
        parameters=[
            OpenApiParameter(
                "status", str, description="Filter by status (draft/reviewed/approved)"
            ),
            OpenApiParameter(
                "is_published", bool, description="Filter by published status"
            ),
            OpenApiParameter(
                "search", str, description="Search in title and description"
            ),
            OpenApiParameter(
                "tags", str, description="Comma-separated tag ids; matches any of them"
            ),
        ],
        responses={
            200: inline_serializer(
                name="SolutionListResponse",
                fields={
                    "count": serializers.IntegerField(),
                    "results": SolutionSerializer(many=True),
                    "totals": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request):
        """List solutions with filters"""
        org = request.profile.org
        base = Solution.objects.filter(org=org)

        # The article list rendered `case_count` per row and a nested `org`
        # per row, neither of which the queryset fetched: 13 articles cost 27
        # queries. Both are one join now, and the count is the same number the
        # serializer used to compute a row at a time.
        queryset = (
            base.select_related("org", "created_by")
            .prefetch_related("tags")
            .annotate(linked_case_count=Count("cases", distinct=True))
            .order_by("-updated_at", "-id")
        )

        # Filters
        status_filter = request.query_params.get("status")
        is_published = request.query_params.get("is_published")
        search = request.query_params.get("search")

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if is_published is not None:
            # `is_published.lower() == "true"` meant every other spelling:
            # `1`, `yes`, `on`: silently filtered to *unpublished*, which is
            # the opposite of what the caller asked for.
            queryset = queryset.filter(is_published=_as_bool(is_published))

        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        # `distinct()` because an article matching two of the requested tags
        # would otherwise be returned twice, which also breaks the page count.
        tag_ids = uuid_list_param(request.query_params, "tags")
        if tag_ids:
            queryset = queryset.filter(tags__id__in=tag_ids).distinct()

        # Counted over the whole knowledge base, not over the filtered page.
        # These four are a partition of the KB, "12 total, 3 of them drafts"
        # , so recomputing them inside a `?status=draft` filter would leave
        # the other three cards reading zero and say nothing.
        counts = base.aggregate(
            count=Count("id"),
            published=Count("id", filter=Q(is_published=True)),
            draft=Count("id", filter=Q(status="draft")),
            reviewed=Count("id", filter=Q(status="reviewed")),
            approved_unpublished=Count(
                "id", filter=Q(status="approved", is_published=False)
            ),
        )

        # Paginate
        results = self.paginate_queryset(queryset, request, view=self)
        serializer = SolutionSerializer(results, many=True)

        response = self.get_paginated_response(serializer.data)
        response.data["totals"] = counts
        return response

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Create a new solution",
        request=SolutionCreateUpdateSerializer,
        responses={201: SolutionSerializer},
    )
    def post(self, request):
        """Create a new solution"""
        # Approving or publishing on the way in is still approving and
        # publishing. Without this an author could write an article and hand
        # it to customers in a single request, never touching the endpoints
        # that exist to gate exactly that.
        if _wants_release(request.data):
            assert_solution_release_access(request.profile)

        serializer = SolutionCreateUpdateSerializer(data=request.data)

        if serializer.is_valid():
            solution = serializer.save(org=request.profile.org, created_by=request.user)
            _apply_tags(solution, request.data, request.profile.org)

            response_serializer = SolutionSerializer(solution)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SolutionDetailView(APIView):
    """
    Get, update, or delete a solution
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Get solution details",
        responses={200: SolutionDetailSerializer},
    )
    def get(self, request, pk):
        """Get solution details"""
        solution = get_solution_or_404(request.profile, pk)
        # The requester's profile decides which linked cases come back. See
        # `SolutionDetailSerializer.get_linked_cases`.
        serializer = SolutionDetailSerializer(
            solution, context={"profile": request.profile}
        )
        return Response(serializer.data)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Update solution",
        request=SolutionCreateUpdateSerializer,
        responses={200: SolutionSerializer},
    )
    def put(self, request, pk):
        """Update solution"""
        return self._update(request, pk, partial=False)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Partially update solution",
        request=SolutionCreateUpdateSerializer,
        responses={200: SolutionSerializer},
    )
    def patch(self, request, pk):
        """Partially update solution"""
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        solution = get_solution_or_404(request.profile, pk)
        assert_solution_write_access(request.profile, solution)
        if _wants_release(request.data, solution):
            assert_solution_release_access(request.profile)

        serializer = SolutionCreateUpdateSerializer(
            solution, data=request.data, partial=partial
        )
        if serializer.is_valid():
            solution = serializer.save(updated_by=request.user)
            _apply_tags(solution, request.data, request.profile.org)
            return Response(SolutionSerializer(solution).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Delete solution",
        responses={204: None},
    )
    def delete(self, request, pk):
        """Delete solution"""
        solution = get_solution_or_404(request.profile, pk)
        assert_solution_delete_access(request.profile, solution)
        solution.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SolutionPublishView(APIView):
    """
    Publish or unpublish a solution
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Publish an approved solution",
        request=None,
        responses={200: SolutionSerializer},
    )
    def post(self, request, pk):
        """Publish solution"""
        solution = get_solution_or_404(request.profile, pk)
        assert_solution_release_access(request.profile)

        if solution.status != "approved":
            return Response(
                {
                    "error": "Only approved solutions can be published. Please approve it first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        solution.publish()
        serializer = SolutionSerializer(solution)
        return Response(serializer.data)


class SolutionUnpublishView(APIView):
    """
    Unpublish a solution
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Solutions (Knowledge Base)"],
        description="Unpublish a solution",
        request=None,
        responses={200: SolutionSerializer},
    )
    def post(self, request, pk):
        """Unpublish solution"""
        solution = get_solution_or_404(request.profile, pk)
        # Taking an article away from customers is the same switch as giving
        # it to them, so it is the same rule. Pulling a bad answer down is
        # urgent, but "urgent" is an argument for having enough admins, not
        # for letting anyone in the org silently un-answer a question.
        assert_solution_release_access(request.profile)

        solution.unpublish()
        serializer = SolutionSerializer(solution)
        return Response(serializer.data)
