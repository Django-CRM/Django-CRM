"""
Tests for API settings (domain) views: DomainList, DomainDetailView.

Run with: pytest common/tests/test_settings.py -v
"""

import pytest
from rest_framework import status

from common.models import APISettings


@pytest.mark.django_db
class TestDomainListView:
    """Tests for GET/POST /api/api-settings/"""

    url = "/api/api-settings/"

    def test_list_api_settings(self, admin_client, org_a):
        """Get list of API settings."""
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert "api_settings" in response.data
        assert "users" in response.data

    def test_create_api_setting(self, admin_client, org_a):
        """Create a new API setting."""
        response = admin_client.post(
            self.url,
            {"title": "Test API", "website": "https://example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["error"] is False

    def test_create_api_setting_invalid_website(self, admin_client, org_a):
        """Creating API setting with invalid website should fail."""
        response = admin_client.post(
            self.url,
            {"title": "Bad API", "website": "not-a-url"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated(self, unauthenticated_client):
        """The request is refused, and refused as a response, not an exception.

        DRF catches ``PermissionDenied`` in ``handle_exception`` and renders it,
        so a test client never sees it raised. Wrapping the call in
        ``pytest.raises`` therefore failed with DID NOT RAISE no matter how the
        endpoint behaved, which meant this test could never have caught the
        access opening up.
        """
        response = unauthenticated_client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_row_carries_its_id(self, admin_client, org_a, admin_user):
        """The pk is the only way to reach the detail, update and delete routes.

        It was missing from the serializer's ``fields``, and it appears in no
        other response, so ``/api/api-settings/<pk>/`` was unreachable for
        every caller including an admin. Pinned here because nothing else
        asserts on the shape of a row.
        """
        setting = APISettings.objects.create(
            title="Listed",
            website="https://listed.com",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        rows = response.data["api_settings"]
        assert str(rows[0]["id"]) == str(setting.pk)

    def test_admin_reads_the_apikey(self, admin_client, org_a, admin_user):
        """An admin has to read the key back: it is what they paste into the
        website form that posts to ``CreateLeadFromSite``."""
        setting = APISettings.objects.create(
            title="Keyed", website="https://keyed.com", org=org_a, created_by=admin_user
        )
        response = admin_client.get(self.url)
        assert response.data["api_settings"][0]["apikey"] == setting.apikey

    def test_non_admin_does_not_read_the_apikey(self, user_client, org_a, admin_user):
        """`apikey` authenticates the anonymous ``CreateLeadFromSite`` endpoint,
        which creates Lead and Contact rows attributed to the setting's creator.
        A USER-role member has no reason to hold it."""
        APISettings.objects.create(
            title="Keyed", website="https://keyed.com", org=org_a, created_by=admin_user
        )
        response = user_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        row = response.data["api_settings"][0]
        assert "apikey" not in row
        # The rest of the row is still readable: this gates the credential, not
        # the record.
        assert row["title"] == "Keyed"

    def test_non_admin_cannot_create(self, user_client, org_a):
        """Creating an API setting mints a key that posts leads into the org."""
        response = user_client.post(
            self.url,
            {"title": "Sneaky", "website": "https://sneaky.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not APISettings.objects.filter(title="Sneaky").exists()


@pytest.mark.django_db
class TestDomainDetailView:
    """Tests for GET/PUT/PATCH/DELETE /api/api-settings/<pk>/"""

    def _url(self, pk):
        return f"/api/api-settings/{pk}/"

    def _create_setting(self, org, user):
        return APISettings.objects.create(
            title="Test Setting",
            website="https://test.com",
            org=org,
            created_by=user,
        )

    def test_get_api_setting(self, admin_client, org_a, admin_user):
        """Get a single API setting."""
        setting = self._create_setting(org_a, admin_user)
        response = admin_client.get(self._url(setting.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert "domain" in response.data

    def test_update_api_setting(self, admin_client, org_a, admin_user):
        """Update an API setting."""
        setting = self._create_setting(org_a, admin_user)
        response = admin_client.put(
            self._url(setting.pk),
            {"title": "Updated Setting", "website": "https://updated.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_delete_api_setting(self, admin_client, org_a, admin_user):
        """Delete an API setting."""
        setting = self._create_setting(org_a, admin_user)
        response = admin_client.delete(self._url(setting.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        assert not APISettings.objects.filter(pk=setting.pk).exists()

    def test_patch_api_setting(self, admin_client, org_a, admin_user):
        """Partial update via PATCH."""
        setting = self._create_setting(org_a, admin_user)
        response = admin_client.patch(
            self._url(setting.pk),
            {"title": "Patched Title"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False

    def test_get_api_setting_cross_org(
        self, org_b_client, org_a, admin_user, admin_profile
    ):
        """Cross-org access should return 404."""
        setting = self._create_setting(org_a, admin_user)
        response = org_b_client.get(self._url(setting.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_admin_reads_the_apikey_on_detail(self, admin_client, org_a, admin_user):
        setting = self._create_setting(org_a, admin_user)
        response = admin_client.get(self._url(setting.pk))
        assert response.data["domain"]["apikey"] == setting.apikey

    def test_non_admin_does_not_read_the_apikey_on_detail(
        self, user_client, org_a, admin_user
    ):
        """Detail is gated the same way as the list, or the list gate is a
        speed bump: the pk is in every list row."""
        setting = self._create_setting(org_a, admin_user)
        response = user_client.get(self._url(setting.pk))
        assert response.status_code == status.HTTP_200_OK
        assert "apikey" not in response.data["domain"]

    def test_non_admin_cannot_update(self, user_client, org_a, admin_user):
        setting = self._create_setting(org_a, admin_user)
        response = user_client.put(
            self._url(setting.pk),
            {"title": "Hijacked", "website": "https://attacker.example"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        setting.refresh_from_db()
        assert setting.title == "Test Setting"

    def test_non_admin_cannot_patch(self, user_client, org_a, admin_user):
        setting = self._create_setting(org_a, admin_user)
        response = user_client.patch(
            self._url(setting.pk), {"title": "Hijacked"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        setting.refresh_from_db()
        assert setting.title == "Test Setting"

    def test_non_admin_cannot_delete(self, user_client, org_a, admin_user):
        """Deleting the setting breaks the org's website lead capture."""
        setting = self._create_setting(org_a, admin_user)
        response = user_client.delete(self._url(setting.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert APISettings.objects.filter(pk=setting.pk).exists()
