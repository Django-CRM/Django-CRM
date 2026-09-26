"""Tests for Tier-3 parent/child cases (problem → incident linkage).

Covers ``Case.clean()`` guards, ``CaseSerializer`` derived fields, the
tree/link/close-with-children endpoints, and the post_save audit trail.
"""

from __future__ import annotations

import pytest
from crum import impersonate
from django.core.exceptions import ValidationError

from cases.models import Case
from common.models import Activity
from conftest import rls_org


def _make_case(
    org,
    creator,
    *,
    name="Sample case",
    status_value="New",
    priority="Normal",
    parent=None,
    is_problem=False,
    closed_on=None,
):
    with impersonate(creator):
        # Seeded into another tenant: the insert-check policy compares
        # against `app.current_org`, so writing this row means being that
        # tenant for the length of the write.
        with rls_org(org):
            return Case.objects.create(
                name=name,
                status=status_value,
                priority=priority,
                org=org,
                parent=parent,
                is_problem=is_problem,
                closed_on=closed_on,
            )


def _activity_for(case, action):
    return Activity.objects.filter(entity_type="Case", entity_id=case.id, action=action)


@pytest.mark.django_db
class TestCleanGuards:
    def test_self_parent_rejected(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, name="Self loop")
        case.parent = case
        with pytest.raises(ValidationError):
            case.clean()

    def test_cycle_rejected(self, admin_user, org_a):
        a = _make_case(org_a, admin_user, name="A")
        b = _make_case(org_a, admin_user, name="B", parent=a)
        # a.parent = b would create a cycle.
        a.parent = b
        with pytest.raises(ValidationError):
            a.clean()

    def test_depth_3_allowed(self, admin_user, org_a):
        root = _make_case(org_a, admin_user, name="Root", is_problem=True)
        mid = _make_case(org_a, admin_user, name="Mid", parent=root)
        leaf = _make_case(org_a, admin_user, name="Leaf", parent=mid)
        # full_clean works without raising for valid 3-level tree.
        leaf.full_clean(exclude={"closed_on", "case_type"})

    def test_depth_4_rejected_via_clean(self, admin_user, org_a):
        a = _make_case(org_a, admin_user, name="L1", is_problem=True)
        b = _make_case(org_a, admin_user, name="L2", parent=a)
        c = _make_case(org_a, admin_user, name="L3", parent=b)
        d = _make_case(org_a, admin_user, name="L4")
        d.parent = c
        with pytest.raises(ValidationError):
            d.clean()

    def test_duplicate_parent_rejected(self, admin_user, org_a):
        merged = _make_case(org_a, admin_user, name="Merged", status_value="Duplicate")
        case = _make_case(org_a, admin_user, name="Live")
        case.parent = merged
        with pytest.raises(ValidationError):
            case.clean()


@pytest.mark.django_db
class TestSerializer:
    def test_parent_summary_and_child_count(self, admin_user, admin_profile, org_a):
        from cases.serializer import CaseSerializer, parent_access_context

        root = _make_case(org_a, admin_user, name="Root", is_problem=True)
        _make_case(org_a, admin_user, name="C1", parent=root)
        _make_case(org_a, admin_user, name="C2", parent=root)
        # Reload so children manager picks up.
        root.refresh_from_db()

        data = CaseSerializer(root).data
        assert data["child_count"] == 2
        assert data["parent_summary"] is None
        assert data["is_problem"] is True

        c1 = root.children.first()
        c1_data = CaseSerializer(
            c1, context=parent_access_context(admin_profile, [c1])
        ).data
        assert c1_data["parent_summary"] == {
            "id": str(root.id),
            "name": root.name,
            "status": root.status,
            "restricted": False,
        }
        assert c1_data["child_count"] == 0


