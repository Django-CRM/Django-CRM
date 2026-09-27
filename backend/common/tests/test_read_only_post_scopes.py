"""The duplicate search is a read, although it is a POST.

`POST /api/{leads,contacts,accounts}/duplicates/` writes nothing; it takes its
criteria in a body only to keep them out of access logs. A `leads:read` token
used to be refused there because the scope action was decided by method alone.
It is now decided by method and path together, against an exact set of paths,
so nothing near those paths turns into a read by accident.
"""

import uuid

import pytest
from django.core.cache import cache

from common import scopes
from common.tests.test_org_api_key_scope import _key_client
from common.tests.test_pat_scope_enforcement import _pat_client
from leads.models import Lead

SEARCHES = (
    "/api/leads/duplicates/",
    "/api/contacts/duplicates/",
    "/api/accounts/duplicates/",
)


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


class TestMatcher:
    @pytest.mark.parametrize("path", SEARCHES)
    def test_a_post_to_a_search_is_a_read(self, path):
        assert scopes.action_for("POST", path) == "read"
        assert scopes.action_for("post", path) == "read"

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_other_writes_to_a_search_path_stay_writes(self, method):
        assert scopes.action_for(method, "/api/leads/duplicates/") == "write"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/leads/duplicates",  # missing slash
            "/api/leads/duplicates/extra/",  # extra segment
            "/api/leads/duplicates//",
            f"/api/leads/{uuid.uuid4()}/duplicates/",  # a record's own list
            f"/api/leads/{uuid.uuid4()}/merge/",
            "/api/Leads/duplicates/",
            "/api/leads/duplicates/../",
            "/api/tasks/duplicates/",
            "/api/leads/",
            "/leads/duplicates/",
        ],
    )
    def test_lookalike_paths_are_writes(self, path):
        assert scopes.action_for("POST", path) == "write"

    def test_the_denial_names_the_scope_it_needs(self):
        denial = scopes.check_request(
            ["contacts:read"], "POST", "/api/leads/duplicates/"
        )
        assert denial == (
            "This token is not scoped for read access to leads. "
            "It needs the leads:read scope."
        )


class TestOverHttp:
    def test_a_leads_read_token_may_search_leads(self, admin_profile, org_a):
        Lead.objects.create(
            org=org_a, title="T", first_name="Ada", last_name="L", email="a@x.io"
        )
        client = _pat_client(admin_profile, scopes=["leads:read"])
        response = client.post(
            "/api/leads/duplicates/", {"email": "a@x.io"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert len(response.json()["duplicates"]) == 1

    def test_the_same_token_still_cannot_create_or_merge(self, admin_profile, org_a):
        keep = Lead.objects.create(org=org_a, title="K", first_name="K", last_name="K")
        lose = Lead.objects.create(org=org_a, title="L", first_name="L", last_name="L")
        client = _pat_client(admin_profile, scopes=["leads:read"])

        create = client.post(
            "/api/leads/", {"title": "N", "first_name": "N", "last_name": "N"}
        )
        assert create.status_code == 403
        assert "leads:write" in create.json()["detail"]

        merge = client.post(
            f"/api/leads/{keep.id}/merge/", {"merge_id": str(lose.id)}, format="json"
        )
        assert merge.status_code == 403
        assert Lead.objects.filter(id=lose.id).exists()

    def test_a_contacts_read_token_is_refused_on_the_leads_search(self, admin_profile):
        client = _pat_client(admin_profile, scopes=["contacts:read"])
        response = client.post(
            "/api/leads/duplicates/", {"email": "a@x.io"}, format="json"
        )
        assert response.status_code == 403
        assert "leads:read" in response.json()["detail"]
        # and it may search its own resource
        ok = client.post(
            "/api/contacts/duplicates/", {"email": "a@x.io"}, format="json"
        )
        assert ok.status_code == 200

    def test_a_read_token_is_refused_on_a_lookalike(self, admin_profile):
        client = _pat_client(admin_profile, scopes=["leads:read"])
        response = client.post(
            "/api/leads/duplicates/extra/", {"email": "a@x.io"}, format="json"
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("path", SEARCHES)
    def test_the_org_api_key_may_search(self, org_a, admin_profile, path):
        response = _key_client(org_a).post(path, {"email": "a@x.io"}, format="json")
        assert response.status_code == 200, response.content

    def test_the_org_api_key_still_cannot_create(self, org_a, admin_profile):
        before = Lead.objects.count()
        response = _key_client(org_a).post(
            "/api/leads/", {"title": "N", "first_name": "N", "last_name": "N"}
        )
        assert response.status_code in (401, 403)
        assert Lead.objects.count() == before
