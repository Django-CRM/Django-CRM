"""What a portal customer may read of the knowledge base, and everything they
may not.

The agent-side rules live in `test_solution_access.py`. These are the customer
rules, and they are stricter: an article reaches a customer only once an admin
has both approved and released it.
"""

import pytest
from rest_framework.test import APIClient

from cases.models import Solution
from common.models import Tags
from common.portal_auth import mint_portal_token
from contacts.models import Contact

LIST_URL = "/api/portal/articles/"
SUGGEST_URL = "/api/portal/articles/suggest/"


def _client(contact):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {mint_portal_token(contact)}")
    return client


def _article(org, title="Resetting your password", **kwargs):
    """A published, approved article unless a test says otherwise."""
    fields = {
        "description": "Open settings, choose security, then reset.",
        "status": "approved",
        "is_published": True,
    }
    fields.update(kwargs)
    return Solution.objects.create(org=org, title=title, **fields)


@pytest.fixture
def billing(org_a):
    return Tags.objects.create(org=org_a, name="Billing")


@pytest.fixture
def pat(org_a):
    return Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )


@pytest.mark.django_db
class TestPortalArticleList:
    def test_lists_a_published_article(self, pat, org_a):
        article = _article(org_a)
        response = _client(pat).get(LIST_URL)
        assert response.status_code == 200
        assert [a["id"] for a in response.json()["articles"]] == [str(article.id)]

    def test_hides_an_unpublished_article(self, pat, org_a):
        _article(org_a, title="Not released", is_published=False)
        assert _client(pat).get(LIST_URL).json()["articles"] == []

    def test_hides_a_draft(self, pat, org_a):
        _article(org_a, title="Half written", status="draft", is_published=False)
        assert _client(pat).get(LIST_URL).json()["articles"] == []

    def test_hides_a_legacy_published_draft(self, pat, org_a):
        """A row from before the published-implies-approved rule existed.

        `SolutionSerializer.validate` refuses to create one now, so it is built
        here the only way it can still arise: straight through the ORM. If the
        portal trusted `is_published` alone this would be customer-visible.
        """
        _article(org_a, title="Published by accident", status="draft")
        assert _client(pat).get(LIST_URL).json()["articles"] == []

    def test_hides_another_orgs_article(self, pat, org_a, org_b):
        mine = _article(org_a)
        _article(org_b, title="Someone else's answer")
        ids = [a["id"] for a in _client(pat).get(LIST_URL).json()["articles"]]
        assert ids == [str(mine.id)]

    def test_search_matches_the_title(self, pat, org_a):
        wanted = _article(org_a, title="Resetting your password")
        _article(org_a, title="Exporting invoices")
        ids = [
            a["id"]
            for a in _client(pat).get(LIST_URL + "?search=password").json()["articles"]
        ]
        assert ids == [str(wanted.id)]

    def test_search_matches_the_body(self, pat, org_a):
        wanted = _article(
            org_a, title="Billing", description="Your VAT number lives in settings."
        )
        _article(org_a, title="Exporting invoices", description="Use the export tab.")
        ids = [
            a["id"]
            for a in _client(pat).get(LIST_URL + "?search=VAT").json()["articles"]
        ]
        assert ids == [str(wanted.id)]

    def test_search_does_not_reach_past_the_visibility_rule(self, pat, org_a, org_b):
        """Searching must not become a second, wider way in."""
        _article(org_a, title="Password draft", status="draft", is_published=False)
        _article(org_b, title="Password reset for another org")
        assert _client(pat).get(LIST_URL + "?search=password").json()["articles"] == []

    def test_anonymous_is_refused(self, org_a):
        _article(org_a)
        assert APIClient().get(LIST_URL).status_code in (401, 403)


@pytest.mark.django_db
class TestPortalArticleDetail:
    def test_reads_a_published_article(self, pat, org_a):
        article = _article(org_a, description="Open settings, then reset.")
        response = _client(pat).get(f"{LIST_URL}{article.id}/")
        assert response.status_code == 200
        assert response.json()["article"]["description"] == "Open settings, then reset."

    def test_a_draft_is_not_found(self, pat, org_a):
        article = _article(org_a, status="draft", is_published=False)
        assert _client(pat).get(f"{LIST_URL}{article.id}/").status_code == 404

    def test_an_unpublished_article_is_not_found(self, pat, org_a):
        article = _article(org_a, is_published=False)
        assert _client(pat).get(f"{LIST_URL}{article.id}/").status_code == 404

    def test_another_orgs_article_is_not_found(self, pat, org_b):
        article = _article(org_b)
        assert _client(pat).get(f"{LIST_URL}{article.id}/").status_code == 404

    def test_anonymous_is_refused(self, org_a):
        article = _article(org_a)
        assert APIClient().get(f"{LIST_URL}{article.id}/").status_code in (401, 403)

    def test_the_projection_withholds_everything_internal(self, pat, org_a, case_a):
        """The leak that would matter, pinned field by field.

        Adding any of these names to `PortalSolutionDetailSerializer.Meta.fields`
        is the production change that breaks this test, which is the point:
        the projection is the security control, so it needs a guard.
        """
        article = _article(org_a)
        article.cases.add(case_a)

        body = _client(pat).get(f"{LIST_URL}{article.id}/").json()["article"]

        assert set(body) == {"id", "title", "description", "updated_at"}
        for withheld in (
            "status",
            "is_published",
            "org",
            "created_by",
            "author",
            "linked_cases",
            "cases",
        ):
            assert withheld not in body

    def test_a_linked_case_id_never_reaches_the_customer(self, pat, org_a, case_a):
        """A published article can be linked to somebody else's ticket."""
        article = _article(org_a)
        article.cases.add(case_a)
        rendered = str(_client(pat).get(f"{LIST_URL}{article.id}/").json())
        assert str(case_a.id) not in rendered


