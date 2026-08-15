"""
Tests for user management views: list users, user detail, update user, status.

Run with: pytest common/tests/test_users.py -v
"""

from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from cases.approvals import Approval, ApprovalRule
from cases.models import Case, TimeEntry
from common.models import Address, Profile
from common.permissions import is_org_admin


@pytest.mark.django_db
class TestUsersListView:
    """Tests for GET /api/users/"""

    url = "/api/users/"

    def test_list_users_as_admin(self, admin_client):
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "active_users" in response.data

    def test_list_users_unauthenticated(self, unauthenticated_client):
        """The request is refused, and refused as a response, not an exception.

        DRF catches ``PermissionDenied`` in ``handle_exception`` and renders it,
        so a test client never sees it raised. Wrapping the call in
        ``pytest.raises`` therefore failed with DID NOT RAISE no matter how the
        endpoint behaved, which meant this test could never have caught the
        access opening up.
        """
        response = unauthenticated_client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_users_no_cross_org_leak(
        self, org_b_client, admin_user, admin_profile
    ):
        """org_b_client should not see org_a's users in its listing."""
        response = org_b_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        # Collect all user emails across active and inactive lists
        active = response.data["active_users"]["active_users"]
        inactive = response.data["inactive_users"]["inactive_users"]
        all_emails = [p["user_details"]["email"] for p in active] + [
            p["user_details"]["email"] for p in inactive
        ]
        assert admin_user.email not in all_emails

    def test_list_users_non_admin_forbidden(self, user_client):
        """Non-admin user should get 403 on user list."""
        response = user_client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_users_context_keys(self, admin_client):
        """User list should include expected context keys."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert "active_users" in data
        assert "inactive_users" in data
        assert "admin_email" in data
        assert "roles" in data
        assert "status" in data

    def test_list_users_with_email_filter(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        """Filter users by email."""
        response = admin_client.get(self.url + "?email=admin")
        assert response.status_code == status.HTTP_200_OK
        active = response.data["active_users"]["active_users"]
        emails = [p["user_details"]["email"] for p in active]
        assert "admin@test.com" in emails

    def test_list_users_with_role_filter(
        self, admin_client, org_a, admin_profile, user_profile
    ):
        """Filter users by role."""
        response = admin_client.get(self.url + "?role=ADMIN")
        assert response.status_code == status.HTTP_200_OK
        active = response.data["active_users"]["active_users"]
        roles = [p["role"] for p in active]
        assert all(r == "ADMIN" for r in roles)

    def test_create_user_validation_error(self, admin_client, org_a):
        """Creating user with duplicate email returns 400."""
        # First create a user that exists
        from common.models import User

        User.objects.create_user(email="existing@test.com", password="pass123")
        from common.models import Profile as ProfileModel

        ProfileModel.objects.create(
            user=User.objects.get(email="existing@test.com"),
            org=org_a,
            role="USER",
            is_active=True,
        )
        response = admin_client.post(
            self.url,
            {
                "email": "existing@test.com",
                "role": "USER",
                "phone": "+1111111111",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_user_non_admin_forbidden(self, user_client):
        """Non-admin user cannot create users."""
        response = user_client.post(
            self.url,
            {
                "email": "nope@test.com",
                "role": "USER",
                "phone": "+1111111112",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_user_empty_body(self, admin_client):
        """Empty POST body returns 400."""
        response = admin_client.post(
            self.url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_user_minimal_payload(self, admin_client, org_a):
        """Admin can create a user with just email and role."""
        response = admin_client.post(
            self.url,
            {
                "email": "new-member@test.com",
                "role": "USER",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Profile.objects.filter(
            user__email="new-member@test.com", org=org_a
        ).exists()

    def test_create_user_minimal_payload_creates_no_address(self, admin_client, org_a):
        """No address fields sent -> no empty Address row is created."""
        response = admin_client.post(
            self.url,
            {"email": "no-address@test.com", "role": "USER"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        profile = Profile.objects.get(user__email="no-address@test.com", org=org_a)
        assert profile.address is None

    def test_create_user_with_address_scopes_address_to_org(self, admin_client, org_a):
        """Address fields sent -> Address is created and scoped to the admin's org."""
        response = admin_client.post(
            self.url,
            {
                "email": "with-address@test.com",
                "role": "USER",
                "city": "Hyderabad",
                "country": "IN",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        profile = Profile.objects.get(user__email="with-address@test.com", org=org_a)
        assert profile.address is not None
        assert profile.address.city == "Hyderabad"
        assert profile.address.org == org_a

    def test_create_user_reuses_existing_account_from_another_org(
        self, admin_client, org_a, org_b
    ):
        """Inviting an email that already has an account adds a Profile, not a 400."""
        from common.models import User

        existing = User.objects.create_user(
            email="veteran@test.com", password="pass123"
        )
        Profile.objects.create(user=existing, org=org_b, role="USER", is_active=True)

        response = admin_client.post(
            self.url,
            {"email": "veteran@test.com", "role": "USER"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        # Same underlying account, now a member of both orgs.
        assert User.objects.filter(email="veteran@test.com").count() == 1
        new_profile = Profile.objects.get(user=existing, org=org_a)
        assert new_profile.role == "USER"
        assert Profile.objects.filter(user=existing, org=org_b).exists()

    def test_create_user_reuse_does_not_mutate_existing_account(
        self, admin_client, org_a, org_b
    ):
        """Inviting an existing account must not touch that account's own fields."""
        from common.models import User

        existing = User.objects.create_user(
            email="dormant@test.com", password="pass123"
        )
        existing.name = "Original Name"
        existing.profile_pic = "original.png"
        existing.is_active = False
        existing.save()
        Profile.objects.create(user=existing, org=org_b, role="ADMIN", is_active=True)

        response = admin_client.post(
            self.url,
            {
                "email": "dormant@test.com",
                "role": "USER",
                "profile_pic": "attacker.png",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        existing.refresh_from_db()
        assert existing.name == "Original Name"
        assert existing.profile_pic == "original.png"
        # A deactivated account must not be silently re-enabled by an invite.
        assert existing.is_active is False

    def test_create_user_losing_race_returns_400_not_500(self, admin_client, org_a):
        """A concurrent invite that wins the DB race yields 400, never a 500."""
        from unittest.mock import patch

        from django.db import IntegrityError

        # Stands in for a second request that committed the same membership
        # between this request's validation and its INSERT.
        with patch.object(
            Profile.objects, "create", side_effect=IntegrityError("duplicate key")
        ):
            response = admin_client.post(
                self.url,
                {"email": "raced@test.com", "role": "USER"},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_user_rolls_back_account_when_membership_fails(
        self, admin_client, org_a
    ):
        """A failed invite must not strand a half-built account behind it."""
        from unittest.mock import patch

        from django.db import IntegrityError

        from common.models import User

        with patch.object(
            Profile.objects, "create", side_effect=IntegrityError("duplicate key")
        ):
            admin_client.post(
                self.url,
                {"email": "orphan@test.com", "role": "USER", "city": "Hyderabad"},
                format="json",
            )

        assert not User.objects.filter(email="orphan@test.com").exists()
        assert not Address.objects.filter(city="Hyderabad").exists()

    def test_create_user_duplicate_within_same_org_rejected(self, admin_client, org_a):
        """Re-inviting someone who is already a member of this org still 400s."""
        from common.models import User

        member = User.objects.create_user(email="member@test.com", password="pass123")
        Profile.objects.create(user=member, org=org_a, role="USER", is_active=True)

        response = admin_client.post(
            self.url,
            {"email": "member@test.com", "role": "USER"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Profile.objects.filter(user=member, org=org_a).count() == 1


@pytest.mark.django_db
class TestUserDetailView:
    """Tests for GET/PUT/PATCH/DELETE /api/user/<pk>/"""

    def _url(self, user_id):
        return f"/api/user/{user_id}/"

    def test_get_user_detail(self, admin_client, admin_user):
        response = admin_client.get(self._url(admin_user.id))
        assert response.status_code == status.HTTP_200_OK
        assert "data" in response.data

    def test_update_user(self, admin_client, admin_user):
        response = admin_client.put(
            self._url(admin_user.id),
            {
                "email": admin_user.email,
                "role": "ADMIN",
                "phone": "+1234567890",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_get_user_wrong_org(self, org_b_client, admin_user, admin_profile):
        """org_b_client cannot view a user that belongs to org_a."""
        response = org_b_client.get(self._url(admin_user.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_user_detail_context(self, admin_client, admin_user):
        """User detail should return full profile data."""
        response = admin_client.get(self._url(admin_user.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.data["data"]
        assert "profile_obj" in data
        assert "opportunity_list" in data
        assert "contacts" in data
        assert "cases" in data
        assert "assigned_data" in data
        assert "comments" in data
        assert "countries" in data

    def test_get_user_detail_non_admin_own_profile(
        self, user_client, regular_user, user_profile
    ):
        """Non-admin can view their own profile."""
        response = user_client.get(self._url(regular_user.id))
        assert response.status_code == status.HTTP_200_OK

    def test_get_user_detail_non_admin_other_profile(
        self, user_client, admin_user, admin_profile, user_profile
    ):
        """Non-admin cannot view someone else's profile."""
        response = user_client.get(self._url(admin_user.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_user_non_admin_own(self, user_client, regular_user, user_profile):
        """Non-admin can update their own profile."""
        response = user_client.put(
            self._url(regular_user.id),
            {
                "email": regular_user.email,
                "role": "USER",
                "phone": "+9876543210",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_update_user_non_admin_other(
        self, user_client, admin_user, admin_profile, user_profile
    ):
        """Non-admin cannot update someone else's profile."""
        response = user_client.put(
            self._url(admin_user.id),
            {
                "email": admin_user.email,
                "role": "USER",
                "phone": "+1111111111",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_user(self, admin_client, admin_user):
        """Test partial update via PATCH."""
        response = admin_client.patch(
            self._url(admin_user.id),
            {"phone": "+5555555555"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_patch_user_email_taken_by_another_account(
        self, admin_client, org_a, org_b, regular_user, user_profile
    ):
        """Renaming a user onto an email another account owns is a clean 400."""
        from common.models import User

        other = User.objects.create_user(email="taken@test.com", password="pass123")
        Profile.objects.create(user=other, org=org_b, role="USER", is_active=True)

        response = admin_client.patch(
            self._url(regular_user.id),
            {"email": "taken@test.com"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        regular_user.refresh_from_db()
        assert regular_user.email != "taken@test.com"

    def test_patch_user_non_admin_other(
        self, user_client, admin_user, admin_profile, user_profile
    ):
        """Non-admin cannot patch someone else's profile."""
        response = user_client.patch(
            self._url(admin_user.id),
            {"phone": "+0000000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_user_as_admin(
        self, admin_client, org_a, regular_user, user_profile
    ):
        """Admin can delete another user."""
        response = admin_client.delete(self._url(regular_user.id))
        assert response.status_code == status.HTTP_200_OK

    def test_delete_user_self_forbidden(self, admin_client, admin_user):
        """Admin cannot delete themselves."""
        response = admin_client.delete(self._url(admin_user.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_user_non_admin_forbidden(
        self, user_client, admin_user, admin_profile, user_profile
    ):
        """Non-admin cannot delete users."""
        response = user_client.delete(self._url(admin_user.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("common.views.user_views.send_email_user_delete")
    def test_delete_user_tells_them_they_were_removed(
        self, send_email, admin_client, org_a, regular_user, user_profile
    ):
        """The notice goes out, and only once the removal has actually happened."""
        response = admin_client.delete(self._url(regular_user.id))

        assert response.status_code == status.HTTP_200_OK
        send_email.delay.assert_called_once_with(
            regular_user.email, deleted_by="admin@test.com"
        )

    @pytest.mark.parametrize("relation", ["time-entry", "approval"])
    @patch("common.views.user_views.send_email_user_delete")
    def test_delete_user_with_protected_history_is_refused(
        self, send_email, admin_client, org_a, regular_user, user_profile, relation
    ):
        """A member who logged time or raised an approval cannot be deleted.

        Both relations are on_delete=PROTECT, so the delete raises and the view
        used to answer 500. The email assertion is the other half of it: the
        notice was sent before the delete was attempted, so a user who was still
        in the org was told they had been removed from it.
        """
        case = Case.objects.create(
            name="Blocking Case", status="New", priority="Normal", org=org_a
        )
        if relation == "time-entry":
            TimeEntry.objects.create(
                org=org_a, case=case, profile=user_profile, started_at=timezone.now()
            )
        else:
            rule = ApprovalRule.objects.create(name="Discount sign-off", org=org_a)
            Approval.objects.create(
                org=org_a, case=case, rule=rule, requested_by=user_profile
            )

        response = admin_client.delete(self._url(regular_user.id))

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["errors"] == (
            "This user can't be deleted while they still have time entries or "
            "approvals linked to them."
        )
        assert Profile.objects.filter(id=user_profile.id).exists()
        send_email.delay.assert_not_called()


@pytest.mark.django_db
class TestUserStatusView:
    """Tests for POST /api/user/<pk>/status/"""

    def _url(self, user_id):
        return f"/api/user/{user_id}/status/"

    def test_activate_user(self, admin_client, org_a, regular_user, user_profile):
        """Admin can activate a user."""
        user_profile.is_active = False
        user_profile.save()
        response = admin_client.post(
            self._url(regular_user.id),
            {"status": "Active"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.is_active is True

    def test_deactivate_user(self, admin_client, org_a, regular_user, user_profile):
        """Admin can deactivate a user."""
        response = admin_client.post(
            self._url(regular_user.id),
            {"status": "Inactive"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.is_active is False

    def test_invalid_status(self, admin_client, org_a, regular_user, user_profile):
        """Invalid status value returns 400."""
        response = admin_client.post(
            self._url(regular_user.id),
            {"status": "InvalidStatus"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_status_non_admin_forbidden(
        self, user_client, org_a, admin_user, admin_profile, user_profile
    ):
        """Non-admin cannot change user status."""
        response = user_client.post(
            self._url(admin_user.id),
            {"status": "Inactive"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_status_returns_profiles(
        self, admin_client, org_a, regular_user, user_profile
    ):
        """Status endpoint should return active and inactive profiles."""
        response = admin_client.post(
            self._url(regular_user.id),
            {"status": "Active"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "active_profiles" in response.data
        assert "inactive_profiles" in response.data

    def test_cannot_deactivate_last_admin(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        """Deactivating the org's only admin is refused, no admin, no recovery."""
        response = admin_client.post(
            self._url(admin_user.id),
            {"status": "Inactive"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        admin_profile.refresh_from_db()
        assert admin_profile.is_active is True

    def test_can_deactivate_admin_when_another_admin_exists(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        """With a second admin present, deactivating one is allowed."""
        from common.models import User

        second = User.objects.create_user(
            email="second-admin@test.com", password="pass123"
        )
        Profile.objects.create(user=second, org=org_a, role="ADMIN", is_active=True)

        response = admin_client.post(
            self._url(admin_user.id),
            {"status": "Inactive"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        admin_profile.refresh_from_db()
        assert admin_profile.is_active is False

    def test_status_unknown_user_is_404_not_500(self, admin_client, org_a):
        """An unknown user id is a clean 404, not a 500 from an unguarded .get()."""
        response = admin_client.post(
            self._url("00000000-0000-0000-0000-000000000000"),
            {"status": "Active"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestGetTeamsAndUsersView:
    """Tests for GET /api/users/get-teams-and-users/"""

    url = "/api/users/get-teams-and-users/"

    def test_get_teams_and_users(self, admin_client, org_a):
        """Returns teams and profiles for the org."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "teams" in response.data
        assert "profiles" in response.data

    def test_permission_classes_include_has_org_context(self):
        """Without HasOrgContext a valid JWT that carries no org_id claim leaves
        request.profile as None and the view dereferences it, so the caller gets
        an uncaught AttributeError 500 instead of a clean 403."""
        from common.permissions import HasOrgContext
        from common.views.user_views import GetTeamsAndUsersView

        assert HasOrgContext in GetTeamsAndUsersView.permission_classes

    def test_no_org_context_is_denied(self):
        """The permission must be able to return False, not only True."""
        from common.permissions import HasOrgContext

        class _Request:
            profile = None
            org = None

        assert HasOrgContext().has_permission(_Request(), None) is False

    def test_org_context_present_is_allowed(self, admin_profile, org_a):
        """The permission must be able to return True, not only False, so a
        normal org member with an active profile and an org on the request
        is let through."""
        from unittest.mock import MagicMock

        from common.permissions import HasOrgContext

        request = MagicMock()
        request.profile = admin_profile
        request.profile.is_active = True
        request.org = org_a
        assert HasOrgContext().has_permission(request, None) is True


@pytest.mark.django_db
class TestProfilePrivilegeEscalation:
    """`role` and the access flags are grantable only by an admin acting on
    someone else. A member editing their own profile was able to PATCH
    themselves to role="ADMIN". A live org-admin escalation. These pin both
    the block and the legitimate admin path so neither drifts.
    """

    def _url(self, user_id):
        return f"/api/user/{user_id}/"

    def test_member_cannot_self_escalate_role_via_patch(
        self, user_client, regular_user, user_profile
    ):
        response = user_client.patch(
            self._url(regular_user.id),
            {"role": "ADMIN"},
            format="json",
        )
        # The field being read_only is what makes this safe, and that half has
        # not changed. What changed is the answer: DRF drops a read_only field
        # in silence, so this used to be a 200 reading "User Updated
        # Successfully" over a profile that had not been updated, and both
        # clients then told the user their role had changed.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "role" in response.data["profile_errors"]
        user_profile.refresh_from_db()
        assert user_profile.role == "USER"

    def test_member_cannot_self_escalate_admin_flag_via_patch(
        self, user_client, regular_user, user_profile
    ):
        response = user_client.patch(
            self._url(regular_user.id),
            {"is_organization_admin": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.is_organization_admin is False

    def test_member_cannot_self_escalate_role_via_put(
        self, user_client, regular_user, user_profile
    ):
        response = user_client.put(
            self._url(regular_user.id),
            {"email": regular_user.email, "role": "ADMIN", "phone": "+15550001111"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user_profile.refresh_from_db()
        assert user_profile.role == "USER"

    def test_a_put_echoing_the_role_it_already_has_is_not_refused(
        self, user_client, regular_user, user_profile
    ):
        """PUT is a whole-object update, so a client sends the fields it read
        back, role included. Only a *different* value is a change to refuse;
        asking for the role you already have is asking for nothing."""
        response = user_client.put(
            self._url(regular_user.id),
            {"email": regular_user.email, "role": "USER", "phone": "+15550001111"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.role == "USER"
        assert user_profile.phone == "+15550001111"

    def test_self_escalation_attempt_grants_no_admin_access(
        self, user_client, regular_user, user_profile
    ):
        """End-to-end: the attempt must not unlock the admin-only roster."""
        user_client.patch(
            self._url(regular_user.id),
            {"role": "ADMIN"},
            format="json",
        )
        after = user_client.get("/api/users/")
        assert after.status_code == status.HTTP_403_FORBIDDEN

    def test_a_patch_echoing_the_access_flags_is_not_refused(
        self, user_client, regular_user, user_profile
    ):
        """`has_sales_access` and `has_marketing_access` are privileged too, and
        the same echo-back rule applies to them: sending what is already stored
        is not a change."""
        response = user_client.patch(
            self._url(regular_user.id),
            {
                "phone": "+15551112222",
                "has_sales_access": user_profile.has_sales_access,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_a_member_flipping_an_access_flag_is_refused(
        self, user_client, regular_user, user_profile
    ):
        response = user_client.patch(
            self._url(regular_user.id),
            {"has_marketing_access": not user_profile.has_marketing_access},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        was = user_profile.has_marketing_access
        user_profile.refresh_from_db()
        assert user_profile.has_marketing_access == was

    def test_member_can_still_edit_own_safe_fields(
        self, user_client, regular_user, user_profile
    ):
        """The fix must not lock members out of their own contact details."""
        response = user_client.patch(
            self._url(regular_user.id),
            {"phone": "+15559998888"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.phone == "+15559998888"
        assert user_profile.role == "USER"

    def test_admin_can_promote_and_demote_another_member(
        self, admin_client, regular_user, user_profile
    ):
        promote = admin_client.patch(
            self._url(regular_user.id),
            {"role": "ADMIN"},
            format="json",
        )
        assert promote.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.role == "ADMIN"

        demote = admin_client.patch(
            self._url(regular_user.id),
            {"role": "USER"},
            format="json",
        )
        assert demote.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.role == "USER"

    def test_admin_cannot_change_their_own_role(
        self, admin_client, admin_user, admin_profile
    ):
        """Even an admin cannot self-demote here, anti self-lockout."""
        response = admin_client.patch(
            self._url(admin_user.id),
            {"role": "USER"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        admin_profile.refresh_from_db()
        assert admin_profile.role == "ADMIN"

    def test_admin_cannot_mint_a_hidden_admin_via_the_flag(
        self, admin_client, regular_user, user_profile
    ):
        """The grant that no screen could show and no screen could take back.

        `is_organization_admin` was a writable privileged field and 43 checks
        across the backend accepted it in place of `role`. No frontend surface
        reads or writes the column, so a member carrying it appeared, on every
        page an operator can open, to be an ordinary member, while accounts,
        contacts, documents, team management and the org record treated them
        as an admin. `role` is now the only input.
        """
        response = admin_client.patch(
            self._url(regular_user.id),
            {"is_organization_admin": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user_profile.refresh_from_db()
        assert user_profile.is_organization_admin is False
        assert user_profile.role == "USER"

    def test_the_flag_grants_nothing_even_when_written_directly(
        self, user_client, user_profile
    ):
        """Defence in depth for rows written before the field was closed.

        A database may already hold `is_organization_admin=True` on a member,
        from the old serializer or a direct write. Reading it back must not
        reopen the endpoints the flag used to unlock, so this bypasses the
        model entirely with a queryset update.
        """
        Profile.objects.filter(pk=user_profile.pk).update(is_organization_admin=True)

        # The roster is the check that mattered: admin-only, and one of the 43
        # that used to read the flag.
        assert user_client.get("/api/users/").status_code == status.HTTP_403_FORBIDDEN

        user_profile.refresh_from_db()
        assert user_profile.is_organization_admin is True
        assert is_org_admin(user_profile) is False
