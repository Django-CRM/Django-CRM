"""Rules the knowledge base documented but did not enforce.

Every case below was driven against the running API before the fix and
answered the wrong thing. What was broken:

1.  The Solution endpoints had **no authorization at all**; `IsAuthenticated`
    was the whole of it. A plain `USER` could rewrite an admin's article
    (200), publish it to customers (200) and hard-delete it (204). The
    draft → reviewed → approved workflow, the publish and unpublish
    endpoints and the settings UI around them were all decoration: whoever
    wrote an article could approve their own work and release it.
2.  `validate_is_published` was guarded by `if value and self.instance`, so on
    **create** it did not run. `POST {"status": "draft", "is_published":
    true}` returned 201 with a published draft.
3.  On update the same check read the **stored** status, never the incoming
    one, and only ran when `is_published` was in the body, so
    `PATCH {"status": "draft"}` on an approved, published article left it
    published. The invariant the model asserts ("published implies approved")
    held on one path in three.
4.  `SolutionDetailSerializer.get_linked_cases` returned the **full 33-field
    case payload** for every linked case with no access check, and
    `CaseSolutionLinkView` checked org and nothing else. So a member refused
    a case with a 403 could link an article to that very case and read its
    name, description, account and contacts back out of the article.
5.  A malformed id was a **500** on every verb. Read, update, delete,
    publish, unpublish, because a UUID column raises `ValidationError`
    rather than returning nothing, and the views caught only
    `Solution.DoesNotExist`.
6.  `?is_published=1` silently meant *unpublished*: the parse was
    `value.lower() == "true"`, so every other spelling of true inverted the
    filter.
7.  The list served `case_count` and a nested `org` per row without fetching
    either, 13 articles cost 27 queries, and `created_by` came back as a
    bare `User` UUID, so no client could name the author.
"""

import pytest
from django.db import connection
from rest_framework import status

from cases.models import Case, Solution

SOLUTIONS_URL = "/api/cases/solutions/"


def _detail(pk):
    return f"{SOLUTIONS_URL}{pk}/"


def _publish(pk):
    return f"{SOLUTIONS_URL}{pk}/publish/"


def _unpublish(pk):
    return f"{SOLUTIONS_URL}{pk}/unpublish/"


def _link(case_pk):
    return f"/api/cases/{case_pk}/solutions/"


def _unlink(case_pk, solution_pk):
    return f"/api/cases/{case_pk}/solutions/{solution_pk}/"


