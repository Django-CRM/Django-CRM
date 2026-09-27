"""Unit tests for the token scope vocabulary and matcher.

These exercise `common.scopes` in isolation, with no request cycle and no
database. The middleware-level behaviour (what a real request gets back when a
scope refuses it) lives in `test_pat_scope_enforcement.py`.
"""

import pytest

from common import scopes


class TestNormalizeScope:
    def test_accepts_resource_and_action(self):
        assert scopes.normalize_scope("leads:read") == "leads:read"

    def test_accepts_wildcard_resource(self):
        assert scopes.normalize_scope("*:write") == "*:write"

    def test_lowercases_and_strips(self):
        assert scopes.normalize_scope("  LEADS:Read ") == "leads:read"

    def test_rejects_unknown_resource(self):
        with pytest.raises(ValueError):
            scopes.normalize_scope("nosuchapp:read")

    def test_rejects_unknown_action(self):
        with pytest.raises(ValueError):
            scopes.normalize_scope("leads:destroy")

    def test_rejects_bare_resource(self):
        with pytest.raises(ValueError):
            scopes.normalize_scope("leads")

    def test_rejects_extra_segments(self):
        with pytest.raises(ValueError):
            scopes.normalize_scope("leads:read:extra")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            scopes.normalize_scope("")


class TestResourceForPath:
    def test_reads_first_segment_under_api(self):
        assert scopes.resource_for_path("/api/leads/") == "leads"

    def test_ignores_deeper_segments(self):
        assert scopes.resource_for_path("/api/cases/123/comment/") == "cases"

    def test_hyphenated_root(self):
        assert scopes.resource_for_path("/api/business-hours/") == "business-hours"

    def test_non_api_path_is_none(self):
        assert scopes.resource_for_path("/admin/") is None

    def test_bare_api_root_is_none(self):
        assert scopes.resource_for_path("/api/") is None


class TestActionFor:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
    def test_safe_methods_read(self, method):
        assert scopes.action_for(method, "/api/leads/") == "read"

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "delete"])
    def test_unsafe_methods_write(self, method):
        assert scopes.action_for(method, "/api/leads/") == "write"


class TestScopesAllow:
    def test_empty_scopes_are_unrestricted(self):
        assert scopes.scopes_allow([], "DELETE", "/api/leads/1/") is True

    def test_read_scope_allows_get(self):
        assert scopes.scopes_allow(["*:read"], "GET", "/api/leads/") is True

    def test_read_scope_refuses_post(self):
        assert scopes.scopes_allow(["*:read"], "POST", "/api/leads/") is False

    def test_write_scope_does_not_imply_read(self):
        """A write-only integration must ask for both, and be told so.

        Implicit widening is how a scope stops meaning what its name says.
        """
        assert scopes.scopes_allow(["leads:write"], "GET", "/api/leads/") is False
        assert scopes.scopes_allow(["leads:write"], "POST", "/api/leads/") is True

    def test_resource_scope_is_confined_to_that_resource(self):
        assert scopes.scopes_allow(["leads:read"], "GET", "/api/leads/") is True
        assert scopes.scopes_allow(["leads:read"], "GET", "/api/contacts/") is False

    def test_search_is_its_own_resource(self):
        """Global search spans every module, so a per-module scope must not reach it."""
        assert scopes.scopes_allow(["leads:read"], "GET", "/api/search/") is False
        assert scopes.scopes_allow(["search:read"], "GET", "/api/search/") is True

    def test_scoped_token_denied_on_unrecognised_path(self):
        """Fail closed: a path with no resource cannot be matched by any scope."""
        assert scopes.scopes_allow(["*:read"], "GET", "/admin/") is False

    def test_multiple_scopes_union(self):
        held = ["leads:read", "contacts:write"]
        assert scopes.scopes_allow(held, "GET", "/api/leads/") is True
        assert scopes.scopes_allow(held, "POST", "/api/contacts/") is True
        assert scopes.scopes_allow(held, "POST", "/api/leads/") is False

    def test_unparseable_stored_scope_is_ignored_not_fatal(self):
        """Rows predating validation may hold junk. It must never grant anything.

        `validate_scopes` only guards new tokens, so a token created before this
        landed can carry any string at all. Junk is dropped, and a token left
        with nothing usable is refused rather than treated as unrestricted.
        """
        assert scopes.scopes_allow(["not-a-scope"], "GET", "/api/leads/") is False
        assert (
            scopes.scopes_allow(["not-a-scope", "leads:read"], "GET", "/api/leads/")
            is True
        )


class TestCredentialPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/profile/tokens/",
            "/api/profile/tokens/8ad0d0f6-0000-0000-0000-000000000000/",
            "/api/org/tokens/",
            "/api/org/api-key/",
        ],
    )
    def test_credential_paths_are_denied(self, path):
        assert scopes.credential_path_denial(path) is not None

    @pytest.mark.parametrize(
        "path", ["/api/leads/", "/api/org/settings/", "/api/profile/"]
    )
    def test_ordinary_paths_are_not_denied(self, path):
        assert scopes.credential_path_denial(path) is None

    def test_denial_survives_full_access(self):
        """The deny-list is independent of scopes, so `[]` does not open it.

        A leaked token that can mint another token cannot be revoked, and a
        leaked token that can read the org API key upgrades itself into a
        credential that outlives its own revocation.
        """
        assert scopes.check_request([], "GET", "/api/org/api-key/") is not None
        assert (
            scopes.check_request(["*:write"], "POST", "/api/profile/tokens/")
            is not None
        )

    def test_check_request_allows_an_ordinary_scoped_read(self):
        assert scopes.check_request(["leads:read"], "GET", "/api/leads/") is None


@pytest.mark.django_db
def test_api_resources_covers_the_live_urlconf():
    """Every API root the project actually serves must be in the vocabulary.

    `API_RESOURCES` is hand-maintained so it stays reviewable in a diff. This is
    the guard that keeps it honest: mount a new app at a new root and this fails,
    instead of that root silently becoming unreachable for every scoped token.
    """
    from django.urls import get_resolver

    found = set()

    def walk(patterns, prefix=""):
        for entry in patterns:
            path = prefix + str(entry.pattern)
            if hasattr(entry, "url_patterns"):
                walk(entry.url_patterns, path)
            elif path.startswith("api/"):
                segment = path[len("api/") :].split("/")[0]
                if segment:
                    found.add(segment)

    walk(get_resolver().url_patterns)

    missing = found - set(scopes.API_RESOURCES)
    assert not missing, f"API roots missing from API_RESOURCES: {sorted(missing)}"
