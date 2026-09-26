"""The admin switch for the public help center: who may flip it, and what an
address may be.

Turning it on publishes the org's articles to anyone, so the write is admin
only and every rule on the slug is pinned here, allowed and refused.
"""

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings

from common.models import Org, Profile, User
from common.serializer import HelpCenterSettingsSerializer
from common.testing import _make_authenticated_client

URL = "/api/org/help-center/"


@pytest.fixture
def superuser_client(org_a):
    """A platform superuser who is only a plain member of this org."""
    user = User.objects.create_user(email="root@test.com", password="testpass123")
    user.is_superuser = True
    user.save()
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    return _make_authenticated_client(user, org_a, profile)


def _reload(org):
    org.refresh_from_db()
    return org


class TestRead:
    def test_member_reads_the_settings_but_may_not_edit(self, user_client, org_a):
        response = user_client.get(URL)
        assert response.status_code == 200
        assert response.json() == {
            "help_center_enabled": False,
            "help_center_slug": None,
            "public_url": None,
            "can_edit": False,
        }

    def test_admin_is_told_they_may_edit(self, admin_client):
        assert admin_client.get(URL).json()["can_edit"] is True

    def test_superuser_is_told_they_may_edit(self, superuser_client):
        assert superuser_client.get(URL).json()["can_edit"] is True

    def test_anonymous_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(URL).status_code in (401, 403)

    @override_settings(FRONTEND_URL="https://app.example.com/")
    def test_public_url_is_built_from_frontend_url(self, admin_client, org_a):
        org_a.help_center_slug = "acme"
        org_a.save()
        assert admin_client.get(URL).json()["public_url"] == (
            "https://app.example.com/help-center/acme"
        )


