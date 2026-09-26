"""Watch / unwatch / list-watchers REST endpoints.

URL surface (mounted under /api/cases/):
    POST   /<id>/watch/: subscribe the requesting profile (idempotent)
    DELETE /<id>/watch/: unsubscribe
    GET    /<id>/watchers/: list everyone watching the case
    GET    /watching/: cases the current profile watches

Authorisation: a user who can see the case can also watch it, using the one
rule in `cases.access.visible_cases_qs`, admins see everything; non-admins
see cases they created OR are assigned to OR already watch (so a watcher who
was un-assigned can still unsubscribe). This module used to keep its own copy
of that query, which is how the case detail view came to disagree with it.
"""

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import visible_cases_qs as _visible_cases_qs
from cases.models import Case, CaseWatcher
from common.permissions import HasOrgContext


class WatchView(APIView):
    """POST/DELETE /api/cases/<pk>/watch/"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
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

    def delete(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
        CaseWatcher.objects.filter(case=case, profile=request.profile).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchersListView(APIView):
    """GET /api/cases/<pk>/watchers/"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, pk, *args, **kwargs):
        case = get_object_or_404(_visible_cases_qs(request.profile), pk=pk)
        rows = (
            CaseWatcher.objects.filter(case=case)
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
        return Response({"watchers": watchers, "count": len(watchers)})


class WatchingListView(APIView):
    """GET /api/cases/watching/

    Returns the list of cases the current profile watches. Accepts the same
    query-param filters as `CaseListView` (status, priority, account,
    case_type, assigned_to[], tags[], search, created_at__gte/lte,
    sla_breached, ordering) so the mobile filter chips work identically in
    watching mode.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, *args, **kwargs):
        from cases.serializer import CaseSerializer, parent_access_context
        from cases.views import apply_case_list_filters

        cases = (
            Case.objects.filter(org=request.profile.org, watchers=request.profile)
            .order_by("-created_at")
            .distinct()
        )
        cases = apply_case_list_filters(cases, request.query_params)
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
