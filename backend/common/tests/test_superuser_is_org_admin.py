"""A Django superuser is an org admin in every org they are a member of.

Owner decision for 1.11.0. ``is_org_admin`` read ``role`` alone while dozens of
call sites added ``or request.user.is_superuser`` by hand and the rest did not,
and both clients gated admin UI on ``role == "ADMIN"``, so a superuser holding
the USER role saw read-only settings pages the API would have let them change.

What is pinned here:

* the rule itself, both ways, on real profiles;
* the one fact the clients gate on (``is_organization_admin`` in the JWT, the
  org lists and the profile payloads) agrees with the rule;
* a representative admin-only endpoint admits the superuser and refuses a
  plain member;
* membership is still required, and neither ``is_superuser``, ``is_staff`` nor
  ``role`` can be written by the caller through the API.
"""

from unittest.mock import MagicMock

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from common.models import Profile, User
from common.permissions import IsOrgAdmin, is_org_admin
from common.serializer import OrgAwareRefreshToken
from common.testing import _make_authenticated_client
from common.views.auth_views import _org_payload


@pytest.fixture
def super_user():
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


@pytest.fixture
def super_profile(super_user, org_a):
    return Profile.objects.create(
        user=super_user, org=org_a, role="USER", is_active=True
    )


@pytest.fixture
def super_client(super_user, org_a, super_profile):
    return _make_authenticated_client(super_user, org_a, super_profile)


@pytest.mark.django_db
class TestTheRule:
    def test_admin_role_is_admin(self, admin_profile):
        assert is_org_admin(admin_profile) is True

    def test_user_role_superuser_is_admin(self, super_profile):
        assert is_org_admin(super_profile) is True

    def test_user_role_member_is_not(self, user_profile):
        assert is_org_admin(user_profile) is False

    def test_permission_class_agrees(self, super_profile, user_profile):
        class _Request:
            def __init__(self, profile):
                self.profile = profile

        assert IsOrgAdmin().has_permission(_Request(super_profile), None) is True
        assert IsOrgAdmin().has_permission(_Request(user_profile), None) is False

    def test_profile_is_admin_property_delegates(self, super_profile, user_profile):
        assert super_profile.is_admin is True
        assert user_profile.is_admin is False

    def test_a_truthy_non_bool_is_not_a_superuser(self):
        """A test double answers every attribute with a truthy Mock."""
        profile = MagicMock()
        profile.role = "USER"
        assert is_org_admin(profile) is False

    def test_the_column_still_mirrors_the_role_only(self, super_profile):
        """`is_organization_admin` the column is not the fact; the API field is."""
        super_profile.refresh_from_db()
        assert super_profile.is_organization_admin is False


@pytest.mark.django_db
class TestThePayloadFactMatchesTheRule:
    def _claim(self, user, org, profile):
        token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)
        return AccessToken(str(token.access_token)).get("is_organization_admin")

    def test_jwt_claim(
        self,
        org_a,
        super_user,
        super_profile,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
    ):
        assert self._claim(super_user, org_a, super_profile) is True
        assert self._claim(admin_user, org_a, admin_profile) is True
        assert self._claim(regular_user, org_a, user_profile) is False

    def test_org_payload(self, org_a, super_profile, user_profile):
        assert _org_payload(org_a, profile=super_profile)["is_organization_admin"]
        member = _org_payload(org_a, profile=user_profile)
        assert member["is_organization_admin"] is False
        assert member["role"] == "USER"
        assert "is_organization_admin" not in _org_payload(org_a)

    def test_profile_endpoint(self, super_client, user_client):
        assert super_client.get("/api/auth/profile/").data["is_organization_admin"]
        member = user_client.get("/api/auth/profile/").data
        assert member["is_organization_admin"] is False

    def test_me_endpoint(self, super_client, user_client, org_a):
        orgs = super_client.get("/api/auth/me/").data["organizations"]
        assert [o["is_organization_admin"] for o in orgs] == [True]
        orgs = user_client.get("/api/auth/me/").data["organizations"]
        assert [o["is_organization_admin"] for o in orgs] == [False]

    def test_org_list_endpoint(self, super_client, user_client):
        rows = super_client.get("/api/org/").data["profile_org_list"]
        assert [r["is_organization_admin"] for r in rows] == [True]
        rows = user_client.get("/api/org/").data["profile_org_list"]
        assert [r["is_organization_admin"] for r in rows] == [False]

    def test_switch_org_response_and_new_token(
        self, super_user, super_profile, org_a, regular_user, user_profile
    ):
        for user, expected in ((super_user, True), (regular_user, False)):
            client = APIClient()
            token = OrgAwareRefreshToken.for_user_and_org(user, None)
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
            response = client.post(
                "/api/auth/switch-org/", {"org_id": str(org_a.id)}, format="json"
            )
            assert response.status_code == status.HTTP_200_OK, response.data
            assert response.data["profile"]["is_organization_admin"] is expected
            claims = AccessToken(response.data["access_token"])
            assert claims["is_organization_admin"] is expected


