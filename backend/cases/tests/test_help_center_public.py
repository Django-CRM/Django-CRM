"""The anonymous public help center: what a stranger may read, and nothing else.

The visibility rule is the portal's (`published_articles`): approved AND
published AND this org's. These tests pin that the public endpoints never
widen it, that every kind of miss is the same 404, and that a staff credential
sent along buys nothing.
"""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from cases.help_center_views import (
    HelpCenterGlobalThrottle,
    HelpCenterIPThrottle,
    HelpCenterPagination,
    PublicHelpCenterArticleView,
    PublicHelpCenterListView,
)
from cases.models import Solution
from common.models import Org, Tags
from common.testing import clear_rls_context, rls_org

SLUG = "acme-help"


def list_url(slug=SLUG):
    return f"/api/public/help/{slug}/"


def article_url(article_id, slug=SLUG):
    return f"/api/public/help/{slug}/articles/{article_id}/"


def _article(org, title="Resetting your password", **kwargs):
    """A published, approved article unless a test says otherwise."""
    fields = {
        "description": "Open settings, choose security, then reset.",
        "status": "approved",
        "is_published": True,
    }
    fields.update(kwargs)
    # Written as that org: under a role RLS binds, the insert-check policy
    # refuses a row for any org but the current one.
    with rls_org(org):
        return Solution.objects.create(org=org, title=title, **fields)


@pytest.fixture(autouse=True)
def _fresh_throttle():
    # The per-IP bucket lives in the cache and every test client shares one
    # address, so a long run would otherwise drift toward the limit.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def acme(org_a):
    org_a.help_center_slug = SLUG
    org_a.help_center_enabled = True
    org_a.save()
    return org_a


@pytest.fixture
def other(org_b):
    org_b.help_center_slug = "other-help"
    org_b.help_center_enabled = True
    org_b.save()
    return org_b


@pytest.fixture
def anon():
    return APIClient()


def _ids(response):
    return [a["id"] for a in response.json()["articles"]]


class TestList:
    def test_lists_a_published_approved_article(self, anon, acme):
        article = _article(acme)
        response = anon.get(list_url())
        assert response.status_code == 200
        body = response.json()
        assert _ids(response) == [str(article.id)]
        assert body["articles_count"] == 1
        assert body["help_center"] == {"name": "Test Organization A"}

    def test_list_item_carries_public_fields_only(self, anon, acme):
        _article(acme)
        item = anon.get(list_url()).json()["articles"][0]
        assert set(item) == {"id", "title", "updated_at", "snippet"}

    def test_company_name_is_preferred_for_the_header(self, anon, acme):
        acme.company_name = "Acme Ltd"
        acme.save()
        assert anon.get(list_url()).json()["help_center"] == {"name": "Acme Ltd"}

    @pytest.mark.parametrize(
        "fields",
        [
            {"status": "draft", "is_published": False},
            {"status": "reviewed", "is_published": False},
            {"status": "approved", "is_published": False},
            # A legacy row from before published-implies-approved was enforced.
            {"status": "draft", "is_published": True},
            {"status": "reviewed", "is_published": True},
        ],
    )
    def test_never_lists_an_article_a_customer_may_not_read(self, anon, acme, fields):
        _article(acme, title="Hidden", **fields)
        response = anon.get(list_url())
        assert response.json()["articles"] == []
        assert response.json()["articles_count"] == 0

    def test_never_lists_another_orgs_article(self, anon, acme, other):
        mine = _article(acme)
        _article(other, title="Someone else's answer")
        assert _ids(anon.get(list_url())) == [str(mine.id)]

    def test_search_matches_title_and_body(self, anon, acme):
        by_title = _article(acme, title="Resetting your password")
        by_body = _article(
            acme, title="Billing", description="Your VAT number lives in settings."
        )
        _article(acme, title="Exporting invoices", description="Use the export tab.")
        assert _ids(anon.get(list_url() + "?q=password")) == [str(by_title.id)]
        assert _ids(anon.get(list_url() + "?q=VAT")) == [str(by_body.id)]

    def test_search_cannot_reach_past_the_visibility_rule(self, anon, acme, other):
        _article(acme, title="Password draft", status="draft", is_published=False)
        _article(acme, title="Password reviewed", status="reviewed", is_published=True)
        _article(other, title="Password for another org")
        response = anon.get(list_url() + "?q=password")
        assert response.json()["articles"] == []
        assert response.json()["articles_count"] == 0

    def test_pagination_is_capped_for_anonymous_callers(self, anon, acme):
        for index in range(3):
            _article(acme, title=f"Article {index}")
        response = anon.get(list_url() + "?limit=2")
        assert len(response.json()["articles"]) == 2
        assert response.json()["articles_count"] == 3
        assert HelpCenterPagination.max_limit == 100


class TestArticle:
    def test_reads_a_published_article(self, anon, acme):
        article = _article(acme, description="Open settings, then reset.")
        response = anon.get(article_url(article.id))
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"help_center", "article", "related"}
        assert set(body["article"]) == {"id", "title", "description", "updated_at"}
        assert body["article"]["description"] == "Open settings, then reset."

    def test_related_is_built_from_the_visible_set_and_hides_tags(self, anon, acme):
        tag = Tags.objects.create(org=acme, name="VIP")
        article = _article(acme, title="Main")
        sibling = _article(acme, title="Sibling")
        draft = _article(
            acme, title="Draft sibling", status="draft", is_published=False
        )
        for row in (article, sibling, draft):
            row.tags.add(tag)
        body = anon.get(article_url(article.id)).json()
        assert body["related"] == [{"id": str(sibling.id), "title": "Sibling"}]
        assert "VIP" not in str(body)


