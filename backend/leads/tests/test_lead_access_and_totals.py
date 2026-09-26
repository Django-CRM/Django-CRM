"""Who may read and write a lead, and the headline counts beside the list.

Both halves exist because the v2 leads pages are wired to this endpoint. The
list needed counts computed over the whole filtered set rather than the page,
and wiring an edit form to `LeadDetailView.put` meant first establishing that
`put` refuses anyone it should.

Every permission test asserts both directions. A check that can only return
False is indistinguishable from a check that is never reached.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from common.models import Tags
from conftest import rls_org
from leads.models import Lead

LEADS_URL = "/api/leads/"


def _detail_url(pk):
    return f"/api/leads/{pk}/"


def _lead(org, **kw):
    # `rls_org` so the tests that seed a second tenant work under a role RLS
    # binds: `lead` carries an insert-check policy against `app.current_org`,
    # and the ambient context is org A.
    with rls_org(org):
        return Lead.objects.create(
            first_name=kw.pop("first_name", "Ada"),
            last_name=kw.pop("last_name", "Lovelace"),
            email=kw.pop("email", "ada@example.com"),
            status=kw.pop("status", "in process"),
            company_name=kw.pop("company_name", "Analytical Engines"),
            org=org,
            **kw,
        )


def _created_by(lead, user):
    """Attribute an existing lead to `user`.

    `BaseModel.save()` sets `created_by` from the thread-local current user and
    ignores whatever the caller passed, so a lead built straight through the
    ORM in a test always lands with `created_by=None`. `.update()` skips
    `save()` and is the only way to stage this case.
    """
    Lead.objects.filter(pk=lead.pk).update(created_by=user)
    lead.refresh_from_db()
    return lead


def _valid_body(**overrides):
    body = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "status": "in process",
    }
    body.update(overrides)
    return body


@pytest.mark.django_db
class TestLeadTotals:
    """`totals` describes the filtered queryset, not the page."""

    def test_count_covers_the_whole_list_not_one_page(self, admin_client, org_a):
        """The page holds 10; the count has to say 24.

        This is the bug the v2 redesign exists to fix: a total reduced over
        the rows the client happens to be holding, printed as though it
        described the list.
        """
        for i in range(24):
            _lead(org_a, email=f"lead{i}@example.com")

        response = admin_client.get(LEADS_URL)

        assert response.status_code == 200
        body = response.json()
        assert len(body["open_leads"]["open_leads"]) == 10
        assert body["totals"]["count"] == 24

    def test_unworked_counts_leads_nobody_touched_in_a_week(self, admin_client, org_a):
        """Contacted 8 days ago counts; contacted yesterday does not."""
        today = timezone.localdate()
        _lead(
            org_a, email="stale@example.com", last_contacted=today - timedelta(days=8)
        )
        _lead(
            org_a, email="fresh@example.com", last_contacted=today - timedelta(days=1)
        )

        totals = admin_client.get(LEADS_URL).json()["totals"]

        assert totals["count"] == 2
        assert totals["unworked_over_a_week"] == 1

    def test_never_contacted_falls_back_to_when_the_lead_arrived(
        self, admin_client, org_a
    ):
        """A lead nobody has contacted is not unworked on the day it lands.

        Both leads below have `last_contacted` unset. Only the one that has
        been sitting there counts.
        """
        old = _lead(org_a, email="old@example.com")
        Lead.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        _lead(org_a, email="new@example.com")

        totals = admin_client.get(LEADS_URL).json()["totals"]

        assert totals["count"] == 2
        assert totals["unworked_over_a_week"] == 1

    def test_totals_exclude_other_orgs(self, admin_client, org_a, org_b):
        """The org filter is already on the queryset; this proves totals use it."""
        _lead(org_a, email="mine@example.com")
        _lead(org_b, email="theirs@example.com")
        _lead(org_b, email="theirs2@example.com")

        totals = admin_client.get(LEADS_URL).json()["totals"]

        assert totals["count"] == 1

    def test_totals_respect_the_non_admin_narrowing(
        self, user_client, admin_client, org_a, user_profile
    ):
        """A non-admin's list is narrowed to their own leads. The count must
        describe that narrowed set, not the org."""
        mine = _lead(org_a, email="mine@example.com")
        mine.assigned_to.add(user_profile)
        _lead(org_a, email="someone-elses@example.com")

        assert user_client.get(LEADS_URL).json()["totals"]["count"] == 1
        assert admin_client.get(LEADS_URL).json()["totals"]["count"] == 2

    def test_totals_respect_an_applied_filter(self, admin_client, org_a):
        """A filtered list gets filtered totals, otherwise the number beside
        the chips contradicts the rows under them."""
        _lead(org_a, email="a@example.com", source="call")
        _lead(org_a, email="b@example.com", source="email")
        _lead(org_a, email="c@example.com", source="email")

        totals = admin_client.get(LEADS_URL, {"source": "email"}).json()["totals"]

        assert totals["count"] == 2


@pytest.mark.django_db
class TestLeadDetailRead:
    """`get_context_data` used to `return Response(...)` on refusal, and
    `get()` wrapped that in a second Response, so the intended 403 rendered
    as a 500. It also appended a User to a list of Profile ids, which meant
    the creator was refused access to their own lead."""

    def test_admin_can_read_any_lead_in_the_org(self, admin_client, org_a):
        lead = _lead(org_a)

        response = admin_client.get(_detail_url(lead.id))

        assert response.status_code == 200
        assert response.json()["lead_obj"]["email"] == "ada@example.com"

    def test_assigned_non_admin_can_read(self, user_client, org_a, user_profile):
        lead = _lead(org_a)
        lead.assigned_to.add(user_profile)

        assert user_client.get(_detail_url(lead.id)).status_code == 200

    def test_creator_can_read_their_own_lead(self, user_client, org_a, regular_user):
        """The branch that was broken by the type mismatch. `created_by` is a
        User FK, so it has to be compared against `profile.user`, and the id
        added to the allow-list has to be the *Profile* id."""
        lead = _created_by(_lead(org_a), regular_user)

        assert user_client.get(_detail_url(lead.id)).status_code == 200

    def test_a_lead_with_no_creator_is_still_readable(self, admin_client, org_a):
        """`created_by` is null on leads that came in through
        `CreateLeadFromSite`. Rendering the mention list dereferenced it
        unguarded, which was a 500 waiting behind the permission check."""
        lead = _lead(org_a)
        assert lead.created_by is None

        assert admin_client.get(_detail_url(lead.id)).status_code == 200

    def test_unrelated_non_admin_is_refused(self, user_client, org_a):
        """Neither assigned nor creator: 404, as for a missing id, not a 500."""
        lead = _lead(org_a)

        assert user_client.get(_detail_url(lead.id)).status_code == 404

    def test_another_org_cannot_read(self, org_b_client, org_a):
        lead = _lead(org_a)

        assert org_b_client.get(_detail_url(lead.id)).status_code == 404


@pytest.mark.django_db
class TestDuplicateEmail:
    """`unique_lead_email_per_org` had no serializer check in front of it, so a
    duplicate reached the database and returned an IntegrityError as a 500,
    with nothing naming the field that caused it."""

    def test_duplicate_email_is_a_400_naming_the_field(self, admin_client, org_a):
        _lead(org_a, email="taken@example.com")
        mine = _lead(org_a, email="mine@example.com")

        response = admin_client.patch(
            _detail_url(mine.id),
            {"email": "taken@example.com"},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "email" in response.json()["errors"]
        mine.refresh_from_db()
        assert mine.email == "mine@example.com"

    def test_the_check_is_case_insensitive_like_the_constraint(
        self, admin_client, org_a
    ):
        """The constraint is on `Lower("email")`. A check that compares exactly
        would let `TAKEN@…` through to the same 500 it was written to prevent."""
        _lead(org_a, email="taken@example.com")
        mine = _lead(org_a, email="mine@example.com")

        response = admin_client.patch(
            _detail_url(mine.id),
            {"email": "TAKEN@EXAMPLE.COM"},
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_a_lead_can_keep_its_own_email(self, admin_client, org_a):
        """The direction that has to keep working: saving a lead without
        changing its address must not collide with itself."""
        mine = _lead(org_a, email="mine@example.com")

        response = admin_client.patch(
            _detail_url(mine.id),
            {"email": "mine@example.com", "job_title": "Head of Operations"},
            content_type="application/json",
        )

        assert response.status_code == 200
        mine.refresh_from_db()
        assert mine.job_title == "Head of Operations"

    def test_another_org_may_use_the_same_email(self, admin_client, org_a, org_b):
        """The constraint is per org. Two tenants having the same person as a
        lead is ordinary, and refusing it would leak that the other org has
        them."""
        _lead(org_b, email="shared@example.com")
        mine = _lead(org_a, email="mine@example.com")

        response = admin_client.patch(
            _detail_url(mine.id),
            {"email": "shared@example.com"},
            content_type="application/json",
        )

        assert response.status_code == 200

    def test_clearing_an_email_is_allowed(self, admin_client, org_a):
        """The constraint is conditional on a non-empty email, so the check
        has to skip empties too: otherwise a second blank-email lead is
        refused for colliding with the first."""
        _lead(org_a, email="")
        mine = _lead(org_a, email="mine@example.com")

        response = admin_client.patch(
            _detail_url(mine.id), {"email": ""}, content_type="application/json"
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestPutClearsRelations:
    """Why the v2 edit form sends PATCH.

    `put` calls `.clear()` on tags, contacts, teams and assigned_to before
    re-adding whatever the body carried, so a form that owns only the scalar
    fields strips all four every time somebody corrects a phone number. This
    is existing behaviour and is left alone. PUT means "replace the whole
    resource" and that reading is defensible. It is pinned here because it is
    load-bearing for the frontend's choice of verb, and a future change that
    makes PUT partial should have to notice this test.
    """

    def test_put_without_tags_clears_them(self, admin_client, org_a, admin_profile):
        lead = _lead(org_a)
        tag = Tags.objects.create(name="Enterprise", org=org_a)
        lead.tags.add(tag)
        lead.assigned_to.add(admin_profile)

        response = admin_client.put(
            _detail_url(lead.id), _valid_body(), content_type="application/json"
        )

        assert response.status_code == 200
        assert lead.tags.count() == 0
        assert lead.assigned_to.count() == 0

    def test_patch_without_tags_leaves_them(self, admin_client, org_a, admin_profile):
        """The same edit over PATCH keeps everything it did not mention."""
        lead = _lead(org_a)
        tag = Tags.objects.create(name="Enterprise", org=org_a)
        lead.tags.add(tag)
        lead.assigned_to.add(admin_profile)

        response = admin_client.patch(
            _detail_url(lead.id),
            {"job_title": "Head of Operations"},
            content_type="application/json",
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.job_title == "Head of Operations"
        assert list(lead.tags.all()) == [tag]
        assert list(lead.assigned_to.all()) == [admin_profile]


@pytest.mark.django_db
class TestLeadDetailWrite:
    """`put` checked the org and nothing else, so any member of the org could
    rewrite any lead in it by id."""

    def test_admin_can_edit_any_lead_in_the_org(self, admin_client, org_a):
        lead = _lead(org_a)

        response = admin_client.put(
            _detail_url(lead.id),
            _valid_body(first_name="Augusta"),
            content_type="application/json",
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.first_name == "Augusta"

    def test_assigned_non_admin_can_edit(self, user_client, org_a, user_profile):
        lead = _lead(org_a)
        lead.assigned_to.add(user_profile)

        response = user_client.put(
            _detail_url(lead.id),
            _valid_body(first_name="Augusta"),
            content_type="application/json",
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.first_name == "Augusta"

    def test_creator_can_edit_their_own_lead(self, user_client, org_a, regular_user):
        lead = _created_by(_lead(org_a), regular_user)

        response = user_client.put(
            _detail_url(lead.id),
            _valid_body(first_name="Augusta"),
            content_type="application/json",
        )

        assert response.status_code == 200

    def test_unrelated_non_admin_cannot_edit(self, user_client, org_a):
        """The gap. This lead is not theirs and the list view does not show it
        to them, but `put` accepted the write anyway."""
        lead = _lead(org_a)

        response = user_client.put(
            _detail_url(lead.id),
            _valid_body(first_name="Augusta"),
            content_type="application/json",
        )

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.first_name == "Ada"

    def test_unrelated_non_admin_cannot_reassign_a_lead_to_themselves(
        self, user_client, org_a, user_profile
    ):
        """The escalation the missing check allowed: take someone else's lead
        by writing your own profile into `assigned_to`."""
        lead = _lead(org_a)

        response = user_client.put(
            _detail_url(lead.id),
            _valid_body(assigned_to=[str(user_profile.id)]),
            content_type="application/json",
        )

        assert response.status_code == 404
        assert user_profile not in lead.assigned_to.all()

    def test_unrelated_non_admin_cannot_force_conversion(self, user_client, org_a):
        """Conversion creates an Account, a Contact and an Opportunity and
        cannot be undone. It was reachable on someone else's lead."""
        lead = _lead(org_a, opportunity_amount="1000.00")

        response = user_client.put(
            _detail_url(lead.id),
            _valid_body(status="converted"),
            content_type="application/json",
        )

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.status == "in process"

    def test_another_org_cannot_edit(self, org_b_client, org_a):
        lead = _lead(org_a)

        response = org_b_client.put(
            _detail_url(lead.id),
            _valid_body(first_name="Augusta"),
            content_type="application/json",
        )

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.first_name == "Ada"


