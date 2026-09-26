"""Object-level authorization on the parent/child endpoints (D50).

`CaseTreeView`, `CaseLinkParentView` and `CaseCloseWithChildrenView` used to
filter by org and nothing else. Any member could read the names of tickets they
cannot open through the tree, link or unlink any ticket, and close any ticket
together with its whole subtree, skipping the approval gate on the way.

The rules they follow now are the ones in `cases.access`:

* tree: read on the ticket asked about; unreadable nodes come back redacted;
* link: write on the child, read on the new parent (a hidden parent answers
  like a missing one); unlink: write on the child;
* close with children: write on the ticket and on every descendant the
  cascade would close, and `close_refusal` passes on each. All or nothing.

`user_client` is a plain member (role USER). `admin_user` creates the tickets
that member is not supposed to reach.
"""

from __future__ import annotations

import uuid

import pytest
from crum import impersonate
from django.utils import timezone

from cases.approvals import ApprovalRule
from cases.models import Case, CaseWatcher
from common.models import Activity
from conftest import rls_org


def _case(org, creator, name, *, parent=None, priority="Normal", status="New"):
    with impersonate(creator):
        with rls_org(org):
            return Case.objects.create(
                name=name,
                status=status,
                priority=priority,
                org=org,
                parent=parent,
                closed_on=timezone.localdate() if status == "Closed" else None,
            )


def _watch(case, profile):
    CaseWatcher.objects.create(case=case, profile=profile, org=case.org)


def _statuses(*cases):
    return [Case.objects.get(pk=c.pk).status for c in cases]