class TestWritePermission:
    def test_member_patch_is_refused_and_changes_nothing(self, user_client, org_a):
        response = user_client.patch(
            URL,
            {"help_center_enabled": True, "help_center_slug": "acme"},
            format="json",
        )
        assert response.status_code == 403
        org = _reload(org_a)
        assert org.help_center_enabled is False
        assert org.help_center_slug is None

    def test_admin_patch_is_allowed(self, admin_client, org_a):
        response = admin_client.patch(
            URL,
            {"help_center_enabled": True, "help_center_slug": "acme"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["help_center_enabled"] is True
        org = _reload(org_a)
        assert (org.help_center_enabled, org.help_center_slug) == (True, "acme")

    def test_superuser_patch_is_allowed(self, superuser_client, org_a):
        response = superuser_client.patch(
            URL, {"help_center_slug": "acme"}, format="json"
        )
        assert response.status_code == 200
        assert _reload(org_a).help_center_slug == "acme"

    def test_only_the_callers_own_org_is_written(self, admin_client, org_a, org_b):
        """No field in the body can point the write at another org."""
        response = admin_client.patch(
            URL,
            {
                "help_center_slug": "acme",
                "id": str(org_b.id),
                "org": str(org_b.id),
                "name": "Renamed",
                "is_active": False,
            },
            format="json",
        )
        assert response.status_code == 200
        assert _reload(org_a).help_center_slug == "acme"
        assert _reload(org_a).name == "Test Organization A"
        assert _reload(org_a).is_active is True
        assert _reload(org_b).help_center_slug is None


class TestSlugFormat:
    @pytest.mark.parametrize(
        "slug",
        [
            "ab",
            "a" * 51,
            "-acme",
            "acme-",
            "ac--me",
            "ac_me",
            "ac me",
            "acmé",
            "acme/../x",
        ],
    )
    def test_malformed_slug_is_refused(self, admin_client, org_a, slug):
        response = admin_client.patch(URL, {"help_center_slug": slug}, format="json")
        assert response.status_code == 400
        assert "help_center_slug" in response.json()
        assert _reload(org_a).help_center_slug is None

    @pytest.mark.parametrize("slug", ["admin", "api", "help", "bottlecrm", "Support"])
    def test_reserved_slug_is_refused_in_any_case(self, admin_client, org_a, slug):
        response = admin_client.patch(URL, {"help_center_slug": slug}, format="json")
        assert response.status_code == 400
        assert "reserved" in response.json()["help_center_slug"][0]

    @pytest.mark.parametrize("slug", ["abc", "a" * 50, "acme-help-2", "42go"])
    def test_well_formed_slug_is_accepted(self, admin_client, org_a, slug):
        response = admin_client.patch(URL, {"help_center_slug": slug}, format="json")
        assert response.status_code == 200
        assert _reload(org_a).help_center_slug == slug

    def test_slug_is_trimmed_and_lowercased(self, admin_client, org_a):
        response = admin_client.patch(
            URL, {"help_center_slug": "  Acme-Help "}, format="json"
        )
        assert response.status_code == 200
        assert _reload(org_a).help_center_slug == "acme-help"

    def test_blank_slug_is_stored_as_null_so_orgs_do_not_collide(
        self, admin_client, org_a, org_b
    ):
        org_b.help_center_slug = None
        org_b.save()
        response = admin_client.patch(URL, {"help_center_slug": ""}, format="json")
        assert response.status_code == 200
        assert _reload(org_a).help_center_slug is None


class TestSlugUniqueness:
    def test_duplicate_in_another_case_is_refused(self, admin_client, org_a, org_b):
        org_b.help_center_slug = "acme"
        org_b.save()
        response = admin_client.patch(URL, {"help_center_slug": "ACME"}, format="json")
        assert response.status_code == 400
        assert response.json()["help_center_slug"] == [
            "That address is already taken. Choose another."
        ]
        assert _reload(org_a).help_center_slug is None

    def test_resaving_your_own_slug_is_not_a_duplicate(self, admin_client, org_a):
        org_a.help_center_slug = "acme"
        org_a.save()
        response = admin_client.patch(
            URL,
            {"help_center_enabled": True, "help_center_slug": "acme"},
            format="json",
        )
        assert response.status_code == 200

    def test_database_refuses_a_case_variant_duplicate(self, org_a, org_b):
        """The backstop behind the serializer check, for any other write path."""
        org_a.help_center_slug = "acme"
        org_a.save()
        org_b.help_center_slug = "Acme"
        with pytest.raises(IntegrityError), transaction.atomic():
            org_b.save()

    def test_a_race_past_the_check_is_a_400_not_a_500(
        self, admin_client, org_a, org_b, monkeypatch
    ):
        """Two admins claiming one address at once both pass the serializer
        check. Simulated by skipping the check; the constraint answers."""
        org_b.help_center_slug = "acme"
        org_b.save()
        monkeypatch.setattr(
            HelpCenterSettingsSerializer,
            "validate_help_center_slug",
            lambda self, value: value,
        )
        response = admin_client.patch(URL, {"help_center_slug": "acme"}, format="json")
        assert response.status_code == 400
        assert response.json() == {
            "help_center_slug": ["That address is already taken. Choose another."]
        }


class TestEnableNeedsSlug:
    def test_enabling_without_a_slug_is_refused(self, admin_client, org_a):
        response = admin_client.patch(URL, {"help_center_enabled": True}, format="json")
        assert response.status_code == 400
        assert "help_center_slug" in response.json()
        assert _reload(org_a).help_center_enabled is False

    def test_enabling_with_a_slug_in_the_same_request_is_allowed(
        self, admin_client, org_a
    ):
        response = admin_client.patch(
            URL,
            {"help_center_enabled": True, "help_center_slug": "acme"},
            format="json",
        )
        assert response.status_code == 200

    def test_clearing_the_slug_while_enabled_is_refused(self, admin_client, org_a):
        org_a.help_center_slug = "acme"
        org_a.help_center_enabled = True
        org_a.save()
        response = admin_client.patch(URL, {"help_center_slug": ""}, format="json")
        assert response.status_code == 400
        assert _reload(org_a).help_center_slug == "acme"

    def test_disabling_keeps_the_slug(self, admin_client, org_a):
        org_a.help_center_slug = "acme"
        org_a.help_center_enabled = True
        org_a.save()
        response = admin_client.patch(
            URL, {"help_center_enabled": False}, format="json"
        )
        assert response.status_code == 200
        org = _reload(org_a)
        assert (org.help_center_enabled, org.help_center_slug) == (False, "acme")

    def test_database_refuses_enabled_without_slug(self, org_a):
        org_a.help_center_enabled = True
        with pytest.raises(IntegrityError), transaction.atomic():
            org_a.save()

    def test_orgs_default_to_off(self):
        org = Org.objects.create(name="Fresh")
        assert (org.help_center_enabled, org.help_center_slug) == (False, None)
