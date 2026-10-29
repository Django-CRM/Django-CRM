"""Watch / unwatch / list-watchers REST endpoints.

URL surface (mounted under /api/cases/):
    POST   /<id>/watch/: subscribe the requesting profile (idempotent)
    DELETE /<id>/watch/: unsubscribe
    GET    /<id>/watchers/: list everyone watching the case, plus
                          `is_current_user_watching` for the caller
    GET    /watching/: cases the current profile watches

Authorisation: a user who can see the case can also watch it, using the one
rule in `cases.access.visible_cases_qs`, admins see everything; non-admins
see cases they created OR are assigned to OR already watch (so a watcher who
was un-assigned can still unsubscribe). This module used to keep its own copy
of that query, which is how the case detail view came to disagree with it.

Watchers are people. An app member (`common.app_members`) is refused a watch
and never listed: these rows are read from the through table, which the
people-only `Profile.objects` does not filter.
"""

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import visible_cases_qs as _visible_cases_qs
from cases.models import Case, CaseWatcher
from cases.serializer import CaseSerializer
from common.permissions import HasOrgContext

_WATCH_RESPONSE = inline_serializer(
    name="CaseWatchResponse",
    fields={
        "watching": serializers.BooleanField(),
        "subscribed_via": serializers.CharField(),
    },
)


class WatchView(APIView):
    """POST/DELETE /api/cases/<pk>/watch/"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    # 201 when this call subscribed the caller, 200 when they already were.
    @extend_schema(
        tags=["Cases"],
        request=None,
        responses={200: _WATCH_RESPONSE, 201: _WATCH_RESPONSE},
    )
    def post(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
        if request.profile.is_app:
            return Response(
                {"error": True, "errors": "An app member cannot watch a ticket."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            obj, created = CaseWatcher.objects.get_or_create(
                case=case,
                profile=request.profile,
                defaults={
                    "org": case.org,
                    "subscribed_via": "manual",
                },
            )
        except IntegrityError:
            obj = CaseWatcher.objects.get(case=case, profile=request.profile)
            created = False
        return Response(
            {"watching": True, "subscribed_via": obj.subscribed_via},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(tags=["Cases"], responses={204: None})
    def delete(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
        CaseWatcher.objects.filter(case=case, profile=request.profile).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchersListView(APIView):
    """GET /api/cases/<pk>/watchers/"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases"],
        responses={
            200: inline_serializer(
                name="CaseWatchersResponse",
                fields={
                    "watchers": inline_serializer(
                        name="CaseWatcherRow",
                        fields={
                            "id": serializers.CharField(),
                            "profile_id": serializers.CharField(),
                            "user_id": serializers.CharField(allow_null=True),
                            "name": serializers.CharField(),
                            "email": serializers.CharField(),
                            "subscribed_via": serializers.CharField(),
                            "created_at": serializers.DateTimeField(),
                        },
                        many=True,
                    ),
                    "count": serializers.IntegerField(),
                    "is_current_user_watching": serializers.BooleanField(),
                },
            )
        },
    )
    def get(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
        rows = (
            CaseWatcher.objects.filter(case=case, profile__is_app=False)
            .select_related("profile__user")
            .order_by("created_at")
        )
        watchers = [
            {
                "id": str(row.id),
                "profile_id": str(row.profile_id),
                "user_id": str(row.profile.user_id) if row.profile.user_id else None,
                "name": (row.profile.user.email if row.profile.user else "") or "",
                "email": row.profile.user.email if row.profile.user else "",
                "subscribed_via": row.subscribed_via,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        # The caller's own watch state, server-derived: both clients show
        # Watch or Unwatch from this rather than matching ids themselves.
        is_watching = any(row.profile_id == request.profile.id for row in rows)
        return Response(
            {
                "watchers": watchers,
                "count": len(watchers),
                "is_current_user_watching": is_watching,
            }
        )


class WatchingListView(APIView):
    """GET /api/cases/watching/

    Returns the list of cases the current profile watches. Accepts the same
    query-param filters as `CaseListView` (status, priority, account,
    case_type, assigned_to[], tags[], search, created_at__gte/lte,
    sla_breached, ordering) so the mobile filter chips work identically in
    watching mode.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Cases"],
        responses={
            200: inline_serializer(
                name="CaseWatchingResponse",
                fields={
                    "cases": CaseSerializer(many=True),
                    "count": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        from cases.serializer import parent_access_context
        from cases.views import apply_case_list_filters

        # The same related rows `/cases/` loads up front, so a row's account,
        # people, teams and tags are not each a query of their own.
        cases = (
            Case.objects.filter(org=request.profile.org, watchers=request.profile)
            .select_related("account", "org", "created_by", "parent")
            .prefetch_related("assigned_to__user", "contacts", "teams", "tags")
            .order_by("-created_at")
            .distinct()
        )
        cases = apply_case_list_filters(
            cases, request.query_params, request.profile.org
        )
        return Response(
            {
                "cases": CaseSerializer(
                    cases,
                    many=True,
                    context=parent_access_context(request.profile, cases),
                ).data,
                "count": cases.count(),
            }
        )