@pytest.mark.django_db
class TestContactCatalogueRespectsRole:
    """The contact picker served beside the lead list.

    Same shape as the `/api/accounts/` leak found in the 2026-08-05 parity
    review, one endpoint over: org-scoped but not role-narrowed, so a member
    could read back the first name of every contact in the org from a payload
    that exists to populate a form. Names only here (`.values("id",
    "first_name")`), which is why this one is smaller than its neighbour, not
    why it should stay.
    """

    def test_member_sees_only_their_own_contacts(
        self, user_client, user_profile, org_a, admin_user
    ):
        from contacts.models import Contact

        mine = Contact.objects.create(first_name="Mine", last_name="C", org=org_a)
        mine.assigned_to.add(user_profile)
        theirs = Contact.objects.create(first_name="Theirs", last_name="C", org=org_a)
        Contact.objects.filter(pk=theirs.pk).update(created_by=admin_user)

        names = {
            row["first_name"] for row in user_client.get(LEADS_URL).json()["contacts"]
        }

        assert "Mine" in names
        assert "Theirs" not in names

    def test_admin_still_sees_the_whole_catalogue(self, admin_client, org_a):
        from contacts.models import Contact

        Contact.objects.create(first_name="One", last_name="C", org=org_a)
        Contact.objects.create(first_name="Two", last_name="C", org=org_a)

        names = {
            row["first_name"] for row in admin_client.get(LEADS_URL).json()["contacts"]
        }

        assert {"One", "Two"} <= names
