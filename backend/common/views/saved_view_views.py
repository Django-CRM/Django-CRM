"""Saved list views (G29): a profile's named filters for one list.

Routes (under /api/saved-views/):
    GET    /?module=<list>   the caller's own views, for one list or for all six
    POST   /                 save one
    GET    /<id>/            one of the caller's views
    PATCH  /<id>/            rename it, or replace its filters
    DELETE /<id>/            delete it

A view is private to the profile that saved it, admins included: every lookup
filters on the org and the profile, so another person's id answers 404 on every
verb, the same body as an id that does not exist. What a view may hold is
checked in ``common/saved_views.py``.
"""

from django.db.models.functions import Lower
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_scoped_or_404
from common.models import SavedView
from common.permissions import HasOrgContext
from common.saved_views import LISTS, MAX_VIEWS_PER_LIST, SavedViewSerializer


class SavedViewListView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Saved views"],
        operation_id="saved_views_list",
        parameters=[
            OpenApiParameter(
                "module", str, enum=sorted(LISTS), description="Only this list's views."
            )
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        profile = request.profile
        views = SavedView.objects.filter(org=profile.org, profile=profile)
        module = request.query_params.get("module")
        if module is not None:
            if module not in LISTS:
                return Response(
                    {"module": [f"Choose one of: {', '.join(sorted(LISTS))}."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            views = views.filter(module=module)
        views = views.order_by("module", Lower("name"))
        return Response(
            {
                "saved_views": SavedViewSerializer(views, many=True).data,
                "limit": MAX_VIEWS_PER_LIST,
            }
        )

    @extend_schema(
        tags=["Saved views"],
        request=SavedViewSerializer,
        responses={201: SavedViewSerializer},
    )
    def post(self, request):
        serializer = SavedViewSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        view = serializer.save(org=request.profile.org, profile=request.profile)
        return Response(SavedViewSerializer(view).data, status=status.HTTP_201_CREATED)


class SavedViewDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_view(self, request, pk):
        return get_scoped_or_404(
            SavedView, pk, request.profile.org, profile=request.profile
        )

    @extend_schema(tags=["Saved views"], responses=SavedViewSerializer)
    def get(self, request, pk):
        return Response(SavedViewSerializer(self.get_view(request, pk)).data)

    @extend_schema(
        tags=["Saved views"],
        request=SavedViewSerializer,
        responses=SavedViewSerializer,
    )
    def patch(self, request, pk):
        serializer = SavedViewSerializer(
            self.get_view(request, pk),
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(tags=["Saved views"], responses={204: None})
    def delete(self, request, pk):
        self.get_view(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
