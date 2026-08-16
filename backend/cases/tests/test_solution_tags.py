"""Tagging a knowledge base article.

Tags are the agents' own vocabulary and are shared with leads, deals and
tickets, so this org's list holds things like "At Risk" and "VIP". None of it
is written for customers to read, which is why tags stay on the agent
serializers and the portal only ever uses them to compute related articles.
The projection guard in `test_portal_articles.py` is what enforces that.
"""

import pytest
from django.db import connection

from cases.models import Solution
from common.models import Tags

SOLUTIONS_LIST_URL = "/api/cases/solutions/"


def _detail_url(pk):
    return f"/api/cases/solutions/{pk}/"


def _set_rls(org):
    """Set PostgreSQL RLS context so direct ORM writes are allowed.
    No-op on SQLite (used in tests).
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.fixture
def billing_tag(org_a):
    _set_rls(org_a)
    return Tags.objects.create(org=org_a, name="Billing")


@pytest.fixture
def access_tag(org_a):
    _set_rls(org_a)
    return Tags.objects.create(org=org_a, name="Access")


@pytest.fixture
def article(org_a, admin_user):
    _set_rls(org_a)
    return Solution.objects.create(
        org=org_a,
        title="Resetting your password",
        description="Open settings, then reset.",
        status="draft",
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestTaggingAnArticle:
    def test_creates_an_article_with_tags(self, admin_client, billing_tag):
        response = admin_client.post(
            SOLUTIONS_LIST_URL,
            {
                "title": "Understanding your invoice",
                "description": "Each line is one seat for one month.",
                "tags": [str(billing_tag.id)],
            },
            format="json",
        )
        assert response.status_code == 201
        article = Solution.objects.get(title="Understanding your invoice")
        assert list(article.tags.all()) == [billing_tag]

    def test_cannot_attach_another_orgs_tag(self, admin_client, article, org_b):
        """The ids come from the client, so the org filter is the whole defence."""
        _set_rls(org_b)
        theirs = Tags.objects.create(org=org_b, name="Their internal label")
        _set_rls(article.org)

        response = admin_client.patch(
            _detail_url(article.pk), {"tags": [str(theirs.id)]}, format="json"
        )
        assert response.status_code == 200
        assert list(article.tags.all()) == []

    def test_cannot_attach_an_archived_tag(self, admin_client, article, org_a):
        _set_rls(org_a)
        retired = Tags.objects.create(org=org_a, name="Retired", is_active=False)
        admin_client.patch(
            _detail_url(article.pk), {"tags": [str(retired.id)]}, format="json"
        )
        assert list(article.tags.all()) == []

    def test_replaces_the_whole_set(
        self, admin_client, article, billing_tag, access_tag
    ):
        article.tags.add(billing_tag)
        admin_client.patch(
            _detail_url(article.pk), {"tags": [str(access_tag.id)]}, format="json"
        )
        assert list(article.tags.all()) == [access_tag]

    def test_a_patch_that_omits_tags_keeps_them(
        self, admin_client, article, billing_tag
    ):
        """The bug this endpoint deliberately does not copy.

        `cases` clears and re-adds, and `AccountDetailView.put` clears outright,
        so editing a title there drops every tag. Absent means unchanged here.
        """
        article.tags.add(billing_tag)
        admin_client.patch(_detail_url(article.pk), {"title": "Renamed"}, format="json")
        assert list(article.tags.all()) == [billing_tag]

    def test_an_explicit_empty_list_clears_them(
        self, admin_client, article, billing_tag
    ):
        article.tags.add(billing_tag)
        admin_client.patch(_detail_url(article.pk), {"tags": []}, format="json")
        assert list(article.tags.all()) == []


@pytest.mark.django_db
class TestAgentsSeeTags:
    def test_the_detail_payload_names_the_tags(
        self, admin_client, article, billing_tag
    ):
        article.tags.add(billing_tag)
        body = admin_client.get(_detail_url(article.pk)).json()
        assert [t["name"] for t in body["tags"]] == ["Billing"]

    def test_the_list_payload_names_the_tags(self, admin_client, article, billing_tag):
        article.tags.add(billing_tag)
        row = next(
            r
            for r in admin_client.get(SOLUTIONS_LIST_URL).json()["results"]
            if r["id"] == str(article.id)
        )
        assert [t["name"] for t in row["tags"]] == ["Billing"]

    def test_filters_the_list_by_tag(
        self, admin_client, article, billing_tag, access_tag, org_a, admin_user
    ):
        article.tags.add(billing_tag)
        _set_rls(org_a)
        other = Solution.objects.create(
            org=org_a, title="Signing in", description="...", created_by=admin_user
        )
        other.tags.add(access_tag)

        body = admin_client.get(f"{SOLUTIONS_LIST_URL}?tags={access_tag.id}").json()
        assert [r["id"] for r in body["results"]] == [str(other.id)]
