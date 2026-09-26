"""Id lists in a case write body: malformed is a 400 and writes nothing.

``tags`` went through a raw ``json.loads`` and straight into an id lookup on
create and PUT, so ``"not-json"`` or ``["nope"]`` answered 500. Every relation
was also parsed after the case had been saved, so even the 400s arrived with
the write already done. All of them now parse through ``payload_id_list``
before the first write.
"""

import pytest

from cases.models import Case
from common.models import Tags, Teams
from contacts.models import Contact

URL = "/api/cases/"
CREATE = {"name": "New case", "status": "New", "priority": "Normal"}
UPDATE = {"name": "After", "status": "New", "priority": "Normal"}
FIELDS = ["contacts", "teams", "assigned_to", "tags"]
MALFORMED = ["not-json", "[not json", ["nope"], {"a": 1}, [1]]


@pytest.fixture
def pairs(org_a, org_b, admin_profile, profile_b):
    """Per field: one object in the caller's org, one in another org."""
    return {
        "contacts": (
            Contact.objects.create(first_name="Ours", last_name="C", org=org_a),
            Contact.objects.create(first_name="Theirs", last_name="C", org=org_b),
        ),
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
def case(org_a, admin_user, pairs):
    """An existing case already linked to our object in every relation."""
    case = Case.objects.create(
        name="Before",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )
    for field, (ours, _) in pairs.items():
        getattr(case, field).add(ours)
    return case


@pytest.mark.django_db
class TestCaseBodyIds:
    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    def test_create_malformed_is_400_and_creates_nothing(
        self, admin_client, field, value
    ):
        before = Case.objects.count()

        response = admin_client.post(URL, {**CREATE, field: value}, format="json")

        assert response.status_code == 400
        assert field in response.data
        assert Case.objects.count() == before

    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_malformed_is_400_and_changes_nothing(
        self, admin_client, case, pairs, method, field, value
    ):
        response = getattr(admin_client, method)(
            f"{URL}{case.id}/", {**UPDATE, field: value}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data
        case.refresh_from_db()
        assert case.name == "Before"
        for name, (ours, _) in pairs.items():
            assert list(getattr(case, name).all()) == [ours], name

    @pytest.mark.parametrize("field", FIELDS)
    def test_create_attaches_only_the_callers_org(self, admin_client, pairs, field):
        ours, theirs = pairs[field]

        response = admin_client.post(
            URL, {**CREATE, field: [str(ours.id), str(theirs.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        created = Case.objects.get(name=CREATE["name"])
        assert list(getattr(created, field).all()) == [ours]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_only_the_callers_org(
        self, admin_client, case, pairs, method, field
    ):
        ours, theirs = pairs[field]
        getattr(case, field).clear()

        response = getattr(admin_client, method)(
            f"{URL}{case.id}/",
            {**UPDATE, field: [str(ours.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(getattr(case, field).all()) == [ours]