class TestNotFoundIsOneAnswer:
    """Every miss must be byte-identical, so nothing can be probed."""

    def _signature(self, response):
        return (response.status_code, response["Content-Type"], response.content)

    def test_every_miss_is_the_same_response(self, anon, acme, org_b):
        disabled = Org.objects.create(
            name="Disabled", help_center_slug="disabled-help", help_center_enabled=False
        )
        inactive = Org.objects.create(
            name="Inactive",
            help_center_slug="inactive-help",
            help_center_enabled=True,
            is_active=False,
        )
        _article(disabled)
        _article(inactive)
        draft = _article(acme, status="draft", is_published=False)
        reviewed = _article(acme, status="reviewed", is_published=False)
        unpublished = _article(acme, status="approved", is_published=False)
        legacy = _article(acme, status="draft", is_published=True)
        foreign = _article(org_b)
        published = _article(acme)

        misses = [
            anon.get(list_url("no-such-help")),
            anon.get(list_url("disabled-help")),
            anon.get(list_url("inactive-help")),
            anon.get(list_url("ACME-HELP")),
            anon.get(article_url(published.id, slug="no-such-help")),
            anon.get(article_url(published.id, slug="disabled-help")),
            anon.get(article_url(published.id, slug="inactive-help")),
            anon.get(article_url(draft.id)),
            anon.get(article_url(reviewed.id)),
            anon.get(article_url(unpublished.id)),
            anon.get(article_url(legacy.id)),
            anon.get(article_url(foreign.id)),
            anon.get(article_url("00000000-0000-0000-0000-000000000000")),
        ]
        signatures = {self._signature(r) for r in misses}
        assert signatures == {(404, "application/json", b'{"error":"Not found"}')}

    def test_a_help_center_switched_off_disappears(self, anon, acme):
        article = _article(acme)
        assert anon.get(article_url(article.id)).status_code == 200
        acme.help_center_enabled = False
        acme.save()
        assert anon.get(list_url()).status_code == 404
        assert anon.get(article_url(article.id)).status_code == 404


class TestStaffCredentialBuysNothing:
    def test_same_orgs_admin_jwt_sees_exactly_what_anonymous_sees(
        self, anon, acme, admin_client
    ):
        _article(acme)
        _article(acme, title="Draft", status="draft", is_published=False)
        assert admin_client.get(list_url()).content == anon.get(list_url()).content
        assert admin_client.get(list_url() + "?q=Draft").json()["articles"] == []

    def test_admin_jwt_cannot_read_own_draft_through_the_public_path(
        self, acme, admin_client
    ):
        draft = _article(acme, status="draft", is_published=False)
        assert admin_client.get(article_url(draft.id)).status_code == 404

    def test_other_orgs_jwt_does_not_open_a_disabled_help_center(
        self, acme, org_b_client
    ):
        acme.help_center_enabled = False
        acme.save()
        assert org_b_client.get(list_url()).status_code == 404


class TestThrottle:
    def test_both_views_are_throttled_per_visitor(self):
        both = [HelpCenterIPThrottle, HelpCenterGlobalThrottle]
        assert PublicHelpCenterListView.throttle_classes == both
        assert PublicHelpCenterArticleView.throttle_classes == both
        assert PublicHelpCenterListView.authentication_classes == []

    def test_limit_returns_429(self, anon, acme, monkeypatch):
        # Patched on the class: `THROTTLE_RATES` is bound at import time, so
        # overriding settings would not reach it (see webforms test_submit).
        monkeypatch.setattr(
            HelpCenterIPThrottle, "THROTTLE_RATES", {"help_center_ip": "2/hour"}
        )
        assert anon.get(list_url()).status_code == 200
        assert anon.get(list_url()).status_code == 200
        assert anon.get(list_url()).status_code == 429

    def test_buckets_are_per_forwarded_client(self, anon, acme, monkeypatch):
        monkeypatch.setattr(
            HelpCenterIPThrottle, "THROTTLE_RATES", {"help_center_ip": "1/hour"}
        )
        first = anon.get(list_url(), HTTP_X_FORWARDED_FOR="203.0.113.1")
        second = anon.get(list_url(), HTTP_X_FORWARDED_FOR="203.0.113.2")
        assert (first.status_code, second.status_code) == (200, 200)

    def test_rotating_the_forwarded_address_hits_the_global_limit(
        self, anon, acme, monkeypatch
    ):
        """X-Forwarded-For is the caller's to set, so each request below gets a
        fresh per-visitor bucket; the per-help-center one still fills."""
        monkeypatch.setattr(
            HelpCenterGlobalThrottle,
            "THROTTLE_RATES",
            {"help_center_global": "2/hour"},
        )
        codes = [
            anon.get(list_url(), HTTP_X_FORWARDED_FOR=f"203.0.113.{n}").status_code
            for n in range(1, 4)
        ]
        assert codes == [200, 200, 429]

    def test_global_limit_is_per_help_center(self, anon, acme, other, monkeypatch):
        """One org's traffic never locks readers out of another org's pages."""
        monkeypatch.setattr(
            HelpCenterGlobalThrottle,
            "THROTTLE_RATES",
            {"help_center_global": "1/hour"},
        )
        assert anon.get(list_url()).status_code == 200
        assert anon.get(list_url()).status_code == 429
        assert anon.get(list_url("other-help")).status_code == 200


@pytest.mark.postgres_only
def test_public_read_runs_under_the_slugs_org_context(anon, acme):
    """Under a non-superuser role an empty context returns zero rows, so a
    listed article proves the view set the context from the slug."""
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    article = _article(acme)
    clear_rls_context()
    assert _ids(anon.get(list_url())) == [str(article.id)]
