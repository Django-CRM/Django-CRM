"""`parent_summary` follows the ticket read rule on the parent (D51).

`CaseSerializer.get_parent_summary` used to return the parent's id, name and
status to anybody who could read the child. Reading a child is not reading its
parent: a member assigned one sub-ticket learned the subject of a ticket the
detail view answers 403 for. A parent the viewer cannot open now comes back as
`{id, name: None, status: None, restricted: True}`, the shape `/tree/` uses for
a hidden node.

The create/update serializer had the matching write-side gap: `parent` was
checked by org alone, so a member could file a ticket under a parent they
cannot open, the same link `/link/` refuses, and learn from the difference
between a 400 and a 200 that the hidden ticket exists.

`user_client` is a plain member (role USER) whose user is `regular_user`.
"""

from __future__ import annotations

import uuid

import pytest
from crum import impersonate
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cases.models import Case, CaseWatcher
from cases.serializer import PARENT_NOT_FOUND, CaseSerializer
from conftest import rls_org

SECRET = "Secret payroll ticket"
REDACTED = {"name": None, "status": None, "restricted": True}


def _case(org, creator, name, *, parent=None):
    with impersonate(creator):
        with rls_org(org):
            return Case.objects.create(
                name=name, status="New", priority="Normal", org=org, parent=parent
            )


def _row(resp, case):
    return next(r for r in resp.json()["cases"] if r["id"] == str(case.id))


