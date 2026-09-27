"""A macro that touches a ticket needs the ticket's scope too.

`/api/macros/<id>/apply/` changes a case and `/api/macros/<id>/render/` returns
its subject and its contact's name and email. Scoped by their first path
segment alone, a `macros:write` token could change a ticket it holds no
`cases:write` for (create a personal macro, then apply it), and a macros token
could read tickets through render. Both now also need the cases scope.
"""

import uuid

import pytest
from django.core.cache import cache

from common import scopes
from common.tests.test_org_api_key_scope import _key_client
from common.tests.test_pat_scope_enforcement import _pat_client
from macros.models import Macro


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ticket(case_factory, contact_factory):
    return case_factory(name="Printer", contact=contact_factory())


@pytest.fixture
def urgent_macro(org_a):
    return Macro.objects.create(
        org=org_a,
        title="Escalate",
        body="Hi %customer_name%, about %case_subject%.",
        scope=Macro.SCOPE_ORG,
        set_priority="Urgent",
    )


def _apply(client, macro, case):
    return client.post(
        f"/api/macros/{macro.id}/apply/", {"case_id": str(case.id)}, format="json"
    )


def _render(client, macro, case):
    return client.post(
        f"/api/macros/{macro.id}/render/", {"case_id": str(case.id)}, format="json"
    )


class TestMatcher:
    ID = str(uuid.uuid4())

    @pytest.mark.parametrize(
        "path",
        [
            f"/api/macros/{ID}/apply/",
            # Every spelling the `<uid:pk>` converter routes to the same view.
            f"/api/macros/{ID.replace('-', '')}/apply/",
            f"/api/macros/{ID.upper()}/apply/",
            f"/api/macros/{{{ID}}}/apply/",
        ],
    )
    def test_apply_needs_cases_write(self, path):
        assert scopes.required_scopes("POST", path) == ["macros:write", "cases:write"]

    def test_render_is_a_read_that_needs_cases_read(self):
        path = f"/api/macros/{self.ID}/render/"
        assert scopes.action_for("POST", path) == "read"
        assert scopes.required_scopes("POST", path) == ["macros:read", "cases:read"]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/macros/",
            f"/api/macros/{ID}/",
            f"/api/macros/{ID}/apply",  # missing slash
            f"/api/macros/{ID}/apply/extra/",
            f"/api/macros/{ID}/x/apply/",
            "/api/macros//apply/",
            f"/api/macrosx/{ID}/apply/",
            f"/api/cases/{ID}/apply/",
            f"/macros/{ID}/apply/",
        ],
    )
    def test_lookalike_paths_are_plain_macro_writes(self, path):
        needed = scopes.required_scopes("POST", path)
        assert needed is None or needed == [f"{scopes.resource_for_path(path)}:write"]

    def test_render_lookalike_stays_a_write(self):
        path = f"/api/macros/{self.ID}/render/extra/"
        assert scopes.action_for("POST", path) == "write"

    def test_the_denial_names_the_missing_cases_scope(self):
        denial = scopes.check_request(
            ["macros:write"], "POST", f"/api/macros/{self.ID}/apply/"
        )
        assert denial == (
            "This token is not scoped for write access to cases. "
            "It needs the cases:write scope."
        )

    def test_unscoped_token_is_still_unrestricted(self):
        assert scopes.check_request([], "POST", f"/api/macros/{self.ID}/apply/") is None


@pytest.mark.django_db
class TestApplyOverHttp:
    def test_macros_write_alone_cannot_change_a_ticket(self, admin_profile, ticket):
        client = _pat_client(admin_profile, scopes=["macros:write"])
        created = client.post(
            "/api/macros/",
            {"title": "x", "scope": "personal", "set_priority": "Urgent"},
            format="json",
        )
        assert created.status_code == 201, created.content
        macro = Macro.objects.get(id=created.json()["id"])

        response = _apply(client, macro, ticket)

        assert response.status_code == 403
        assert "cases:write" in response.json()["detail"]
        ticket.refresh_from_db()
        assert ticket.priority == "Normal"

    def test_macros_write_with_cases_write_applies(
        self, admin_profile, ticket, urgent_macro
    ):
        client = _pat_client(admin_profile, scopes=["macros:write", "cases:write"])
        response = _apply(client, urgent_macro, ticket)
        assert response.status_code == 200, response.content
        ticket.refresh_from_db()
        assert ticket.priority == "Urgent"

    def test_cases_write_alone_cannot_apply(self, admin_profile, ticket, urgent_macro):
        client = _pat_client(admin_profile, scopes=["cases:write"])
        response = _apply(client, urgent_macro, ticket)
        assert response.status_code == 403
        assert "macros:write" in response.json()["detail"]
        ticket.refresh_from_db()
        assert ticket.priority == "Normal"

    def test_wildcard_write_applies(self, admin_profile, ticket, urgent_macro):
        client = _pat_client(admin_profile, scopes=["*:write"])
        response = _apply(client, urgent_macro, ticket)
        assert response.status_code == 200, response.content
        ticket.refresh_from_db()
        assert ticket.priority == "Urgent"

    def test_org_api_key_cannot_apply(self, org_a, admin_profile, ticket, urgent_macro):
        response = _apply(_key_client(org_a), urgent_macro, ticket)
        assert response.status_code == 403
        ticket.refresh_from_db()
        assert ticket.priority == "Normal"


@pytest.mark.django_db
class TestRenderOverHttp:
    def test_macros_read_alone_cannot_render(self, admin_profile, ticket, urgent_macro):
        client = _pat_client(admin_profile, scopes=["macros:read"])
        response = _render(client, urgent_macro, ticket)
        assert response.status_code == 403
        assert "cases:read" in response.json()["detail"]
        assert "Printer" not in response.content.decode()
        urgent_macro.refresh_from_db()
        assert urgent_macro.usage_count == 0

    def test_macros_read_with_cases_read_renders(
        self, admin_profile, ticket, urgent_macro
    ):
        client = _pat_client(admin_profile, scopes=["macros:read", "cases:read"])
        response = _render(client, urgent_macro, ticket)
        assert response.status_code == 200, response.content
        assert "Printer" in response.json()["rendered_body"]

    def test_macros_write_alone_cannot_render(
        self, admin_profile, ticket, urgent_macro
    ):
        # Render is a read now, and `write` never implies `read`.
        client = _pat_client(admin_profile, scopes=["macros:write", "cases:write"])
        assert _render(client, urgent_macro, ticket).status_code == 403

    def test_org_api_key_may_render(self, org_a, admin_profile, ticket, urgent_macro):
        # The key's `*:read` covers both scopes, and it can already read the
        # case itself through GET /api/cases/<id>/.
        response = _render(_key_client(org_a), urgent_macro, ticket)
        assert response.status_code == 200, response.content
        assert "Printer" in response.json()["rendered_body"]
