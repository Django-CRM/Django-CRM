"""Id lists in a contact write body: malformed is a 400 and writes nothing.

``tags`` went through a raw ``json.loads`` and straight into an id lookup on
create, PUT and PATCH, so ``"not-json"`` or ``["nope"]`` answered 500. Every
relation was also parsed after the contact had been saved, so even the 400s
arrived with the write already done. All of them now parse through
``payload_id_list`` before the first write.
"""

import pytest

from common.models import Tags, Teams
from contacts.models import Contact

URL = "/api/contacts/"
CREATE = {"first_name": "New", "last_name": "Contact", "email": "new@example.com"}
UPDATE = {"first_name": "After", "last_name": "Contact", "email": "was@example.com"}
FIELDS = ["teams", "assigned_to", "tags"]
MALFORMED = ["not-json", "[not json", ["nope"], {"a": 1}, [1]]


@pytest.fixture
def pairs(org_a, org_b, admin_profile, profile_b):
    """Per field: one object in the caller's org, one in another org."""
    return {
        "teams": (
            Teams.objects.create(name="Ours", description="-", org=org_a),
            Teams.objects.create(name="Theirs", description="-", org=org_b),
        ),
        "assigned_to": (admin_profile, profile_b),
        "tags": (
            Tags.objects.create(name="Ours", org=org_a),
            Tags.objects.create(name="Theirs", org=org_b),
        ),
    }


@pytest.fixture
def contact(org_a, pairs):
    """An existing contact already linked to our object in every relation."""
    contact = Contact.objects.create(
        first_name="Before", last_name="Contact", email="was@example.com", org=org_a
    )
    for field, (ours, _) in pairs.items():
        getattr(contact, field).add(ours)
    return contact


@pytest.mark.django_db
class TestContactBodyIds:
    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    def test_create_malformed_is_400_and_creates_nothing(
        self, admin_client, field, value
    ):
        before = Contact.objects.count()

        response = admin_client.post(URL, {**CREATE, field: value}, format="json")

        assert response.status_code == 400
        assert field in response.data
        assert Contact.objects.count() == before

    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_malformed_is_400_and_changes_nothing(
        self, admin_client, contact, pairs, method, field, value
    ):
        response = getattr(admin_client, method)(
            f"{URL}{contact.id}/", {**UPDATE, field: value}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data
        contact.refresh_from_db()
        assert contact.first_name == "Before"
        for name, (ours, _) in pairs.items():
            assert list(getattr(contact, name).all()) == [ours], name

    @pytest.mark.parametrize("field", FIELDS)
    def test_create_attaches_only_the_callers_org(self, admin_client, pairs, field):
        ours, theirs = pairs[field]

        response = admin_client.post(
            URL, {**CREATE, field: [str(ours.id), str(theirs.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        created = Contact.objects.get(email=CREATE["email"])
        assert list(getattr(created, field).all()) == [ours]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_only_the_callers_org(
        self, admin_client, contact, pairs, method, field
    ):
        ours, theirs = pairs[field]
        getattr(contact, field).clear()

        response = getattr(admin_client, method)(
            f"{URL}{contact.id}/",
            {**UPDATE, field: [str(ours.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(getattr(contact, field).all()) == [ours]