@pytest.mark.django_db
class TestDetail:
    def test_member_who_cannot_read_the_parent_gets_it_redacted(
        self, user_client, admin_user, regular_user, org_a
    ):
        parent = _case(org_a, admin_user, SECRET)
        child = _case(org_a, regular_user, "My sub-ticket", parent=parent)
        # The parent really is hidden from this member.
        assert user_client.get(f"/api/cases/{parent.id}/").status_code == 403

        resp = user_client.get(f"/api/cases/{child.id}/")
        assert resp.status_code == 200, resp.content
        assert resp.json()["cases_obj"]["parent_summary"] == {
            "id": str(parent.id),
            **REDACTED,
        }
        assert SECRET.encode() not in resp.content

    def test_member_who_can_read_the_parent_gets_it_in_full(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        parent = _case(org_a, admin_user, "Watched parent")
        CaseWatcher.objects.create(case=parent, profile=user_profile, org=org_a)
        child = _case(org_a, regular_user, "My sub-ticket", parent=parent)

        resp = user_client.get(f"/api/cases/{child.id}/")
        assert resp.status_code == 200, resp.content
        assert resp.json()["cases_obj"]["parent_summary"] == {
            "id": str(parent.id),
            "name": "Watched parent",
            "status": "New",
            "restricted": False,
        }

    def test_admin_gets_every_parent_in_full(
        self, admin_client, admin_user, regular_user, org_a
    ):
        parent = _case(org_a, regular_user, "Member's parent")
        child = _case(org_a, admin_user, "Child", parent=parent)

        summary = admin_client.get(f"/api/cases/{child.id}/").json()["cases_obj"][
            "parent_summary"
        ]
        assert summary["name"] == "Member's parent"
        assert summary["restricted"] is False

    def test_a_serializer_given_no_viewer_redacts(self, admin_user, org_a):
        # Fail closed: a call site that forgets the context leaks nothing.
        parent = _case(org_a, admin_user, SECRET)
        child = _case(org_a, admin_user, "Child", parent=parent)
        assert CaseSerializer(child).data["parent_summary"] == {
            "id": str(parent.id),
            **REDACTED,
        }


@pytest.mark.django_db
class TestList:
    def test_each_row_is_judged_on_its_own_parent(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        hidden = _case(org_a, admin_user, SECRET)
        mine = _case(org_a, regular_user, "My own parent")
        under_hidden = _case(org_a, regular_user, "Under hidden", parent=hidden)
        under_mine = _case(org_a, regular_user, "Under mine", parent=mine)

        resp = user_client.get("/api/cases/")
        assert resp.status_code == 200, resp.content
        assert _row(resp, under_hidden)["parent_summary"] == {
            "id": str(hidden.id),
            **REDACTED,
        }
        assert _row(resp, under_mine)["parent_summary"]["name"] == "My own parent"
        assert _row(resp, under_mine)["parent_summary"]["restricted"] is False
        assert SECRET.encode() not in resp.content

    def test_parents_cost_one_query_per_page_not_one_per_row(
        self, user_client, admin_user, regular_user, org_a
    ):
        # Every case exists before the first request, so both requests list
        # the same rows; the readable parents are the member's own tickets and
        # are on the list either way. Only the links change in between.
        children = [_case(org_a, regular_user, f"Child {i}") for i in range(6)]
        parents = [
            _case(org_a, admin_user if i % 2 else regular_user, f"Parent {i}")
            for i in range(6)
        ]
        with CaptureQueriesContext(connection) as unlinked:
            assert user_client.get("/api/cases/").status_code == 200

        for child, parent in zip(children, parents):
            Case.objects.filter(pk=child.pk).update(parent=parent)

        with CaptureQueriesContext(connection) as linked:
            resp = user_client.get("/api/cases/")
        assert resp.status_code == 200
        rows = [_row(resp, c)["parent_summary"] for c in children]
        assert [r["restricted"] for r in rows] == [False, True] * 3
        # Six parents, half of them hidden, and one extra query for all of
        # them: the batch lookup of which parent ids the member may read.
        assert len(linked) - len(unlinked) <= 1


@pytest.mark.django_db
class TestLinkingThroughTheTicketForm:
    """`parent` on create and PATCH takes the rule `/link/` takes."""

    def test_a_hidden_parent_is_refused_like_a_missing_one(
        self, user_client, admin_user, regular_user, org_a
    ):
        hidden = _case(org_a, admin_user, SECRET)
        mine = _case(org_a, regular_user, "Mine")

        refused = user_client.patch(
            f"/api/cases/{mine.id}/", {"parent": str(hidden.id)}, format="json"
        )
        missing = user_client.patch(
            f"/api/cases/{mine.id}/", {"parent": str(uuid.uuid4())}, format="json"
        )
        assert refused.status_code == missing.status_code == 400
        assert refused.json()["errors"]["parent"] == [PARENT_NOT_FOUND]
        assert missing.json()["errors"]["parent"] == [PARENT_NOT_FOUND]
        assert Case.objects.get(pk=mine.pk).parent_id is None

    def test_a_hidden_parent_is_refused_on_create(self, user_client, admin_user, org_a):
        hidden = _case(org_a, admin_user, SECRET)
        resp = user_client.post(
            "/api/cases/",
            {
                "name": "New one",
                "status": "New",
                "priority": "Normal",
                "parent": str(hidden.id),
            },
            format="json",
        )
        assert resp.status_code == 400, resp.content
        assert SECRET.encode() not in resp.content
        assert not Case.objects.filter(name="New one").exists()

    def test_a_readable_parent_is_accepted(self, user_client, regular_user, org_a):
        parent = _case(org_a, regular_user, "My parent")
        mine = _case(org_a, regular_user, "Mine")
        resp = user_client.patch(
            f"/api/cases/{mine.id}/", {"parent": str(parent.id)}, format="json"
        )
        assert resp.status_code == 200, resp.content
        assert Case.objects.get(pk=mine.pk).parent_id == parent.id

    def test_resending_the_current_hidden_parent_is_not_a_new_link(
        self, user_client, admin_user, regular_user, org_a
    ):
        hidden = _case(org_a, admin_user, SECRET)
        mine = _case(org_a, regular_user, "Mine", parent=hidden)
        resp = user_client.patch(
            f"/api/cases/{mine.id}/",
            {"parent": str(hidden.id), "priority": "High"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert SECRET.encode() not in resp.content
        assert Case.objects.get(pk=mine.pk).priority == "High"

    def test_admin_may_link_under_any_parent(
        self, admin_client, admin_user, regular_user, org_a
    ):
        parent = _case(org_a, regular_user, "Member's")
        child = _case(org_a, admin_user, "Admin's")
        resp = admin_client.patch(
            f"/api/cases/{child.id}/", {"parent": str(parent.id)}, format="json"
        )
        assert resp.status_code == 200, resp.content


@pytest.mark.django_db
def test_another_orgs_case_is_refused_like_a_missing_one(
    user_client, regular_user, case_b, org_a
):
    mine = _case(org_a, regular_user, "Mine")
    resp = user_client.patch(
        f"/api/cases/{mine.id}/", {"parent": str(case_b.id)}, format="json"
    )
    assert resp.status_code == 400, resp.content
    assert resp.json()["errors"]["parent"] == [PARENT_NOT_FOUND]
