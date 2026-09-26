"""Id lists in a lead write body: malformed is a 400 and writes nothing.

Every relation was parsed after the lead had been saved, so a malformed id
answered 400 with the write already done. PUT also ran ``contacts`` and
``assigned_to`` through a raw ``json.loads``, so text that was not JSON
answered 500. All of them now parse through ``payload_id_list`` before the
first write.
"""

import pytest

from common.models import Tags, Teams
from contacts.models import Contact
from leads.models import Lead

URL = "/api/leads/"
CREATE = {"first_name": "New", "last_name": "Lead", "email": "new@example.com"}
UPDATE = {"first_name": "After", "last_name": "Lead", "email": "was@example.com"}
FIELDS = ["tags", "contacts", "teams", "assigned_to"]
MALFORMED = ["not-json", "[not json", ["nope"], {"a": 1}, [1]]


@pytest.fixture
def pairs(org_a, org_b, admin_profile, profile_b):
    """Per field: one object in the caller's org, one in another org."""
    return {
        "tags": (
            Tags.objects.create(name="Ours", org=org_a),
            Tags.objects.create(name="Theirs", org=org_b),
        ),
        "contacts": (
            Contact.objects.create(first_name="Ours", last_name="C", org=org_a),
            Contact.objects.create(first_name="Theirs", last_name="C", org=org_b),
        ),
        "teams": (
            Teams.objects.create(name="Ours", description="-", org=org_a),
            Teams.objects.create(name="Theirs", description="-", org=org_b),
        ),
        "assigned_to": (admin_profile, profile_b),
    }


@pytest.fixture
def lead(org_a, admin_user, pairs):
    """An existing lead already linked to our object in every relation."""
    lead = Lead.objects.create(
        first_name="Before",
        last_name="Lead",
        email="was@example.com",
        org=org_a,
        created_by=admin_user,
    )
    for field, (ours, _) in pairs.items():
        getattr(lead, field).add(ours)
    return lead


@pytest.mark.django_db
class TestLeadBodyIds:
    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    def test_create_malformed_is_400_and_creates_nothing(
        self, admin_client, field, value
    ):
        before = Lead.objects.count()

        response = admin_client.post(URL, {**CREATE, field: value}, format="json")

        assert response.status_code == 400
        assert field in response.data
        assert Lead.objects.count() == before

    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_malformed_is_400_and_changes_nothing(
        self, admin_client, lead, pairs, method, field, value
    ):
        response = getattr(admin_client, method)(
            f"{URL}{lead.id}/", {**UPDATE, field: value}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data
        lead.refresh_from_db()
        assert lead.first_name == "Before"
        for name, (ours, _) in pairs.items():
            assert list(getattr(lead, name).all()) == [ours], name

    @pytest.mark.parametrize("field", FIELDS)
    def test_create_attaches_only_the_callers_org(self, admin_client, pairs, field):
        ours, theirs = pairs[field]

        response = admin_client.post(
            URL, {**CREATE, field: [str(ours.id), str(theirs.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        created = Lead.objects.get(email=CREATE["email"])
        assert list(getattr(created, field).all()) == [ours]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_only_the_callers_org(
        self, admin_client, lead, pairs, method, field
    ):
        ours, theirs = pairs[field]
        getattr(lead, field).clear()

        response = getattr(admin_client, method)(
            f"{URL}{lead.id}/",
            {**UPDATE, field: [str(ours.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(getattr(lead, field).all()) == [ours]