# --------------------------------------------------------------------------- #
# GET /api/cases/<pk>/tree/
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestTreeAccess:
    def test_member_without_read_is_refused(
        self, user_client, admin_user, user_profile, org_a
    ):
        root = _case(org_a, admin_user, "Admin root")
        _case(org_a, admin_user, "Admin child", parent=root)
        resp = user_client.get(f"/api/cases/{root.id}/tree/")
        # The ticket detail GET answers a hidden in-org ticket 403 too.
        assert resp.status_code == 403
        assert b"Admin child" not in resp.content
        assert b"Admin root" not in resp.content

    def test_detail_get_answers_the_same(self, user_client, admin_user, org_a):
        root = _case(org_a, admin_user, "Admin root")
        assert user_client.get(f"/api/cases/{root.id}/").status_code == 403

    def test_watcher_may_read_the_tree(
        self, user_client, admin_user, user_profile, org_a
    ):
        root = _case(org_a, admin_user, "Watched root")
        _watch(root, user_profile)
        resp = user_client.get(f"/api/cases/{root.id}/tree/")
        assert resp.status_code == 200, resp.content
        assert resp.json()["root"]["name"] == "Watched root"

    def test_hidden_descendant_is_redacted(
        self, user_client, admin_user, regular_user, org_a
    ):
        root = _case(org_a, regular_user, "My root")
        hidden = _case(org_a, admin_user, "Secret payroll ticket", parent=root)
        mine = _case(org_a, regular_user, "My grandchild", parent=hidden)

        resp = user_client.get(f"/api/cases/{mine.id}/tree/")
        assert resp.status_code == 200, resp.content
        assert b"Secret payroll ticket" not in resp.content

        body = resp.json()
        assert body["root"]["name"] == "My root"
        node = body["root"]["children"][0]
        assert node["id"] == str(hidden.id)
        assert node["restricted"] is True
        assert node["name"] is None
        assert "priority" not in node and "assigned_to" not in node
        # Shape kept: the readable grandchild still sits under it.
        assert node["children"][0]["name"] == "My grandchild"
        assert "restricted" not in node["children"][0]

    def test_admin_sees_every_name(self, admin_client, regular_user, org_a):
        root = _case(org_a, regular_user, "Member root")
        _case(org_a, regular_user, "Member child", parent=root)
        resp = admin_client.get(f"/api/cases/{root.id}/tree/")
        assert resp.status_code == 200
        child = resp.json()["root"]["children"][0]
        assert child["name"] == "Member child"
        assert "restricted" not in child

    def test_other_org_is_404(self, org_b_client, admin_user, org_a):
        root = _case(org_a, admin_user, "Org A root")
        assert org_b_client.get(f"/api/cases/{root.id}/tree/").status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/cases/<pk>/link/
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestLinkAccess:
    def test_member_cannot_link_a_ticket_they_cannot_write(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        child = _case(org_a, admin_user, "Admin child")
        # Watching grants read, not write.
        _watch(child, user_profile)
        parent = _case(org_a, regular_user, "My parent")
        resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": str(parent.id)}, format="json"
        )
        assert resp.status_code == 403
        child.refresh_from_db()
        assert child.parent_id is None
        assert (
            not Activity.objects.filter(entity_id=child.id)
            .filter(action="LINKED_PARENT")
            .exists()
        )

    def test_member_cannot_unlink_a_ticket_they_cannot_write(
        self, user_client, admin_user, org_a
    ):
        parent = _case(org_a, admin_user, "Admin parent")
        child = _case(org_a, admin_user, "Admin child", parent=parent)
        resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": None}, format="json"
        )
        assert resp.status_code == 403
        child.refresh_from_db()
        assert child.parent_id == parent.id

    def test_hidden_parent_answers_like_a_missing_one(
        self, user_client, admin_user, regular_user, org_a
    ):
        child = _case(org_a, regular_user, "My child")
        hidden = _case(org_a, admin_user, "Secret parent")
        hidden_resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": str(hidden.id)}, format="json"
        )
        missing_resp = user_client.post(
            f"/api/cases/{child.id}/link/",
            {"parent_id": str(uuid.uuid4())},
            format="json",
        )
        assert hidden_resp.status_code == missing_resp.status_code == 400
        assert hidden_resp.json() == missing_resp.json()
        assert b"Secret parent" not in hidden_resp.content
        child.refresh_from_db()
        assert child.parent_id is None

    def test_malformed_parent_id_is_a_missing_parent(
        self, user_client, regular_user, org_a
    ):
        child = _case(org_a, regular_user, "My child")
        resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": "not-a-uuid"}, format="json"
        )
        assert resp.status_code == 400
        assert "parent_id" in resp.json()

    def test_writer_links_under_a_readable_parent(
        self, user_client, admin_user, regular_user, user_profile, org_a
    ):
        child = _case(org_a, regular_user, "My child")
        parent = _case(org_a, admin_user, "Watched parent")
        _watch(parent, user_profile)
        resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": str(parent.id)}, format="json"
        )
        assert resp.status_code == 200, resp.content
        child.refresh_from_db()
        assert child.parent_id == parent.id

    def test_writer_unlinks(self, user_client, admin_user, regular_user, org_a):
        parent = _case(org_a, admin_user, "Admin parent")
        child = _case(org_a, regular_user, "My child", parent=parent)
        resp = user_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": None}, format="json"
        )
        assert resp.status_code == 200, resp.content
        child.refresh_from_db()
        assert child.parent_id is None

    def test_admin_links_any_ticket(self, admin_client, regular_user, org_a):
        child = _case(org_a, regular_user, "Member child")
        parent = _case(org_a, regular_user, "Member parent")
        resp = admin_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": str(parent.id)}, format="json"
        )
        assert resp.status_code == 200, resp.content
        child.refresh_from_db()
        assert child.parent_id == parent.id

    def test_other_org_is_404(self, org_b_client, admin_user, org_a):
        child = _case(org_a, admin_user, "Org A child")
        resp = org_b_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": None}, format="json"
        )
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/cases/<pk>/close-with-children/
# --------------------------------------------------------------------------- #


def _close(client, case, cascade=True):
    return client.post(
        f"/api/cases/{case.id}/close-with-children/",
        {"cascade": cascade, "resolution_comment": "done"},
        format="json",
    )


