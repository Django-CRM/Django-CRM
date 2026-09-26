"""Id lists in an account write body: malformed is a 400 and writes nothing.

Create saved the account and only then parsed ``contacts``, ``teams`` and
``assigned_to``, so a malformed id answered 400 with the account already
created. PUT saved the fields and replaced the contacts before it parsed
``tags``. ``ATOMIC_REQUESTS`` is off, so nothing rolled either back. Every id
list is now parsed before the first write.
"""

import pytest

from accounts.models import Account
from common.models import Tags, Teams
from contacts.models import Contact

URL = "/api/accounts/"
CREATE = {"name": "New account"}
UPDATE = {"name": "After"}
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
def account(org_a, pairs):
    """An existing account already linked to our object in every relation."""
    account = Account.objects.create(name="Before", org=org_a)
    for field, (ours, _) in pairs.items():
        getattr(account, field).add(ours)
    return account


@pytest.mark.django_db
class TestAccountBodyIds:
    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    def test_create_malformed_is_400_and_creates_nothing(
        self, admin_client, field, value
    ):
        before = Account.objects.count()

        response = admin_client.post(URL, {**CREATE, field: value}, format="json")

        assert response.status_code == 400
        assert field in response.data
        assert Account.objects.count() == before

    @pytest.mark.parametrize("value", MALFORMED)
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_malformed_is_400_and_changes_nothing(
        self, admin_client, account, pairs, method, field, value
    ):
        response = getattr(admin_client, method)(
            f"{URL}{account.id}/", {**UPDATE, field: value}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data
        account.refresh_from_db()
        assert account.name == "Before"
        for name, (ours, _) in pairs.items():
            assert list(getattr(account, name).all()) == [ours], name

    @pytest.mark.parametrize("field", FIELDS)
    def test_create_attaches_only_the_callers_org(self, admin_client, pairs, field):
        ours, theirs = pairs[field]

        response = admin_client.post(
            URL, {**CREATE, field: [str(ours.id), str(theirs.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        created = Account.objects.get(name=CREATE["name"])
        assert list(getattr(created, field).all()) == [ours]

    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_only_the_callers_org(
        self, admin_client, account, pairs, method, field
    ):
        ours, theirs = pairs[field]
        getattr(account, field).clear()

        response = getattr(admin_client, method)(
            f"{URL}{account.id}/",
            {**UPDATE, field: [str(ours.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(getattr(account, field).all()) == [ours]
