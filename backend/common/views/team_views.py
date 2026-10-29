import uuid

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common import swagger_params
from common.lookups import get_scoped_or_404
from common.models import Profile, Teams
from common.pagination import CappedLimitOffsetPagination
from common.permissions import HasOrgContext, is_org_admin, may_configure
from common.serializer import (
    TeamCreateSerializer,
    TeamsSerializer,
    TeamswaggerCreateSerializer,
)
from common.tasks import remove_users, update_team_users
from common.validators import payload_id_list, uuid_list_param, uuid_param


def _member_profiles(data, org):
    """The members a team write names, or None when it names none.

    ``users`` is the documented key; ``assign_users`` is the one the PUT used
    to read. Null or blank counts as not sent, and only an explicit empty list
    (``[]``, or that text in a form) empties a team: entries that hold no id,
    such as ``[null]``, are refused. Every id has to be a profile of ``org``.
    """
    for key in ("users", "assign_users"):
        if hasattr(data, "getlist"):
            values = data.getlist(key)
            value = values[0] if len(values) == 1 else values or None
        else:
            value = data.get(key)
        if value is None or value == "":
            continue
        ids = payload_id_list(value, key)
        emptied = value == [] or (
            isinstance(value, str) and "".join(value.split()) == "[]"
        )
        if not ids and not emptied:
            raise ValidationError(
                {key: ["Send member ids, or an empty list to remove every member."]}
            )
        profiles = list(Profile.objects.filter(id__in=ids, org=org))
        found = {profile.id for profile in profiles}
        unknown = [i for i in dict.fromkeys(ids) if uuid.UUID(str(i)) not in found]
        if unknown:
            raise ValidationError(
                {key: [f"'{i}' is not a member of this organization." for i in unknown]}
            )
        return profiles
    return None


def _set_members(team, profiles, org):
    """Replace the members, then queue putting them on the team's records and
    taking whoever left off them."""
    before = set(team.users.values_list("id", flat=True))
    team.users.set(profiles)
    team_id, org_id = str(team.id), str(org.id)
    update_team_users.delay(team_id, org_id)
    left = before - set(team.users.values_list("id", flat=True))
    if left:
        remove_users.delay(sorted(str(i) for i in left), team_id, org_id)


class TeamsListView(APIView, CappedLimitOffsetPagination):
    model = Teams
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_context_data(self, **kwargs):
        params = self.request.query_params
        queryset = self.model.objects.filter(org=self.request.profile.org).order_by(
            "-id"
        )
        if params:
            if params.get("team_name"):
                queryset = queryset.filter(name__icontains=params.get("team_name"))
            created_by = uuid_param(params, "created_by")
            if created_by:
                queryset = queryset.filter(created_by=created_by)
            # `get` rather than `getlist` handed `__in` a single string, which
            # Django iterates character by character, so this filter matched
            # nothing and said nothing about why.
            assigned_users = uuid_list_param(params, "assigned_users")
            if assigned_users:
                queryset = queryset.filter(users__id__in=assigned_users)

        context = {}
        results_teams = self.paginate_queryset(
            queryset.distinct(), self.request, view=self
        )
        teams = TeamsSerializer(results_teams, many=True).data
        if results_teams:
            offset = queryset.filter(id__gte=results_teams[-1].id).count()
            if offset == queryset.count():
                offset = None
        else:
            offset = 0
        context["per_page"] = 10
        page_number = int(self.offset / 10) + 1
        context["page_number"] = page_number
        context.update({"teams_count": self.count, "offset": offset})
        context["teams"] = teams
        return context

    @extend_schema(
        tags=["Teams"],
        operation_id="teams_list",
        parameters=swagger_params.teams_list_get_params,
        responses={
            200: inline_serializer(
                name="TeamsListResponse",
                fields={
                    "per_page": serializers.IntegerField(),
                    # Was ListField, which described the bug rather than the
                    # contract: a stray trailing comma made `page_number` a
                    # one-element tuple, so five list endpoints answered
                    # `"page_number": [1]`. The other four declared IntegerField
                    # and disagreed with what they actually sent.
                    "page_number": serializers.IntegerField(),
                    "teams_count": serializers.IntegerField(),
                    "offset": serializers.IntegerField(allow_null=True),
                    "teams": TeamsSerializer(many=True),
                },
            )
        },
    )
    def get(self, *args, **kwargs):
        if not is_org_admin(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        context = self.get_context_data(**kwargs)
        return Response(context)

    @extend_schema(
        tags=["Teams"],
        operation_id="teams_create",
        request=TeamswaggerCreateSerializer,
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TeamCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        if not may_configure(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        params = self.request.data
        members = _member_profiles(params, request.profile.org)
        serializer = TeamCreateSerializer(data=params, request_obj=request)
        if serializer.is_valid():
            team_obj = serializer.save(org=request.profile.org)
            if members:
                team_obj.users.add(*members)
            return Response(
                {"error": False, "message": "Team Created Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class TeamsDetailView(APIView):
    model = Teams
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return get_scoped_or_404(self.model, pk, self.request.profile.org)

    @extend_schema(
        tags=["Teams"],
        operation_id="teams_retrieve",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TeamDetailResponse", fields={"team": TeamsSerializer()}
            )
        },
    )
    def get(self, request, pk, **kwargs):
        if not is_org_admin(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        self.team_obj = self.get_object(pk)
        context = {}
        context["team"] = TeamsSerializer(self.team_obj).data
        return Response(context)

    @extend_schema(
        tags=["Teams"],
        operation_id="teams_update",
        request=TeamswaggerCreateSerializer,
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TeamUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, *args, **kwargs):
        if not may_configure(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        params = request.data
        self.team = self.get_object(pk)
        members = _member_profiles(params, request.profile.org)
        serializer = TeamCreateSerializer(
            data=params, instance=self.team, request_obj=request
        )
        if serializer.is_valid():
            team_obj = serializer.save()
            if members is not None:
                _set_members(team_obj, members, request.profile.org)
            return Response(
                {"error": False, "message": "Team Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Teams"],
        request=TeamswaggerCreateSerializer,
        parameters=swagger_params.organization_params,
        description="Partial Team Update",
        responses={
            200: inline_serializer(
                name="TeamPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, *args, **kwargs):
        """Handle partial updates to a team."""
        if not may_configure(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        params = request.data
        self.team = self.get_object(pk)
        members = _member_profiles(params, request.profile.org)
        serializer = TeamCreateSerializer(
            data=params, instance=self.team, request_obj=request, partial=True
        )
        if serializer.is_valid():
            team_obj = serializer.save()
            if members is not None:
                _set_members(team_obj, members, request.profile.org)
            return Response(
                {"error": False, "message": "Team Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["Teams"],
        operation_id="teams_destroy",
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TeamDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, **kwargs):
        if not may_configure(self.request.profile):
            return Response(
                {
                    "error": True,
                    "errors": "You don't have permission to perform this action.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        self.team_obj = self.get_object(pk)
        self.team_obj.delete()
        return Response(
            {"error": False, "message": "Team Deleted Successfully"},
            status=status.HTTP_200_OK,
        )