@pytest.mark.django_db
class TestCloseWithChildrenAccess:
    def test_member_cannot_close_a_ticket_they_cannot_write(
        self, user_client, admin_user, user_profile, org_a
    ):
        root = _case(org_a, admin_user, "Admin root")
        _watch(root, user_profile)
        child = _case(org_a, admin_user, "Admin child", parent=root)
        resp = _close(user_client, root)
        assert resp.status_code == 403
        assert _statuses(root, child) == ["New", "New"]

    def test_one_unwritable_descendant_refuses_the_whole_close(
        self, user_client, admin_user, regular_user, org_a
    ):
        root = _case(org_a, regular_user, "My root")
        mine = _case(org_a, regular_user, "My child", parent=root)
        hidden = _case(org_a, admin_user, "Secret sibling", parent=root)
        resp = _close(user_client, root)
        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert body["error"] is True
        assert "not yours to close" in body["errors"]
        assert b"Secret sibling" not in resp.content
        assert _statuses(root, mine, hidden) == ["New", "New", "New"]
        assert not Activity.objects.filter(action="PARENT_CLOSED_CASCADE").exists()

    def test_member_may_close_their_ticket_alone(
        self, user_client, admin_user, regular_user, org_a
    ):
        # Without the cascade, the unwritable child is not touched and does
        # not stand in the way.
        root = _case(org_a, regular_user, "My root")
        hidden = _case(org_a, admin_user, "Admin child", parent=root)
        resp = _close(user_client, root, cascade=False)
        assert resp.status_code == 200, resp.content
        assert _statuses(root, hidden) == ["Closed", "New"]

    def test_approval_required_on_a_descendant_refuses(
        self, admin_client, admin_user, org_a
    ):
        root = _case(org_a, admin_user, "Root", priority="Low")
        gated = _case(org_a, admin_user, "Urgent child", parent=root, priority="Urgent")
        ApprovalRule.objects.create(
            name="Urgent needs sign-off",
            org=org_a,
            trigger_event="pre_close",
            match_priority="Urgent",
        )
        resp = _close(admin_client, root)
        assert resp.status_code == 400, resp.content
        message = resp.json()["errors"]
        assert "approval" in message and "Urgent child" in message
        assert _statuses(root, gated) == ["New", "New"]

    def test_approval_required_on_the_ticket_itself_refuses(
        self, admin_client, admin_user, org_a
    ):
        root = _case(org_a, admin_user, "Urgent root", priority="Urgent")
        ApprovalRule.objects.create(
            name="Urgent needs sign-off",
            org=org_a,
            trigger_event="pre_close",
            match_priority="Urgent",
        )
        resp = _close(admin_client, root, cascade=False)
        assert resp.status_code == 400, resp.content
        assert "approval" in resp.json()["errors"]["status"][0]
        assert _statuses(root) == ["New"]

    def test_allowed_close_stamps_every_ticket(self, user_client, regular_user, org_a):
        root = _case(org_a, regular_user, "My root")
        child = _case(org_a, regular_user, "My child", parent=root)
        grandchild = _case(org_a, regular_user, "My grandchild", parent=child)
        resp = _close(user_client, root)
        assert resp.status_code == 200, resp.content
        assert set(resp.json()["cascaded_case_ids"]) == {
            str(child.id),
            str(grandchild.id),
        }
        today = timezone.localdate()
        for c in (root, child, grandchild):
            c.refresh_from_db()
            assert c.status == "Closed"
            assert c.closed_on == today
            assert c.resolved_at is not None

    def test_admin_closes_everybodys_subtree(self, admin_client, regular_user, org_a):
        root = _case(org_a, regular_user, "Member root")
        child = _case(org_a, regular_user, "Member child", parent=root)
        resp = _close(admin_client, root)
        assert resp.status_code == 200, resp.content
        assert _statuses(root, child) == ["Closed", "Closed"]

    def test_stored_cycle_does_not_hang(self, admin_client, admin_user, org_a):
        a = _case(org_a, admin_user, "A")
        b = _case(org_a, admin_user, "B", parent=a)
        Case.objects.filter(pk=a.pk).update(parent=b)
        resp = _close(admin_client, a)
        assert resp.status_code == 200, resp.content
        assert resp.json()["cascaded_case_ids"] == [str(b.id)]

    def test_other_org_is_404(self, org_b_client, admin_user, org_a):
        root = _case(org_a, admin_user, "Org A root")
        assert _close(org_b_client, root).status_code == 404
        assert _statuses(root) == ["New"]
