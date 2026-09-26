"""The tags API answers 400, never 500, when a text field is not text.

``name`` went straight to ``.strip()``, so a JSON number, list, object or null
raised ``AttributeError``. ``description`` is a NOT NULL text column, so a null
raised ``IntegrityError``, and anything else was stored as its ``str()``.
``color`` on the reactivate path was written unchecked, bypassing the palette
the other two paths enforce.
"""

import pytest
from rest_framework import status

from common.models import Tags

NON_STRINGS = [5, 1.5, True, ["VIP"], {"name": "VIP"}, None]

LIST_URL = "/api/tags/"


def _detail_url(pk):
    return f"/api/tags/{pk}/"


@pytest.fixture
def tag(org_a):
    return Tags.objects.create(name="Existing", org=org_a)


def _assert_400_on(response, field):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error"] is True
    assert field in response.data["errors"]


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("field", ["name", "color", "description"])
    @pytest.mark.parametrize("value", NON_STRINGS)
    def test_non_string_is_400(self, admin_client, org_a, field, value):
        body = {"name": "Fresh", field: value}

        response = admin_client.post(LIST_URL, body, format="json")

        _assert_400_on(response, field)
        assert not Tags.objects.filter(org=org_a).exists()

    @pytest.mark.parametrize("field", ["color", "description"])
    @pytest.mark.parametrize("value", NON_STRINGS)
    def test_non_string_on_reactivate_is_400(self, admin_client, org_a, field, value):
        archived = Tags.objects.create(
            name="Dormant", org=org_a, color="red", is_active=False
        )

        response = admin_client.post(
            LIST_URL, {"name": "Dormant", field: value}, format="json"
        )

        _assert_400_on(response, field)
        archived.refresh_from_db()
        assert archived.is_active is False
        assert archived.color == "red"

    def test_reactivate_ignores_a_colour_outside_the_palette(self, admin_client, org_a):
        """As on rename, an unknown colour keeps what is there instead of being
        stored past the column's choices."""
        archived = Tags.objects.create(
            name="Dormant", org=org_a, color="red", is_active=False
        )

        response = admin_client.post(
            LIST_URL, {"name": "Dormant", "color": "x" * 40}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        archived.refresh_from_db()
        assert archived.is_active is True
        assert archived.color == "red"

    def test_valid_strings_still_create(self, admin_client, org_a):
        response = admin_client.post(
            LIST_URL,
            {"name": "  Fresh  ", "color": "teal", "description": "Hot"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        created = response.data["tag"]
        assert (created["name"], created["color"], created["description"]) == (
            "Fresh",
            "teal",
            "Hot",
        )


@pytest.mark.django_db
class TestRename:
    @pytest.mark.parametrize("field", ["name", "color", "description"])
    @pytest.mark.parametrize("value", NON_STRINGS)
    def test_non_string_is_400(self, admin_client, tag, field, value):
        body = {"name": "Renamed", field: value}

        response = admin_client.put(_detail_url(tag.pk), body, format="json")

        _assert_400_on(response, field)
        tag.refresh_from_db()
        assert tag.name == "Existing"

    def test_valid_strings_still_rename(self, admin_client, tag):
        response = admin_client.put(
            _detail_url(tag.pk),
            {"name": "Renamed", "color": "teal", "description": "Hot"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        tag.refresh_from_db()
        assert (tag.name, tag.color, tag.description) == ("Renamed", "teal", "Hot")
