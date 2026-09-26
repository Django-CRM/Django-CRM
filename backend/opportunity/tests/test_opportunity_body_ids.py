"""Id lists in a deal write body: malformed is a 400 and writes nothing.

Every relation was parsed after the deal had been saved, so a malformed id
answered 400 with the write already done. All of them now parse through
``payload_id_list`` before the first write.
"""

import pytest

from common.models import Tags, Teams
from contacts.models import Contact
from opportunity.models import Opportunity

URL = "/api/opportunities/"
CREATE = {"name": "New deal", "stage": "QUALIFICATION"}
UPDATE = {"name": "After", "stage": "QUALIFICATION"}
FIELDS = ["contacts", "tags", "teams", "assigned_to"]
MALFORMED = ["not-json", "[not json", ["nope"], {"a": 1}, [1]]


@pytest.fixture
def pairs(org_a, org_b, admin_profile, profile_b):
    """Per field: one object in the caller's org, one in another org."""
    return {
        "contacts": (
            Contact.objects.create(first_name="Ours", last_name="C", org=org_a),
            Contact.objects.create(first_name="Theirs", last_name="C", org=org_b),
        ),
        "tags": (
            Tags.objects.create(name="Ours", org=org_a),
            Tags.objects.create(name="Theirs", org=org_b),
        ),
        "teams": (
            Teams.objects.create(name="Ours", description="-", org=org_a),
            Teams.objects.create(name="Theirs", description="-", org=org_b),
        ),
        "assigned_to": (admin_profile, profile_b),
    }


@pytest.fixture
def deal(org_a, admin_user, pairs):
    """An existing deal already linked to our object in every relation."""
    deal = Opportunity.objects.create(
        name="Before", stage="QUALIFICATION", org=org_a, created_by=admin_user
    )
    for field, (ours, _) in pairs.items():
        getattr(deal, field).add(ours)
    return deal


@pytest.mark.django_db
class TestOpportunityBodyIds:
    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    def test_create_malformed_is_400_and_creates_nothing(
        self, admin_client, field, value
    ):
        before = Opportunity.objects.count()

        response = admin_client.post(URL, {**CREATE, field: value}, format="json")

        assert response.status_code == 400
        assert field in response.data
        assert Opportunity.objects.count() == before

    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_malformed_is_400_and_changes_nothing(
        self, admin_client, deal, pairs, method, field, value
    ):
        response = getattr(admin_client, method)(
            f"{URL}{deal.id}/", {**UPDATE, field: value}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data
        deal.refresh_from_db()
        assert deal.name == "Before"
        for name, (ours, _) in pairs.items():
            assert list(getattr(deal, name).all()) == [ours], name

    @pytest.mark.parametrize("field", FIELDS)
    def test_create_attaches_only_the_callers_org(self, admin_client, pairs, field):
        ours, theirs = pairs[field]

        response = admin_client.post(
            URL, {**CREATE, field: [str(ours.id), str(theirs.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        created = Opportunity.objects.get(name=CREATE["name"])
        assert list(getattr(created, field).all()) == [ours]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_only_the_callers_org(
        self, admin_client, deal, pairs, method, field
    ):
        ours, theirs = pairs[field]
        getattr(deal, field).clear()

        response = getattr(admin_client, method)(
            f"{URL}{deal.id}/",
            {**UPDATE, field: [str(ours.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(getattr(deal, field).all()) == [ours]
