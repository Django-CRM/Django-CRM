"""Account create attaches tags by id, like every other account write path.

Create used to hand ``tags`` to ``get_or_create_tags``, which takes tag NAMES.
Every client sends ids (mobile's ``Account.toPayload`` does, and so do PUT and
PATCH on the same endpoint), so each id was stored as a brand-new tag named
after the UUID and the tag the caller chose was never attached.
"""

import uuid

import pytest

from accounts.models import Account
from common.models import Tags


@pytest.mark.django_db
class TestAccountCreateTagIds:
    url = "/api/accounts/"

    def test_existing_tag_id_attaches_that_tag_and_creates_none(
        self, admin_client, org_a
    ):
        tag = Tags.objects.create(name="VIP", org=org_a)
        before = Tags.objects.count()

        response = admin_client.post(
            self.url, {"name": "Tagged", "tags": [str(tag.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        account = Account.objects.get(name="Tagged", org=org_a)
        assert list(account.tags.all()) == [tag]
        assert Tags.objects.count() == before

    def test_json_encoded_id_list_attaches_the_tag(self, admin_client, org_a):
        """Multipart bodies carry the list as JSON text."""
        tag = Tags.objects.create(name="Partner", org=org_a)

        response = admin_client.post(
            self.url, {"name": "Multipart", "tags": f'["{tag.id}"]'}
        )

        assert response.status_code == 200, response.data
        account = Account.objects.get(name="Multipart", org=org_a)
        assert list(account.tags.all()) == [tag]

    def test_another_orgs_tag_id_attaches_nothing(self, admin_client, org_a, org_b):
        foreign = Tags.objects.create(name="Theirs", org=org_b)
        before = Tags.objects.count()

        response = admin_client.post(
            self.url, {"name": "Mine", "tags": [str(foreign.id)]}, format="json"
        )

        assert response.status_code == 200, response.data
        account = Account.objects.get(name="Mine", org=org_a)
        assert account.tags.count() == 0
        assert Tags.objects.count() == before

    def test_unknown_id_attaches_nothing(self, admin_client, org_a):
        before = Tags.objects.count()

        response = admin_client.post(
            self.url, {"name": "Ghost", "tags": [str(uuid.uuid4())]}, format="json"
        )

        assert response.status_code == 200, response.data
        assert Account.objects.get(name="Ghost", org=org_a).tags.count() == 0
        assert Tags.objects.count() == before

    @pytest.mark.parametrize(
        "tags", [["not-a-uuid"], "not-a-uuid", "[not json", {"id": "x"}, [5]]
    )
    def test_malformed_tags_are_400_and_create_nothing(self, admin_client, org_a, tags):
        """Same answer as leads create: a 400 naming ``tags``, never a 500.

        Parsed before the account is saved, so the 400 leaves no half-created
        account behind.
        """
        before = Tags.objects.count()

        response = admin_client.post(
            self.url, {"name": "Malformed", "tags": tags}, format="json"
        )

        assert response.status_code == 400
        assert "tags" in response.data
        assert not Account.objects.filter(name="Malformed").exists()
        assert Tags.objects.count() == before

    @pytest.mark.parametrize("field", ["contacts", "teams", "assigned_to"])
    def test_malformed_ids_in_other_relations_are_400(self, admin_client, org_a, field):
        """``handle_m2m_assignment`` handed raw text to an ``id__in`` lookup.

        It now parses through ``payload_id_list``, so a malformed id names its
        field in a 400 instead of raising ``ValidationError`` as a 500.
        """
        response = admin_client.post(
            self.url, {"name": f"Bad {field}", field: ["not-a-uuid"]}, format="json"
        )

        assert response.status_code == 400
        assert field in response.data


@pytest.mark.django_db
class TestAccountUpdateMalformedTags:
    """PUT and PATCH took the same raw ``json.loads`` path and 500'd."""

    def _account(self, org):
        return Account.objects.create(name="Existing", org=org)

    @pytest.mark.parametrize("method", ["put", "patch"])
    @pytest.mark.parametrize("tags", [["not-a-uuid"], "[not json"])
    def test_malformed_tags_are_400(self, admin_client, org_a, method, tags):
        account = self._account(org_a)

        response = getattr(admin_client, method)(
            f"/api/accounts/{account.id}/",
            {"name": "Existing", "tags": tags},
            format="json",
        )

        assert response.status_code == 400
        assert "tags" in response.data

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_valid_id_still_attaches(self, admin_client, org_a, org_b, method):
        account = self._account(org_a)
        mine = Tags.objects.create(name="Mine", org=org_a)
        theirs = Tags.objects.create(name="Theirs", org=org_b)

        response = getattr(admin_client, method)(
            f"/api/accounts/{account.id}/",
            {"name": "Existing", "tags": [str(mine.id), str(theirs.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(account.tags.all()) == [mine]