def _set_rls(org):
    """Set the RLS context so direct ORM writes are allowed on PostgreSQL."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


def _article(org, author, **kwargs):
    _set_rls(org)
    article = Solution.objects.create(
        title=kwargs.pop("title", "Resetting a locked account"),
        description=kwargs.pop("description", "Open the console, then reset."),
        org=org,
        **kwargs,
    )
    # `BaseModel.save()` reads `created_by` from the crum thread-local and
    # writes None when no request is in flight, so passing it to `create()`
    # stores nothing. `.update()` writes the column directly.
    Solution.objects.filter(pk=article.pk).update(created_by=author)
    article.refresh_from_db()
    return article


@pytest.fixture
def admin_article(org_a, admin_user):
    return _article(org_a, admin_user, title="Admin's article", status="approved")


@pytest.fixture
def member_article(org_a, regular_user):
    return _article(org_a, regular_user, title="Member's article")


@pytest.mark.django_db
class TestWhoMayEditAnArticle:
    """`write`, author or admin. Proven both ways, per rule."""

    def test_author_may_edit_their_own(self, user_client, member_article):
        response = user_client.patch(
            _detail(member_article.pk),
            {"description": "A better answer."},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        member_article.refresh_from_db()
        assert member_article.description == "A better answer."

    def test_member_may_not_edit_somebody_elses(self, user_client, admin_article):
        response = user_client.patch(
            _detail(admin_article.pk), {"title": "Hijacked"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        admin_article.refresh_from_db()
        assert admin_article.title == "Admin's article"

    def test_admin_may_edit_anybody_elses(self, admin_client, member_article):
        response = admin_client.patch(
            _detail(member_article.pk), {"title": "Tidied up"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        member_article.refresh_from_db()
        assert member_article.title == "Tidied up"

    def test_author_may_delete_their_own(self, user_client, member_article):
        response = user_client.delete(_detail(member_article.pk))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Solution.objects.filter(pk=member_article.pk).exists()

    def test_member_may_not_delete_somebody_elses(self, user_client, admin_article):
        response = user_client.delete(_detail(admin_article.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Solution.objects.filter(pk=admin_article.pk).exists()

    def test_every_member_may_read_every_article(self, user_client, admin_article):
        """`read` is deliberately not restricted. See `cases.kb_access`."""
        response = user_client.get(_detail(admin_article.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "Admin's article"


@pytest.mark.django_db
class TestApprovingAndPublishingAreAdminOnly:
    """`release` is narrower than `write` on purpose: the author must not be
    the one who certifies their own answer."""

    def test_author_may_not_approve_their_own_article(
        self, user_client, member_article
    ):
        response = user_client.patch(
            _detail(member_article.pk), {"status": "approved"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        member_article.refresh_from_db()
        assert member_article.status == "draft"

    def test_author_may_send_their_own_article_for_review(
        self, user_client, member_article
    ):
        """draft → reviewed is asking for a check, not performing one."""
        response = user_client.patch(
            _detail(member_article.pk), {"status": "reviewed"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        member_article.refresh_from_db()
        assert member_article.status == "reviewed"

    def test_admin_may_approve(self, admin_client, member_article):
        response = admin_client.patch(
            _detail(member_article.pk), {"status": "approved"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        member_article.refresh_from_db()
        assert member_article.status == "approved"

    def test_member_may_not_publish_through_the_endpoint(
        self, user_client, admin_article
    ):
        response = user_client.post(_publish(admin_article.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        admin_article.refresh_from_db()
        assert admin_article.is_published is False

    def test_member_may_not_publish_through_a_patch(
        self, user_client, org_a, regular_user
    ):
        """The field and the endpoint are the same act, so they take the same
        rule. Gating only the endpoint would leave the door beside it open."""
        article = _article(org_a, regular_user, status="approved")
        response = user_client.patch(
            _detail(article.pk), {"is_published": True}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        article.refresh_from_db()
        assert article.is_published is False

    def test_member_may_not_unpublish(self, user_client, org_a, admin_user):
        article = _article(org_a, admin_user, status="approved", is_published=True)
        response = user_client.post(_unpublish(article.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        article.refresh_from_db()
        assert article.is_published is True

    def test_admin_may_publish_and_unpublish(self, admin_client, org_a, admin_user):
        article = _article(org_a, admin_user, status="approved")
        assert admin_client.post(_publish(article.pk)).status_code == status.HTTP_200_OK
        article.refresh_from_db()
        assert article.is_published is True

        assert (
            admin_client.post(_unpublish(article.pk)).status_code == status.HTTP_200_OK
        )
        article.refresh_from_db()
        assert article.is_published is False

    def test_repeating_the_status_an_article_already_has_is_not_an_approval(
        self, user_client, org_a, regular_user
    ):
        """A gate that reads values rather than transitions would stop an
        author fixing a typo on their own already-approved article."""
        article = _article(org_a, regular_user, status="approved")
        response = user_client.patch(
            _detail(article.pk),
            {"status": "approved", "description": "Fixed a typo."},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        article.refresh_from_db()
        assert article.description == "Fixed a typo."


@pytest.mark.django_db
class TestPublishedImpliesApproved:
    """One invariant, checked on every path into the two fields."""

    def test_cannot_create_a_published_draft(self, admin_client):
        response = admin_client.post(
            SOLUTIONS_URL,
            {
                "title": "Straight to the customer",
                "description": "Nobody has read this.",
                "status": "draft",
                "is_published": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is_published" in response.data
        assert not Solution.objects.filter(is_published=True).exists()

    def test_can_create_an_approved_published_article(self, admin_client):
        response = admin_client.post(
            SOLUTIONS_URL,
            {
                "title": "Checked and released",
                "description": "Somebody has read this.",
                "status": "approved",
                "is_published": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_published"] is True

    def test_cannot_demote_a_published_article(self, admin_client, org_a, admin_user):
        article = _article(org_a, admin_user, status="approved", is_published=True)
        response = admin_client.patch(
            _detail(article.pk), {"status": "draft"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # The error names the field the caller can act on, not the one they sent.
        assert "status" in response.data
        article.refresh_from_db()
        assert article.status == "approved"
        assert article.is_published is True

    def test_unpublishing_first_makes_the_demotion_legal(
        self, admin_client, org_a, admin_user
    ):
        article = _article(org_a, admin_user, status="approved", is_published=True)
        assert (
            admin_client.post(_unpublish(article.pk)).status_code == status.HTTP_200_OK
        )
        response = admin_client.patch(
            _detail(article.pk), {"status": "draft"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        article.refresh_from_db()
        assert article.status == "draft"

    def test_an_already_inconsistent_row_stays_editable(
        self, user_client, org_a, regular_user
    ):
        """Rows written before the rule existed can be published drafts.
        Judging the stored combination on a request that touches neither
        field would make them uneditable, and editing is how somebody fixes
        one."""
        article = _article(org_a, regular_user, status="draft", is_published=True)
        response = user_client.patch(
            _detail(article.pk), {"description": "Still editable."}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        article.refresh_from_db()
        assert article.description == "Still editable."

    def test_but_moving_either_field_must_land_somewhere_legal(
        self, user_client, org_a, regular_user
    ):
        article = _article(org_a, regular_user, status="draft", is_published=True)
        response = user_client.patch(
            _detail(article.pk), {"status": "reviewed"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        article.refresh_from_db()
        assert article.status == "draft"


@pytest.mark.django_db
class TestLinkedCasesIsNotAWayIntoCases:
    """The read-around, both halves of it."""

    @pytest.fixture
    def hidden_case(self, org_a, admin_user):
        """A case the regular member is not allowed to open: they did not
        create it, are not assigned to it and are not watching it."""
        _set_rls(org_a)
        case = Case.objects.create(
            name="Payroll export fails",
            status="New",
            priority="High",
            description="Contains things the member may not read.",
            org=org_a,
        )
        Case.objects.filter(pk=case.pk).update(created_by=admin_user)
        return case

    def test_the_member_really_cannot_open_that_case(self, user_client, hidden_case):
        """The premise. Without this the rest proves nothing."""
        response = user_client.get(f"/api/cases/{hidden_case.pk}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_member_may_not_link_an_article_to_it(
        self, user_client, hidden_case, member_article
    ):
        response = user_client.post(
            _link(hidden_case.pk),
            {"solution_id": str(member_article.pk)},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert hidden_case.solutions.count() == 0

    def test_member_may_not_unlink_from_it(
        self, user_client, hidden_case, member_article
    ):
        hidden_case.solutions.add(member_article)
        response = user_client.delete(_unlink(hidden_case.pk, member_article.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert hidden_case.solutions.count() == 1

    def test_linked_cases_hides_the_case_from_the_member(
        self, user_client, hidden_case, member_article
    ):
        hidden_case.solutions.add(member_article)
        response = user_client.get(_detail(member_article.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["linked_cases"] == []

    def test_the_count_stays_honest(self, user_client, hidden_case, member_article):
        """Zero rows and a count of one is the page saying "there is a ticket
        here and it is not yours". Better than a number that quietly means
        something different for each reader."""
        hidden_case.solutions.add(member_article)
        response = user_client.get(_detail(member_article.pk))
        assert response.data["case_count"] == 1

    def test_an_admin_still_sees_it(self, admin_client, hidden_case, member_article):
        hidden_case.solutions.add(member_article)
        response = admin_client.get(_detail(member_article.pk))
        linked = response.data["linked_cases"]
        assert [row["name"] for row in linked] == ["Payroll export fails"]

    def test_linked_cases_carries_no_case_body(
        self, admin_client, hidden_case, member_article
    ):
        """It used to be the whole `CaseSerializer`. A rail of links needs
        five fields; the description, account and contacts were never for
        this endpoint to publish."""
        hidden_case.solutions.add(member_article)
        row = admin_client.get(_detail(member_article.pk)).data["linked_cases"][0]
        assert set(row) == {"id", "name", "status", "priority", "created_at"}

    def test_a_member_may_link_to_a_case_they_do_own(
        self, user_client, org_a, user_profile, member_article
    ):
        _set_rls(org_a)
        case = Case.objects.create(name="Their own ticket", status="New", org=org_a)
        case.assigned_to.add(user_profile)
        response = user_client.post(
            _link(case.pk), {"solution_id": str(member_article.pk)}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert case.solutions.count() == 1


@pytest.mark.django_db
class TestMissingAndMalformedIds:
    @pytest.mark.parametrize(
        "pk",
        ["not-a-uuid", "1", "00000000-0000-0000-0000-000000000001"],
        ids=["malformed", "numeric", "well-formed-but-absent"],
    )
    def test_every_verb_answers_404(self, admin_client, pk):
        assert admin_client.get(_detail(pk)).status_code == status.HTTP_404_NOT_FOUND
        assert (
            admin_client.patch(_detail(pk), {"title": "x"}, format="json").status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            admin_client.put(
                _detail(pk), {"title": "x", "description": "y"}, format="json"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert admin_client.delete(_detail(pk)).status_code == status.HTTP_404_NOT_FOUND
        assert admin_client.post(_publish(pk)).status_code == status.HTTP_404_NOT_FOUND
        assert (
            admin_client.post(_unpublish(pk)).status_code == status.HTTP_404_NOT_FOUND
        )

    def test_a_malformed_solution_id_on_the_link_endpoint_is_a_404(
        self, admin_client, case_a
    ):
        response = admin_client.post(
            _link(case_a.pk), {"solution_id": "not-a-uuid"}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_orgs_article_is_a_404(self, admin_client, org_b, user_b):
        article = _article(org_b, user_b)
        _set_rls(org_b)
        assert (
            admin_client.get(_detail(article.pk)).status_code
            == status.HTTP_404_NOT_FOUND
        )


@pytest.mark.django_db
class TestTheListPayload:
    def test_totals_describe_the_whole_knowledge_base(
        self, admin_client, org_a, admin_user
    ):
        _article(org_a, admin_user, title="One", status="draft")
        _article(org_a, admin_user, title="Two", status="reviewed")
        _article(org_a, admin_user, title="Three", status="approved")
        _article(org_a, admin_user, title="Four", status="approved", is_published=True)

        totals = admin_client.get(SOLUTIONS_URL).data["totals"]
        assert totals == {
            "count": 4,
            "published": 1,
            "draft": 1,
            "reviewed": 1,
            "approved_unpublished": 1,
        }

    def test_totals_ignore_the_filter(self, admin_client, org_a, admin_user):
        """The four numbers are a partition of the KB, so recomputing them
        inside `?status=draft` would leave three cards reading zero."""
        _article(org_a, admin_user, title="One", status="draft")
        _article(org_a, admin_user, title="Two", status="approved")

        response = admin_client.get(f"{SOLUTIONS_URL}?status=draft")
        assert len(response.data["results"]) == 1
        assert response.data["totals"]["count"] == 2

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
    def test_every_spelling_of_true_filters_to_published(
        self, admin_client, org_a, admin_user, value
    ):
        _article(org_a, admin_user, title="Live", status="approved", is_published=True)
        _article(org_a, admin_user, title="Internal", status="draft")

        response = admin_client.get(f"{SOLUTIONS_URL}?is_published={value}")
        assert [row["title"] for row in response.data["results"]] == ["Live"]

    @pytest.mark.parametrize("value", ["false", "0", "no"])
    def test_every_spelling_of_false_filters_to_unpublished(
        self, admin_client, org_a, admin_user, value
    ):
        _article(org_a, admin_user, title="Live", status="approved", is_published=True)
        _article(org_a, admin_user, title="Internal", status="draft")

        response = admin_client.get(f"{SOLUTIONS_URL}?is_published={value}")
        assert [row["title"] for row in response.data["results"]] == ["Internal"]

    def test_the_author_is_a_name(self, admin_client, member_article):
        row = admin_client.get(SOLUTIONS_URL).data["results"][0]
        assert row["author"] == "user"  # `User.name` from the email local part

    def test_the_list_does_not_cost_a_query_per_row(
        self, admin_client, org_a, admin_user, django_assert_max_num_queries
    ):
        """`case_count` and the nested `org` were both computed per row: 13
        articles, 27 queries. The ceiling here is the page's fixed cost.
        Auth, the count, the page, and does not move with the row count."""
        for index in range(12):
            _article(org_a, admin_user, title=f"Article {index}")

        # An explicit `limit` above the default page size, so the ceiling is
        # measured against a full page rather than the first ten rows.
        with django_assert_max_num_queries(10):
            response = admin_client.get(f"{SOLUTIONS_URL}?limit=12")
        assert len(response.data["results"]) == 12

    def test_case_count_is_right_with_the_annotation(
        self, admin_client, case_a, case_b_same_org, member_article
    ):
        member_article.cases.add(case_a, case_b_same_org)
        row = admin_client.get(SOLUTIONS_URL).data["results"][0]
        assert row["case_count"] == 2

    def test_newest_edit_first(self, admin_client, org_a, admin_user):
        _article(org_a, admin_user, title="Older")
        newer = _article(org_a, admin_user, title="Newer")
        titles = [
            row["title"] for row in admin_client.get(SOLUTIONS_URL).data["results"]
        ]
        assert titles[0] == newer.title
