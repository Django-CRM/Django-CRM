"""
Tests for organization management views: OrgProfileCreateView, OrgUpdateView,
ProfileView, ProfileDetailView, OrgSettingsView.

Run with: pytest common/tests/test_organizations.py -v
"""

import pytest
from rest_framework import status

from common.models import Org, Profile, Teams


@pytest.mark.django_db
class TestOrgProfileCreateView:
    """Tests for GET/POST /api/org/"""

    url = "/api/org/"

    def test_create_org(self, admin_client, admin_user):
        """Create a new organization."""
        response = admin_client.post(
            self.url,
            {"name": "New Organization"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["error"] is False
        assert response.data["message"] == "New Org is Created."
        # The body's `status` predates the real HTTP status and now agrees
        # with it; it stays so existing API consumers keep their shape.
        assert response.data["status"] == 201
        assert response.data["org"]["name"] == "New Organization"
        # User should be an admin of the new org
        assert Profile.objects.filter(
            user=admin_user, org__name="New Organization", role="ADMIN"
        ).exists()

    def test_create_org_response_includes_id(self, admin_client, admin_user):
        """The created org's id must be in the response.

        The frontend (org/new/+page.server.js) reads `org.id` off this
        response to set the `org` cookie and to mint an org-scoped token for
        the optional vertical-pack apply call. Before this field was added
        to OrgProfileCreateSerializer.Meta.fields, `org.id` was `undefined`
        here and every downstream use of it silently no-opped (C1).
        """
        response = admin_client.post(
            self.url,
            {"name": "Org With Id"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert "id" in response.data["org"]
        assert response.data["org"]["id"] is not None
        created = Profile.objects.get(user=admin_user, org__name="Org With Id").org
        assert str(response.data["org"]["id"]) == str(created.id)

    def test_create_org_ignores_client_supplied_id(self, admin_client, admin_user):
        """A client-supplied "id" in the request body must not be honoured.

        `id` is a UUIDField(editable=False), which DRF marks read-only
        automatically -- this pins that guarantee so adding "id" to the
        serializer's fields for C1 never regresses into a mass-assignment
        hole (client picking its own org id / colliding with another org).
        """
        spoofed_id = "11111111-1111-1111-1111-111111111111"
        response = admin_client.post(
            self.url,
            {"name": "Org Ignoring Spoofed Id", "id": spoofed_id},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["org"]["id"] != spoofed_id
        created = Profile.objects.get(
            user=admin_user, org__name="Org Ignoring Spoofed Id"
        ).org
        assert str(created.id) != spoofed_id
        assert str(response.data["org"]["id"]) == str(created.id)

    def test_create_org_special_chars(self, admin_client):
        """Organization name with special characters should fail."""
        response = admin_client.post(
            self.url,
            {"name": "Bad@Name!"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_org_duplicate_name(self, admin_client, org_a):
        """Creating org with duplicate name should fail."""
        response = admin_client.post(
            self.url,
            {"name": org_a.name},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_orgs(self, admin_client, admin_user, admin_profile, org_a):
        """List organizations the user belongs to."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert "profile_org_list" in response.data
        org_names = [p["org"]["name"] for p in response.data["profile_org_list"]]
        assert org_a.name in org_names

    def test_list_orgs_unauthenticated(self, unauthenticated_client):
        """Unauthenticated callers are refused.

        The endpoint answers 403, not the 401 this test used to assert, because
        no authenticator offers a ``WWW-Authenticate`` challenge. Access is
        denied either way; only the status text differs.
        """
        response = unauthenticated_client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestOrgUpdateView:
    """Tests for GET/PUT/PATCH /api/org/<pk>/"""

    def _url(self, pk):
        return f"/api/org/{pk}/"

    def test_get_org(self, admin_client, org_a):
        """Get organization details."""
        response = admin_client.get(self._url(org_a.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert "org" in response.data

    def test_update_org(self, admin_client, org_a):
        """Admin can update organization."""
        response = admin_client.put(
            self._url(org_a.id),
            {"name": "Updated Org Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        org_a.refresh_from_db()
        assert org_a.name == "Updated Org Name"

    def test_update_org_non_admin_forbidden(self, user_client, org_a, user_profile):
        """Non-admin user cannot update organization."""
        response = user_client.put(
            self._url(org_a.id),
            {"name": "Nope"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_different_org_forbidden(self, admin_client, org_b):
        """Cannot update a different organization."""
        response = admin_client.put(
            self._url(org_b.id),
            {"name": "Cross Org Update"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_org(self, admin_client, org_a):
        """Partial update of organization via PATCH."""
        response = admin_client.patch(
            self._url(org_a.id),
            {"name": "Patched Org"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_patch_org_non_admin_forbidden(self, user_client, org_a, user_profile):
        """Non-admin cannot PATCH organization."""
        response = user_client.patch(
            self._url(org_a.id),
            {"name": "No Patch"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_different_org_forbidden(self, admin_client, org_b):
        """Cannot get a different organization's details."""
        response = admin_client.get(self._url(org_b.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_org_rejects_an_overlong_name(self, admin_client, org_a):
        """`Org.name` is varchar(100) and this endpoint validated nothing.

        It assigned `request.data["name"]` straight to the column, so on
        PostgreSQL a longer name reached the driver as `DataError: value too
        long for type character varying(100)` and came back as a 500. Verified
        against the dev database before this test was written; the suite runs
        on SQLite, which stores it happily, so unfixed code answers 200 here.
        """
        response = admin_client.put(
            self._url(org_a.id),
            {"name": "X" * 200},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data["errors"]

        org_a.refresh_from_db()
        assert org_a.name != "X" * 200

    def test_patch_org_rejects_an_overlong_name(self, admin_client, org_a):
        """PATCH was a copy of PUT, so it carried the same gap."""
        response = admin_client.patch(
            self._url(org_a.id),
            {"name": "X" * 200},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data["errors"]

    def test_update_org_accepts_a_name_at_the_limit(self, admin_client, org_a):
        """The validator has to be able to say yes at exactly 100 characters."""
        name = "X" * 100
        response = admin_client.put(self._url(org_a.id), {"name": name}, format="json")
        assert response.status_code == status.HTTP_200_OK

        org_a.refresh_from_db()
        assert org_a.name == name

    def test_update_org_ignores_a_client_supplied_id(self, admin_client, org_a, org_b):
        """The id comes from the URL, which is already checked against the JWT.

        Routing the body through a serializer that names `id` is only safe
        while `id` stays read-only, so this pins it.
        """
        original = org_a.id
        response = admin_client.put(
            self._url(org_a.id),
            {"name": "Renamed", "id": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        org_a.refresh_from_db()
        assert org_a.id == original
        assert org_a.name == "Renamed"


@pytest.mark.django_db
class TestProfileView:
    """Tests for GET/PATCH /api/profile/"""

    url = "/api/profile/"

    def test_get_profile(self, admin_client, admin_profile):
        """Get current user's profile."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "user_obj" in response.data

    def test_patch_profile_phone(self, admin_client, admin_profile):
        """Update phone via PATCH."""
        response = admin_client.patch(
            self.url,
            {"phone": "+9876543210"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "user_obj" in response.data
        admin_profile.refresh_from_db()
        assert admin_profile.phone == "+9876543210"

    def test_patch_profile_name(self, admin_client, admin_profile):
        """Update name on User via PATCH /api/profile/."""
        response = admin_client.patch(
            self.url,
            {"name": "Alex Carter"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        admin_profile.user.refresh_from_db()
        assert admin_profile.user.name == "Alex Carter"

    def test_patch_profile_name_trims_whitespace(self, admin_client, admin_profile):
        """Name input is trimmed before save."""
        response = admin_client.patch(
            self.url,
            {"name": "  Alex Carter  "},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        admin_profile.user.refresh_from_db()
        assert admin_profile.user.name == "Alex Carter"

    def test_patch_profile_name_and_phone_together(self, admin_client, admin_profile):
        """Both fields update in a single PATCH."""
        response = admin_client.patch(
            self.url,
            {"name": "Alex Carter", "phone": "+1234567890"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        admin_profile.refresh_from_db()
        admin_profile.user.refresh_from_db()
        assert admin_profile.user.name == "Alex Carter"
        assert admin_profile.phone == "+1234567890"

    def test_patch_profile_rejects_invalid_phone(self, admin_client, admin_profile):
        """A phone with letters is a clean 400, not written to the column.

        The view used to write request.data straight onto the model, so any
        string landed. It now runs through flexible_phone_validator.
        """
        Profile.objects.filter(pk=admin_profile.pk).update(phone="+1234567890")
        response = admin_client.patch(
            self.url,
            {"phone": "not a phone ext 5"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "phone" in response.data
        admin_profile.refresh_from_db()
        assert admin_profile.phone == "+1234567890"  # unchanged

    def test_patch_profile_allows_blank_phone_to_clear(
        self, admin_client, admin_profile
    ):
        """A blank phone is allowed and clears the field (allow_blank path)."""
        Profile.objects.filter(pk=admin_profile.pk).update(phone="+1234567890")
        response = admin_client.patch(self.url, {"phone": ""}, format="json")
        assert response.status_code == status.HTTP_200_OK
        admin_profile.refresh_from_db()
        assert admin_profile.phone == ""

    def test_patch_profile_rejects_overlong_name(self, admin_client, admin_profile):
        """A name past the 255 max is a clean 400, not a silent truncation."""
        response = admin_client.patch(
            self.url,
            {"name": "x" * 256},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data

    def test_patch_profile_cannot_escalate_role(self, user_client, user_profile):
        """A plain USER cannot set role/admin flags on their own profile here.

        The self-edit serializer names only phone and name, so a role or
        is_organization_admin in the body is ignored (not 400'd). The request
        succeeds, the privileged fields do not move. This is the /api/profile/
        counterpart to the CreateProfileSerializer escalation that was closed on
        /api/user/<id>/; the door has to be shut on every self-edit path.
        """
        response = user_client.patch(
            self.url,
            {
                "role": "ADMIN",
                "is_organization_admin": True,
                "has_sales_access": True,
                "phone": "+1234567890",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_profile.refresh_from_db()
        assert user_profile.role == "USER"
        assert user_profile.is_organization_admin is False
        assert user_profile.has_sales_access is False
        assert user_profile.phone == "+1234567890"  # the legit field did change

    def test_get_profile_includes_teams(self, admin_client, admin_profile):
        """GET surfaces the current user's team names for the profile page."""
        team = Teams.objects.create(name="New business", org=admin_profile.org)
        team.users.add(admin_profile)
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["user_obj"]["teams"] == ["New business"]


@pytest.mark.django_db
class TestOrgSettingsView:
    """Tests for GET/PATCH /api/org/settings/"""

    url = "/api/org/settings/"

    def test_get_org_settings(self, admin_client, org_a):
        """Get organization settings."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "default_currency" in response.data
        assert "currency_symbol" in response.data

    def test_patch_org_settings(self, admin_client, org_a):
        """Admin can update organization settings."""
        response = admin_client.patch(
            self.url,
            {"default_currency": "EUR"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.default_currency == "EUR"

    def test_patch_org_settings_non_admin_forbidden(
        self, user_client, org_a, user_profile
    ):
        """Non-admin cannot update organization settings."""
        response = user_client.patch(
            self.url,
            {"default_currency": "GBP"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_patch_org_settings_company_name(self, admin_client, org_a):
        """Update company name in org settings."""
        response = admin_client.patch(
            self.url,
            {"company_name": "ACME Corp"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.company_name == "ACME Corp"

    # ── Reads: what the settings page shows, and what it must never show ──────

    def test_get_org_settings_member_can_read(self, user_client, org_a, user_profile):
        """A non-admin member can READ org settings (the page is read-only for
        them). Editing is gated; reading the company profile is not."""
        response = user_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "company_name" in response.data

    def test_get_org_settings_never_exposes_api_key(self, admin_client, org_a):
        """The org API key authenticates as the org's first admin. It must never
        appear in the settings payload -- not even for an admin, who reads/rotates
        it through the dedicated OrgApiKeyView instead."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "api_key" not in response.data
        # And the value itself is nowhere in the serialized body.
        org_a.refresh_from_db()
        assert org_a.api_key not in str(response.data)

    def test_get_org_settings_includes_behaviour_and_header_fields(
        self, admin_client, org_a
    ):
        """The two behaviour switches and the read-only header context are part
        of the payload the page renders."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        for key in (
            "csat_enabled",
            "auto_close_children_on_parent_close",
            "member_count",
            "created_at",
        ):
            assert key in response.data

    def test_get_org_settings_member_count(
        self, admin_client, org_a, admin_profile, user_profile
    ):
        """member_count reflects the active profiles in this org."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        # admin_profile + user_profile are both active in org_a.
        assert response.data["member_count"] == 2

    def test_get_org_settings_includes_vertical_and_terminology(
        self, admin_client, org_a
    ):
        """The frontend shell reads terminology/vertical off this same endpoint
        (no second fetch) -- both must be present in the payload, and reflect
        whatever the pack applier actually wrote."""
        org_a.vertical = "real-estate"
        org_a.terminology = {"lead.plural": "Enquiries"}
        org_a.save(update_fields=["vertical", "terminology"])
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["vertical"] == "real-estate"
        assert response.data["terminology"] == {"lead.plural": "Enquiries"}

    # ── Writes: the behaviour toggles that had no API write path before ──────

    def test_patch_org_settings_behaviour_toggles(self, admin_client, org_a):
        """Admin can flip csat_enabled and the cascade-close default -- the only
        write path these columns have."""
        assert org_a.csat_enabled is True
        assert org_a.auto_close_children_on_parent_close is False
        response = admin_client.patch(
            self.url,
            {
                "csat_enabled": False,
                "auto_close_children_on_parent_close": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.csat_enabled is False
        assert org_a.auto_close_children_on_parent_close is True

    def test_patch_org_settings_toggle_non_admin_forbidden(
        self, user_client, org_a, user_profile
    ):
        """A member cannot silence org-wide CSAT (or any switch)."""
        response = user_client.patch(
            self.url,
            {"csat_enabled": False},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        org_a.refresh_from_db()
        assert org_a.csat_enabled is True

    # ── Mass assignment: the allow-list holds ────────────────────────────────

    def test_patch_org_settings_cannot_change_api_key(self, admin_client, org_a):
        """api_key is not a writable field. A PATCH naming it is ignored, not
        honoured -- the credential is only rotated via OrgApiKeyView."""
        original_key = org_a.api_key
        response = admin_client.patch(
            self.url,
            {"api_key": "attacker-chosen-key", "company_name": "Legit Co"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.api_key == original_key
        # The legitimate field in the same request still applied.
        assert org_a.company_name == "Legit Co"

    def test_patch_org_settings_cannot_set_terminology_or_vertical(
        self, admin_client, org_a
    ):
        """vertical and terminology are written exclusively by the pack applier.
        A PATCH naming them -- even from an admin -- must be silently ignored,
        not honoured: this is mass assignment on fields the server derives."""
        response = admin_client.patch(
            self.url,
            {
                "vertical": "attacker-chosen-vertical",
                "terminology": {"lead.plural": "Hacked"},
                "company_name": "Legit Co",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.vertical == ""
        assert org_a.terminology == {}
        # The legitimate field in the same request still applied.
        assert org_a.company_name == "Legit Co"

    def test_patch_org_settings_cannot_deactivate_org(self, admin_client, org_a):
        """is_active (the org kill switch) is not editable through the settings
        form -- an org cannot deactivate itself here."""
        response = admin_client.patch(
            self.url,
            {"is_active": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.is_active is True

    # ── Validation: bad input is a clean 400, not a 500 ──────────────────────

    def test_patch_org_settings_invalid_currency_400(self, admin_client, org_a):
        """A currency outside the supported list is rejected with a 400."""
        response = admin_client.patch(
            self.url,
            {"default_currency": "ZZZ"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        org_a.refresh_from_db()
        assert org_a.default_currency != "ZZZ"

    def test_patch_org_settings_invalid_country_400(self, admin_client, org_a):
        """An unknown country code is rejected with a 400."""
        response = admin_client.patch(
            self.url,
            {"country": "QQ"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_org_settings_invalid_email_400(self, admin_client, org_a):
        """A malformed email is rejected with a 400, not stored raw."""
        response = admin_client.patch(
            self.url,
            {"email": "not-an-email"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # ── Tenant isolation: the endpoint edits the CALLER's org, never another ─

    def test_patch_org_settings_edits_only_callers_org(
        self, org_b_client, org_a, org_b, admin_profile
    ):
        """org/settings/ takes the org from request.profile, never a client id, so
        an admin of org B editing settings can only ever touch org B."""
        org_a_name_before = org_a.company_name
        response = org_b_client.patch(
            self.url,
            {"company_name": "Org B Trading Ltd"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        org_b.refresh_from_db()
        assert org_b.company_name == "Org B Trading Ltd"
        assert org_a.company_name == org_a_name_before

    def test_get_org_settings_unauthenticated_rejected(self, unauthenticated_client):
        """No token, no settings. (Asserts rejection without pinning the exact
        401-vs-403 code, which is inconsistent across this app's endpoints.)"""
        response = unauthenticated_client.get(self.url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


@pytest.mark.django_db
class TestOrgTimezone:
    """An org carries the timezone its days are counted in.

    Optional at creation on purpose: a mobile build installed before this field
    existed still has to be able to create an org, and there is no way to patch
    a shipped app from the server. Omitted means UTC.
    """

    create_url = "/api/org/"
    settings_url = "/api/org/settings/"

    def test_creating_an_org_with_a_timezone_keeps_it(self, admin_client):
        response = admin_client.post(
            self.create_url,
            {"name": "Berlin Org", "timezone": "Europe/Berlin"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Org.objects.get(name="Berlin Org").timezone == "Europe/Berlin"

    def test_creating_an_org_without_one_gets_utc(self, admin_client):
        """The compatibility case: an older client sends only a name."""
        response = admin_client.post(
            self.create_url, {"name": "Quiet Org"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Org.objects.get(name="Quiet Org").timezone == "UTC"

    def test_a_timezone_that_is_not_a_zone_is_refused(self, admin_client):
        """A clean 400 rather than a 500 from the database or a silent accept
        that would make every date for that org fall back forever."""
        response = admin_client.post(
            self.create_url,
            {"name": "Nowhere Org", "timezone": "Mars/Olympus_Mons"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Org.objects.filter(name="Nowhere Org").exists()

    def test_an_offset_is_not_a_timezone(self, admin_client):
        """`+05:30` names an instant's offset, not a rule that knows when the
        clocks move, so it must not be storable."""
        response = admin_client.post(
            self.create_url,
            {"name": "Offset Org", "timezone": "+05:30"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_admin_can_change_it_afterwards(self, admin_client, org_a):
        response = admin_client.patch(
            self.settings_url, {"timezone": "America/New_York"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        org_a.refresh_from_db()
        assert org_a.timezone == "America/New_York"

    def test_a_member_cannot(self, user_client, org_a):
        """The other half of the pair. Without it, a check that refuses
        everyone would pass the admin test's inverse by accident."""
        before = org_a.timezone

        response = user_client.patch(
            self.settings_url, {"timezone": "America/New_York"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        org_a.refresh_from_db()
        assert org_a.timezone == before

    def test_sign_in_tells_the_client_which_zone_the_org_uses(
        self, admin_client, org_a
    ):
        """Mobile shows the org's timezone in settings, so it has to arrive
        with the org rather than being guessed from the device."""
        org_a.timezone = "Asia/Tokyo"
        org_a.save(update_fields=["timezone"])

        response = admin_client.get("/api/org/settings/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["timezone"] == "Asia/Tokyo"


@pytest.mark.django_db
class TestTimezoneList:
    """One vocabulary for both clients, reachable before you have an org."""

    url = "/api/org/timezones/"

    def _names(self, client):
        return [z["name"] for z in client.get(self.url).data["timezones"]]

    def test_it_lists_zones(self, admin_client):
        response = admin_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        names = [z["name"] for z in response.data["timezones"]]
        assert "UTC" in names, "the field default has to be selectable"
        assert "America/New_York" in names
        assert names == sorted(names), "callers render it without re-sorting"

    def test_it_carries_both_spellings_a_client_might_hold(self, admin_client):
        """A browser reports `Asia/Calcutta`; this app's own migration wrote
        `Asia/Kolkata`. A list missing either one gives a select with no
        matching option, which submits its first entry instead."""
        names = self._names(admin_client)

        assert "Asia/Kolkata" in names
        assert "Asia/Calcutta" in names

    def test_it_drops_the_aliases_that_only_add_noise(self, admin_client):
        names = self._names(admin_client)

        assert "US/Eastern" not in names
        assert "Etc/GMT+5" not in names
        assert "EST" not in names

    def test_every_zone_carries_its_current_offset(self, admin_client):
        """Mobile has no way to read the device's IANA zone name without a
        platform package, so it preselects by matching the device's offset."""
        zones = {
            z["name"]: z["offset_minutes"]
            for z in admin_client.get(self.url).data["timezones"]
        }

        assert zones["UTC"] == 0
        assert zones["Asia/Kolkata"] == 330
        # New York is either -240 (daylight) or -300 (standard), never fixed.
        assert zones["America/New_York"] in (-240, -300)

    def test_it_needs_a_signed_in_user(self, unauthenticated_client):
        response = unauthenticated_client.get(self.url)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