@pytest.mark.django_db
class TestAnAdminOnlyEndpoint:
    def test_api_settings_create_admits_a_user_role_superuser(self, super_client):
        response = super_client.post(
            "/api/api-settings/",
            {"title": "Site", "website": "https://site.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_api_settings_create_refuses_a_member(self, user_client):
        response = user_client.post(
            "/api/api-settings/",
            {"title": "Site", "website": "https://site.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_pack_sample_data_gate_admits_superuser_and_refuses_member(
        self, super_client, user_client
    ):
        """`_require_admin` in pack_views used to compare `role` inline."""
        assert (
            super_client.delete("/api/packs/sample-data/").status_code
            != status.HTTP_403_FORBIDDEN
        )
        assert (
            user_client.delete("/api/packs/sample-data/").status_code
            == status.HTTP_403_FORBIDDEN
        )


@pytest.mark.django_db
class TestNoEscalation:
    def test_superuser_without_a_profile_cannot_enter_another_org(
        self, super_user, super_profile, org_b
    ):
        """Superuser is admin in orgs they belong to; it is not a pass into others."""
        client = APIClient()
        token = OrgAwareRefreshToken.for_user_and_org(super_user, None)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        response = client.post(
            "/api/auth/switch-org/", {"org_id": str(org_b.id)}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_inactive_superuser_membership_gets_no_org_context(
        self, super_user, super_profile, org_a
    ):
        client = _make_authenticated_client(super_user, org_a, super_profile)
        super_profile.is_active = False
        super_profile.save()
        response = client.post(
            "/api/api-settings/",
            {"title": "Site", "website": "https://site.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_make_themselves_superuser_or_admin(
        self, user_client, regular_user, user_profile
    ):
        body = {
            "is_superuser": True,
            "is_staff": True,
            "role": "ADMIN",
            "is_organization_admin": True,
            "name": "Still Me",
        }
        user_client.patch("/api/profile/", body, format="json")
        user_client.put(f"/api/user/{regular_user.id}/", body, format="json")

        regular_user.refresh_from_db()
        user_profile.refresh_from_db()
        assert regular_user.is_superuser is False
        assert regular_user.is_staff is False
        assert user_profile.role == "USER"
        assert is_org_admin(user_profile) is False

    def test_admin_cannot_make_a_member_superuser(
        self, admin_client, regular_user, user_profile
    ):
        admin_client.put(
            f"/api/user/{regular_user.id}/",
            {
                "email": regular_user.email,
                "role": "USER",
                "is_superuser": True,
                "is_staff": True,
            },
            format="json",
        )
        regular_user.refresh_from_db()
        assert regular_user.is_superuser is False
        assert regular_user.is_staff is False

    def test_superuser_cannot_change_their_own_role(
        self, super_client, super_user, super_profile
    ):
        """Being an admin does not lift the no-self-role-change rule."""
        response = super_client.put(
            f"/api/user/{super_user.id}/",
            {"email": super_user.email, "role": "ADMIN"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        super_profile.refresh_from_db()
        assert super_profile.role == "USER"


@pytest.mark.django_db
def test_an_admin_approval_rule_counts_a_superuser_as_an_admin(
    org_a, super_profile, user_profile, admin_profile
):
    """`Approval.can_be_acted_on_by` compared `role` to the rule's role inline."""
    from cases.approvals import Approval, ApprovalRule

    rule = ApprovalRule.objects.create(org=org_a, name="Admins", approver_role="ADMIN")
    approval = Approval(org=org_a, rule=rule)

    assert approval.can_be_acted_on_by(admin_profile) is True
    assert approval.can_be_acted_on_by(super_profile) is True
    assert approval.can_be_acted_on_by(user_profile) is False


@pytest.mark.django_db
def test_org_create_response_carries_the_creators_admin_fact(regular_user):
    """Mobile caches the created org straight from this response; without the
    fact the creator saw no admin controls until they signed in again."""
    client = APIClient()
    token = OrgAwareRefreshToken.for_user_and_org(regular_user, None)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    response = client.post(
        "/api/org/", {"name": "Brand New Org", "is_organization_admin": False}
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    org = response.data["org"]
    assert org["role"] == "ADMIN"
    assert org["is_organization_admin"] is True
    assert Profile.objects.get(user=regular_user, org_id=org["id"]).role == "ADMIN"