@pytest.mark.django_db
class TestPortalRelatedArticles:
    """Related articles come from the agents' tags, without exposing them.

    The tag vocabulary is shared with leads and deals and reads like "At Risk"
    and "VIP", so it computes the list and never appears in it.
    """

    def test_relates_two_articles_sharing_a_tag(self, pat, org_a, billing):
        one = _article(org_a, title="Understanding your invoice")
        two = _article(org_a, title="Changing your billing address")
        one.tags.add(billing)
        two.tags.add(billing)

        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert [r["title"] for r in body["related"]] == [
            "Changing your billing address"
        ]

    def test_never_relates_an_article_to_itself(self, pat, org_a, billing):
        one = _article(org_a)
        one.tags.add(billing)
        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert body["related"] == []

    def test_an_untagged_article_relates_to_nothing(self, pat, org_a, billing):
        one = _article(org_a, title="Untagged")
        two = _article(org_a, title="Tagged")
        two.tags.add(billing)
        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert body["related"] == []

    def test_related_obeys_the_visibility_rule(self, pat, org_a, billing):
        """A shared tag is not a way past `_published_articles`."""
        one = _article(org_a, title="Visible")
        draft = _article(org_a, title="Draft", status="draft", is_published=False)
        unreleased = _article(org_a, title="Unreleased", is_published=False)
        for article in (one, draft, unreleased):
            article.tags.add(billing)

        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert body["related"] == []

    def test_related_never_crosses_an_org(self, pat, org_a, org_b, billing):
        """Same tag *name* in two orgs is two different rows, but prove it."""
        one = _article(org_a, title="Mine")
        theirs = _article(org_b, title="Theirs")
        their_tag = Tags.objects.create(org=org_b, name="Billing")
        one.tags.add(billing)
        theirs.tags.add(their_tag)

        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert body["related"] == []

    def test_related_is_capped(self, pat, org_a, billing):
        one = _article(org_a, title="Anchor")
        one.tags.add(billing)
        for i in range(6):
            sibling = _article(org_a, title=f"Sibling {i}")
            sibling.tags.add(billing)

        body = _client(pat).get(f"{LIST_URL}{one.id}/").json()
        assert len(body["related"]) == 3

    def test_related_carries_no_tag_names(self, pat, org_a, billing):
        one = _article(org_a, title="Anchor")
        two = _article(org_a, title="Sibling")
        one.tags.add(billing)
        two.tags.add(billing)

        rendered = str(_client(pat).get(f"{LIST_URL}{one.id}/").json())
        assert "Billing" not in rendered
        assert set(_client(pat).get(f"{LIST_URL}{one.id}/").json()["related"][0]) == {
            "id",
            "title",
        }


@pytest.mark.django_db
class TestPortalArticleSuggest:
    """Deflection: what the customer is shown while typing a new request."""

    def test_suggests_a_matching_article(self, pat, org_a):
        wanted = _article(org_a, title="Resetting your password")
        _article(org_a, title="Exporting invoices")
        response = _client(pat).get(SUGGEST_URL + "?q=password")
        assert response.status_code == 200
        assert [a["id"] for a in response.json()["articles"]] == [str(wanted.id)]

    def test_suggest_is_not_swallowed_by_the_detail_route(self, pat, org_a):
        """`<uid:pk>` matches any segment and rejects in `to_python`.

        So this path only reaches the suggester because the resolver falls
        through. Pinned here because reordering the two routes would break it
        silently, returning "Article not found" for every suggestion.
        """
        response = _client(pat).get(SUGGEST_URL + "?q=anything")
        assert response.status_code == 200
        assert "articles" in response.json()

    def test_a_blank_query_suggests_nothing(self, pat, org_a):
        """No guessing. An empty box is not a request for a random article."""
        _article(org_a)
        assert _client(pat).get(SUGGEST_URL + "?q=").json()["articles"] == []

    def test_a_missing_query_suggests_nothing(self, pat, org_a):
        _article(org_a)
        assert _client(pat).get(SUGGEST_URL).json()["articles"] == []

    def test_obeys_the_visibility_rule(self, pat, org_a, org_b):
        _article(org_a, title="Password draft", status="draft", is_published=False)
        _article(org_a, title="Password unreleased", is_published=False)
        _article(org_b, title="Password reset elsewhere")
        assert _client(pat).get(SUGGEST_URL + "?q=password").json()["articles"] == []

    def test_caps_the_number_returned(self, pat, org_a):
        for i in range(10):
            _article(org_a, title=f"Password article {i}")
        body = _client(pat).get(SUGGEST_URL + "?q=password").json()
        assert len(body["articles"]) == 3

    def test_carries_a_snippet_not_the_whole_body(self, pat, org_a):
        _article(org_a, title="Password", description="x" * 500)
        snippet = (
            _client(pat)
            .get(SUGGEST_URL + "?q=password")
            .json()["articles"][0]["snippet"]
        )
        assert len(snippet) < 500

    def test_anonymous_is_refused(self, org_a):
        _article(org_a)
        assert APIClient().get(SUGGEST_URL + "?q=password").status_code in (401, 403)