@pytest.mark.django_db
class TestTreeEndpoint:
    def test_returns_root_with_descendants_from_any_node(
        self, admin_client, admin_user, org_a
    ):
        root = _make_case(org_a, admin_user, name="Root", is_problem=True)
        mid = _make_case(org_a, admin_user, name="Mid", parent=root)
        leaf = _make_case(org_a, admin_user, name="Leaf", parent=mid)
        # Hitting the leaf should still surface the full tree, with focus.
        resp = admin_client.get(f"/api/cases/{leaf.id}/tree/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["focus_id"] == str(leaf.id)
        assert body["root"]["id"] == str(root.id)
        assert body["root"]["children"][0]["id"] == str(mid.id)
        assert body["root"]["children"][0]["children"][0]["id"] == str(leaf.id)

    def test_truncates_below_max_depth(self, admin_client, admin_user, org_a):
        # Force a hand-built 4-deep chain for the truncation flag (model
        # validation would normally block this, but the endpoint must still
        # cap the recursion).
        a = _make_case(org_a, admin_user, name="A", is_problem=True)
        b = _make_case(org_a, admin_user, name="B", parent=a)
        c = _make_case(org_a, admin_user, name="C", parent=b)
        d = _make_case(org_a, admin_user, name="D")
        Case.objects.filter(id=d.id).update(parent=c)
        resp = admin_client.get(f"/api/cases/{a.id}/tree/")
        body = resp.json()
        # Walk down to the deepest node we expose.
        node = body["root"]
        for _ in range(3):
            node = node["children"][0]
        assert node.get("truncated") is True

    def test_cross_org_404(self, admin_client, user_b, org_b):
        other = _make_case(org_b, user_b, name="Cross")
        resp = admin_client.get(f"/api/cases/{other.id}/tree/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestLinkEndpoint:
    def test_link_parent_records_activity(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        parent = _make_case(org_a, admin_user, name="Parent", is_problem=True)
        child = _make_case(org_a, admin_user, name="Child")
        resp = admin_client.post(
            f"/api/cases/{child.id}/link/",
            {"parent_id": str(parent.id)},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        child.refresh_from_db()
        assert child.parent_id == parent.id
        assert _activity_for(child, "LINKED_PARENT").count() == 1
        # Audit row carries parent_id in metadata.
        a = _activity_for(child, "LINKED_PARENT").first()
        assert a.metadata["parent_id"] == str(parent.id)

    def test_unlink_clears_parent(self, admin_client, admin_user, admin_profile, org_a):
        parent = _make_case(org_a, admin_user, name="P")
        child = _make_case(org_a, admin_user, name="C", parent=parent)
        resp = admin_client.post(
            f"/api/cases/{child.id}/link/", {"parent_id": None}, format="json"
        )
        assert resp.status_code == 200, resp.content
        child.refresh_from_db()
        assert child.parent_id is None
        assert _activity_for(child, "UNLINKED_PARENT").count() == 1

    def test_self_link_rejected(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, name="Self")
        resp = admin_client.post(
            f"/api/cases/{case.id}/link/",
            {"parent_id": str(case.id)},
            format="json",
        )
        assert resp.status_code == 400

    def test_cycle_rejected(self, admin_client, admin_user, org_a):
        a = _make_case(org_a, admin_user, name="A")
        b = _make_case(org_a, admin_user, name="B", parent=a)
        # Now try to make a a child of b → cycle.
        resp = admin_client.post(
            f"/api/cases/{a.id}/link/",
            {"parent_id": str(b.id)},
            format="json",
        )
        assert resp.status_code == 400

    def test_cross_org_link_rejected(
        self, admin_client, admin_user, user_b, org_a, org_b
    ):
        local = _make_case(org_a, admin_user, name="Local")
        foreign = _make_case(org_b, user_b, name="Foreign")
        resp = admin_client.post(
            f"/api/cases/{local.id}/link/",
            {"parent_id": str(foreign.id)},
            format="json",
        )
        assert resp.status_code == 400

    def test_depth_overflow_rejected(self, admin_client, admin_user, org_a):
        a = _make_case(org_a, admin_user, name="A", is_problem=True)
        b = _make_case(org_a, admin_user, name="B", parent=a)
        c = _make_case(org_a, admin_user, name="C", parent=b)
        d = _make_case(org_a, admin_user, name="D")
        resp = admin_client.post(
            f"/api/cases/{d.id}/link/",
            {"parent_id": str(c.id)},
            format="json",
        )
        assert resp.status_code == 400

    def test_link_to_duplicate_rejected(self, admin_client, admin_user, org_a):
        merged = _make_case(org_a, admin_user, name="Merged", status_value="Duplicate")
        case = _make_case(org_a, admin_user, name="Active")
        resp = admin_client.post(
            f"/api/cases/{case.id}/link/",
            {"parent_id": str(merged.id)},
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestCloseWithChildren:
    def test_no_cascade_by_default(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        parent = _make_case(org_a, admin_user, name="P", is_problem=True)
        child = _make_case(org_a, admin_user, name="C", parent=parent)
        resp = admin_client.post(
            f"/api/cases/{parent.id}/close-with-children/",
            {"resolution_comment": "fixed"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.status == "Closed"
        assert child.status == "New"
        assert resp.json()["cascaded_case_ids"] == []

    def test_cascade_override_true_closes_descendants(
        self, admin_client, admin_user, org_a
    ):
        parent = _make_case(org_a, admin_user, name="P", is_problem=True)
        c1 = _make_case(org_a, admin_user, name="C1", parent=parent)
        c2 = _make_case(org_a, admin_user, name="C2", parent=parent)
        resp = admin_client.post(
            f"/api/cases/{parent.id}/close-with-children/",
            {"resolution_comment": "rolled out fix", "cascade": True},
            format="json",
        )
        assert resp.status_code == 200
        parent.refresh_from_db()
        c1.refresh_from_db()
        c2.refresh_from_db()
        assert parent.status == "Closed"
        assert c1.status == "Closed"
        assert c2.status == "Closed"
        assert _activity_for(c1, "PARENT_CLOSED_CASCADE").count() == 1
        assert _activity_for(c2, "PARENT_CLOSED_CASCADE").count() == 1
        ids = set(resp.json()["cascaded_case_ids"])
        assert ids == {str(c1.id), str(c2.id)}

    def test_org_default_drives_cascade(self, admin_client, admin_user, org_a):
        org_a.auto_close_children_on_parent_close = True
        org_a.save(update_fields=["auto_close_children_on_parent_close"])
        parent = _make_case(org_a, admin_user, name="P", is_problem=True)
        child = _make_case(org_a, admin_user, name="C", parent=parent)
        resp = admin_client.post(
            f"/api/cases/{parent.id}/close-with-children/",
            {"resolution_comment": "auto"},
            format="json",
        )
        assert resp.status_code == 200
        child.refresh_from_db()
        assert child.status == "Closed"

    def test_already_closed_descendants_skipped(self, admin_client, admin_user, org_a):
        from datetime import date

        parent = _make_case(org_a, admin_user, name="P", is_problem=True)
        already = _make_case(
            org_a,
            admin_user,
            name="Already",
            parent=parent,
            status_value="Closed",
            closed_on=date(2024, 1, 1),
        )
        resp = admin_client.post(
            f"/api/cases/{parent.id}/close-with-children/",
            {"cascade": True},
            format="json",
        )
        assert resp.status_code == 200
        # Already-closed case must NOT receive a cascade activity row.
        assert _activity_for(already, "PARENT_CLOSED_CASCADE").count() == 0
        assert resp.json()["cascaded_case_ids"] == []

    def test_duplicate_parent_rejected(self, admin_client, admin_user, org_a):
        merged = _make_case(org_a, admin_user, name="Merged", status_value="Duplicate")
        resp = admin_client.post(
            f"/api/cases/{merged.id}/close-with-children/",
            {"cascade": True},
            format="json",
        )
        assert resp.status_code == 400
