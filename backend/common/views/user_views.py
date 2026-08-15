from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import Case
from cases.serializer import CaseSerializer
from common import swagger_params
from common.models import Comment, PersonalAccessToken, Profile, Teams, User
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    BillingAddressSerializer,
    CommentSerializer,
    CreateProfileSerializer,
    CreateUserSerializer,
    ProfileSerializer,
    TeamsSerializer,
    UserCreateSwaggerSerializer,
    UserUpdateStatusSwaggerSerializer,
)
from common.tasks import send_email_user_delete
from common.utils import COUNTRIES, ROLES
from contacts.models import Contact
from contacts.serializer import ContactSerializer
from opportunity.models import Opportunity
from opportunity.serializer import OpportunitySerializer


def _valid_token_counts_by_profile(org):
    """{profile_id (str): count of non-revoked, non-expired PATs} for one org.

    One query for the whole roster. A token is "valid" if it has not been
    revoked and has not expired. The same test PersonalAccessToken.is_valid()
    applies per row, expressed as a filter so the count is a single round trip.
    """
    now = timezone.now()
    rows = (
        PersonalAccessToken.objects.filter(org=org, revoked_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values("profile_id")
        .annotate(n=Count("id"))
    )
    return {str(r["profile_id"]): r["n"] for r in rows}


class GetTeamsAndUsersView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="TeamsAndUsersResponse",
                fields={
                    "teams": TeamsSerializer(many=True),
                    "profiles": ProfileSerializer(many=True),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        data = {}
        teams = Teams.objects.filter(org=request.profile.org).order_by("-id")
        teams_data = TeamsSerializer(teams, many=True).data
        profiles = Profile.objects.filter(
            is_active=True, org=request.profile.org
        ).order_by("user__email")
        profiles_data = ProfileSerializer(profiles, many=True).data
        data["teams"] = teams_data
        data["profiles"] = profiles_data
        return Response(data)


class UsersListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        request=UserCreateSwaggerSerializer,
        responses={
            201: inline_serializer(
                name="UserCreateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, format=None):
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        params = request.data
        if params:
            user_serializer = CreateUserSerializer(data=params, org=request.profile.org)
            address_serializer = BillingAddressSerializer(data=params)
            # This POST is already gated to admins above, and inviting someone
            # inherently means choosing their role, so role is grantable here.
            profile_serializer = CreateProfileSerializer(
                data=params, can_grant_privileges=True
            )
            data = {}
            if not user_serializer.is_valid():
                data["user_errors"] = dict(user_serializer.errors)
            if not profile_serializer.is_valid():
                data["profile_errors"] = profile_serializer.errors
            if not address_serializer.is_valid():
                data["address_errors"] = (address_serializer.errors,)
            if data:
                return Response(
                    {"error": True, "errors": data},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # A concurrent invite for the same email can commit between the
            # checks above and the writes below. Keep the account, address and
            # membership in one transaction so a lost race rolls back cleanly
            # instead of stranding a half-built user.
            try:
                with transaction.atomic():
                    # Address is org-scoped and RLS-protected, so it must carry
                    # the org. Only create one when address fields were actually
                    # supplied -- invites from the web UI send just email + role.
                    address_obj = None
                    if any(address_serializer.validated_data.values()):
                        address_obj = address_serializer.save(org=request.profile.org)

                    email = user_serializer.validated_data["email"]
                    user = User.objects.filter(email__iexact=email).first()
                    if user is None:
                        user = user_serializer.save(is_active=True)
                    # An existing account is reused as-is: the inviting admin
                    # gets a profile in their own org and no say over that
                    # person's account.

                    Profile.objects.create(
                        user=user,
                        date_of_joining=timezone.now(),
                        role=profile_serializer.validated_data["role"],
                        address=address_obj,
                        org=request.profile.org,
                    )
            except IntegrityError:
                return Response(
                    {"error": True, "errors": "User already in organization"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"error": False, "message": "User Created Successfully"},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"error": True, "errors": "Invalid request"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.user_list_params,
        responses={
            200: inline_serializer(
                name="UsersListResponse",
                fields={
                    "active_users": serializers.DictField(),
                    "inactive_users": serializers.DictField(),
                    "admin_email": serializers.CharField(),
                    "roles": serializers.ListField(),
                    "status": serializers.ListField(),
                },
            )
        },
    )
    def get(self, request, format=None):
        # Check if profile exists and user has permission
        if not self.request.profile:
            return Response(
                {"error": True, "errors": "Organization context required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        queryset = Profile.objects.filter(org=request.profile.org).order_by("-id")
        params = request.query_params
        if params:
            if params.get("email"):
                queryset = queryset.filter(user__email__icontains=params.get("email"))
            if params.get("role"):
                queryset = queryset.filter(role=params.get("role"))
            if params.get("status"):
                queryset = queryset.filter(is_active=params.get("status"))

        # A not-yet-revoked, unexpired token on a deactivated account is a
        # dormant liability the team page surfaces: it is rejected at login
        # today (resolve_valid_pat checks profile.is_active: see
        # test_pat_auth.py::test_inactive_profile_raises), but it would
        # authenticate again the moment the account is reactivated, so it is
        # worth revoking as part of offboarding. Count such tokens per profile
        # once, here, rather than on ProfileSerializer (which many endpoints
        # share and would each pay an extra query for a field only this reads).
        token_counts = _valid_token_counts_by_profile(request.profile.org)

        def _with_token_counts(rows):
            for row in rows:
                row["active_token_count"] = token_counts.get(str(row["id"]), 0)
            return rows

        context = {}
        queryset_active_users = queryset.filter(is_active=True)
        results_active_users = self.paginate_queryset(
            queryset_active_users.distinct(), self.request, view=self
        )
        active_users = _with_token_counts(
            ProfileSerializer(results_active_users, many=True).data
        )
        if results_active_users:
            offset = queryset_active_users.filter(
                id__gte=results_active_users[-1].id
            ).count()
            if offset == queryset_active_users.count():
                offset = None
        else:
            offset = 0
        context["active_users"] = {
            "active_users_count": self.count,
            "active_users": active_users,
            "offset": offset,
        }

        queryset_inactive_users = queryset.filter(is_active=False)
        results_inactive_users = self.paginate_queryset(
            queryset_inactive_users.distinct(), self.request, view=self
        )
        inactive_users = _with_token_counts(
            ProfileSerializer(results_inactive_users, many=True).data
        )
        if results_inactive_users:
            offset = queryset_inactive_users.filter(
                id__gte=results_inactive_users[-1].id
            ).count()
            if offset == queryset_inactive_users.count():
                offset = None
        else:
            offset = 0
        context["inactive_users"] = {
            "inactive_users_count": self.count,
            "inactive_users": inactive_users,
            "offset": offset,
        }

        context["admin_email"] = settings.ADMIN_EMAIL
        context["roles"] = ROLES
        context["status"] = [("True", "Active"), ("False", "In Active")]
        return Response(context)


class UserDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        # Security fix: Filter by org to prevent cross-org enumeration
        # Lookup by user ID since frontend sends user.id, not profile.id
        return get_object_or_404(Profile, user__id=pk, org=self.request.profile.org)

    @staticmethod
    def _may_grant_privileges(request, target_profile):
        """May this request set role / admin / access flags on `target_profile`?

        Two rules, both of which the /v2/team UI already advertises as enforced:

        * only an admin (or superuser) may hand out access, and
        * nobody may change their *own* role. An admin included, so the org
          cannot be self-locked out of its last admin and a member cannot
          promote themselves.

        Editing your own profile for contact details stays allowed; only the
        privileged fields are withheld, by making them read_only downstream.
        """
        actor_is_admin = is_org_admin(request.profile) or request.user.is_superuser
        editing_self = request.profile.id == target_profile.id
        return actor_is_admin and not editing_self

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="UserDetailResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "data": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request, pk, format=None):
        profile_obj = self.get_object(pk)
        if (
            not is_org_admin(self.request.profile)
            and self.request.profile.id != profile_obj.id
        ):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Org check now handled by get_object_or_404 in get_object()
        assigned_data = Profile.objects.filter(
            org=request.profile.org, is_active=True
        ).values("id", "user__email")
        context = {}
        context["profile_obj"] = ProfileSerializer(profile_obj).data
        # Security fix: Add org filter to prevent cross-org data leakage
        opportunity_list = Opportunity.objects.filter(
            assigned_to=profile_obj, org=request.profile.org
        )
        context["opportunity_list"] = OpportunitySerializer(
            opportunity_list, many=True
        ).data
        contacts = Contact.objects.filter(
            assigned_to=profile_obj, org=request.profile.org
        )
        context["contacts"] = ContactSerializer(contacts, many=True).data
        cases = Case.objects.filter(assigned_to=profile_obj, org=request.profile.org)
        context["cases"] = CaseSerializer(cases, many=True).data
        context["assigned_data"] = assigned_data
        comments = Comment.objects.filter(
            commented_by=profile_obj, org=request.profile.org
        )
        context["comments"] = CommentSerializer(comments, many=True).data
        context["countries"] = COUNTRIES
        return Response(
            {"error": False, "data": context},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        request=UserCreateSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="UserUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        profile = self.get_object(pk)
        address_obj = profile.address
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
            and self.request.profile.id != profile.id
        ):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if profile.org != request.profile.org:
            return Response(
                {"error": True, "errors": "User company doesnot match with header...."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CreateUserSerializer(
            data=params, instance=profile.user, org=request.profile.org
        )
        address_serializer = BillingAddressSerializer(data=params, instance=address_obj)
        # Role and the access flags may be set only by an admin editing someone
        # *other* than themselves. A member reaches this path only for their own
        # profile (the guard above lets self through), so this denies them the
        # privileged fields; and an admin cannot flip their own role here either,
        # which keeps them from self-locking the org out of its last admin.
        profile_serializer = CreateProfileSerializer(
            data=params,
            instance=profile,
            can_grant_privileges=self._may_grant_privileges(request, profile),
        )
        data = {}
        if not serializer.is_valid():
            data["contact_errors"] = serializer.errors
        if not address_serializer.is_valid():
            data["address_errors"] = (address_serializer.errors,)
        if not profile_serializer.is_valid():
            data["profile_errors"] = (profile_serializer.errors,)
        if data:
            data["error"] = True
            return Response(
                data,
                status=status.HTTP_400_BAD_REQUEST,
            )
        if address_serializer.is_valid():
            address_obj = address_serializer.save(org=request.profile.org)
            user = serializer.save()
            user.email = user.email
            user.save()
        if profile_serializer.is_valid():
            profile = profile_serializer.save()
            return Response(
                {"error": False, "message": "User Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        request=UserCreateSwaggerSerializer,
        description="Partial User Update",
        responses={
            200: inline_serializer(
                name="UserPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a user."""
        params = request.data
        profile = self.get_object(pk)
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
            and self.request.profile.id != profile.id
        ):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if profile.org != request.profile.org:
            return Response(
                {
                    "error": True,
                    "errors": "User company does not match with header....",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateUserSerializer(
            data=params, instance=profile.user, org=request.profile.org, partial=True
        )
        profile_serializer = CreateProfileSerializer(
            data=params,
            instance=profile,
            partial=True,
            can_grant_privileges=self._may_grant_privileges(request, profile),
        )
        data = {}
        if not serializer.is_valid():
            data["contact_errors"] = serializer.errors
        if not profile_serializer.is_valid():
            data["profile_errors"] = profile_serializer.errors
        if data:
            data["error"] = True
            return Response(data, status=status.HTTP_400_BAD_REQUEST)

        if serializer.is_valid():
            serializer.save()
        if profile_serializer.is_valid():
            profile = profile_serializer.save()
            return Response(
                {"error": False, "message": "User Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(
        tags=["users"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="UserDeleteResponse", fields={"status": serializers.CharField()}
            )
        },
    )
    def delete(self, request, pk, format=None):
        if not is_org_admin(self.request.profile):
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        self.object = self.get_object(pk)
        if self.object.id == request.profile.id:
            return Response(
                {"error": True, "errors": "Permission Denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        deleted_by = self.request.profile.user.email
        recipient = self.object.user.email
        try:
            self.object.delete()
        except ProtectedError:
            # TimeEntry.profile, Approval.requested_by and Approval.approver all
            # point here with on_delete=PROTECT, so a member who has logged time
            # or touched an approval cannot be removed. Unguarded this was a 500,
            # and the notice below had already gone out by then: the user was
            # told they had been removed from an org they were still in.
            return Response(
                {
                    "error": True,
                    "errors": "This user can't be deleted while they still have "
                    "time entries or approvals linked to them.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        send_email_user_delete.delay(recipient, deleted_by=deleted_by)
        return Response({"status": "success"}, status=status.HTTP_200_OK)


class UserStatusView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["users"],
        description="User Status View",
        parameters=swagger_params.organization_params,
        request=UserUpdateStatusSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="UserStatusResponse",
                fields={
                    "active_profiles": ProfileSerializer(many=True),
                    "inactive_profiles": ProfileSerializer(many=True),
                },
            )
        },
    )
    def post(self, request, pk, format=None):
        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            return Response(
                {
                    "error": True,
                    "errors": "You do not have permission to perform this action",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        params = request.data
        profiles = Profile.objects.filter(org=request.profile.org)
        # Lookup by user ID since frontend sends user.id, not profile.id.
        # get_object_or_404 (a 404), not .get() (a 500), on an unknown id.
        profile = get_object_or_404(profiles, user__id=pk)

        if params.get("status"):
            user_status = params.get("status")
            if user_status == "Active":
                profile.is_active = True
            elif user_status == "Inactive":
                # Deactivating a profile is the one remaining way to strand an
                # org with no admin (self role-change is blocked in the
                # serializer), and an org with no admin can never invite,
                # promote or reconfigure itself again. Refuse to deactivate the
                # last active admin, the rule the /v2/team page advertises.
                target_is_admin = is_org_admin(profile)
                if target_is_admin and not (
                    profiles.filter(is_active=True)
                    .filter(role="ADMIN")
                    .exclude(pk=profile.pk)
                    .exists()
                ):
                    return Response(
                        {
                            "error": True,
                            "errors": "The organization must keep at least one active admin.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                profile.is_active = False
            else:
                return Response(
                    {"error": True, "errors": "Please enter Valid Status for user"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.save()

        context = {}
        active_profiles = profiles.filter(is_active=True)
        inactive_profiles = profiles.filter(is_active=False)
        context["active_profiles"] = ProfileSerializer(active_profiles, many=True).data
        context["inactive_profiles"] = ProfileSerializer(
            inactive_profiles, many=True
        ).data
        return Response(context)
