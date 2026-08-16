"""
Tests for the Tier-1 audit log: cases/signals.py + Activity API.

Covers the cases-side hooks of the cross-cutting Activity model
(see docs/cases/COORDINATION_DECISIONS.md D1).
"""

from __future__ import annotations

import pytest

from cases.models import Solution
from common.models import Activity, Comment


def _case_activities(case):
    return Activity.objects.filter(entity_type="Case", entity_id=case.pk).order_by(
        "created_at"
    )


@pytest.mark.django_db
class TestCaseSignalActivities:
    def test_create_emits_create_activity(self, admin_client, org_a):
        # Use the API so request middleware sets crum's current user.
        response = admin_client.post(
            "/api/cases/",
            {
                "name": "New audited case",
                "status": "New",
                "priority": "High",
            },
        )
        assert response.status_code == 200
        case_id = response.json()["id"]
        rows = list(
            Activity.objects.filter(entity_type="Case", entity_id=case_id).values_list(
                "action", flat=True
            )
        )
        assert "CREATE" in rows

    def test_status_change_emits_status_changed(self, admin_client, case_a):
        response = admin_client.patch(
            f"/api/cases/{case_a.pk}/",
            {"status": "Pending"},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        rows = _case_activities(case_a)
        status_rows = [r for r in rows if r.action == "STATUS_CHANGED"]
        assert len(status_rows) == 1
        meta = status_rows[0].metadata
        assert meta == {"before": "New", "after": "Pending"}

    def test_priority_change_emits_priority_changed(self, admin_client, case_a):
        response = admin_client.patch(
            f"/api/cases/{case_a.pk}/",
            {"priority": "Urgent"},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        priority_rows = [
            r for r in _case_activities(case_a) if r.action == "PRIORITY_CHANGED"
        ]
        assert len(priority_rows) == 1
        assert priority_rows[0].metadata == {"before": "High", "after": "Urgent"}

    def test_assignment_emits_assign(self, admin_client, case_a, admin_profile):
        # Add the admin profile as an assignee.
        case_a.assigned_to.add(admin_profile)
        rows = [r for r in _case_activities(case_a) if r.action == "ASSIGN"]
        assert len(rows) == 1
        assert rows[0].metadata == {"added": [str(admin_profile.pk)]}

    def test_comment_create_emits_comment(
        self, admin_client, case_a, admin_profile, org_a
    ):
        from django.contrib.contenttypes.models import ContentType

        Comment.objects.create(
            comment="Investigating",
            content_type=ContentType.objects.get_for_model(case_a),
            object_id=case_a.pk,
            commented_by=admin_profile,
            org=org_a,
        )
        rows = [r for r in _case_activities(case_a) if r.action == "COMMENT"]
        assert len(rows) == 1
        meta = rows[0].metadata
        assert "comment_id" in meta
        assert meta["is_internal"] is False

    def test_solution_link_emits_linked_solution(
        self, admin_client, case_a, org_a, admin_user
    ):
        sol = Solution.objects.create(
            title="Reset password",
            description="...",
            org=org_a,
            status="approved",
            is_published=True,
            created_by=admin_user,
        )
        case_a.solutions.add(sol)
        rows = [r for r in _case_activities(case_a) if r.action == "LINKED_SOLUTION"]
        assert len(rows) == 1
        assert rows[0].metadata == {"solution_id": str(sol.pk)}

        case_a.solutions.remove(sol)
        unlinked = [
            r for r in _case_activities(case_a) if r.action == "UNLINKED_SOLUTION"
        ]
        assert len(unlinked) == 1
        assert unlinked[0].metadata == {"solution_id": str(sol.pk)}

    def test_soft_delete_via_bulk_emits_delete(
        self, admin_client, case_a, case_b_same_org
    ):
        response = admin_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(case_a.pk), str(case_b_same_org.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        for case in (case_a, case_b_same_org):
            rows = [r for r in _case_activities(case) if r.action == "DELETE"]
            assert len(rows) == 1
            assert rows[0].metadata.get("bulk") is True


@pytest.mark.django_db
class TestActivityFeedAPI:
    def test_detail_includes_recent_activities(self, admin_client, case_a):
        # Generate a couple of activity rows.
        admin_client.patch(
            f"/api/cases/{case_a.pk}/",
            {"status": "Pending"},
            content_type="application/json",
        )
        response = admin_client.get(f"/api/cases/{case_a.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert "activities" in data
        assert len(data["activities"]) >= 1
        actions = {row["action"] for row in data["activities"]}
        assert "STATUS_CHANGED" in actions

    def test_activities_endpoint_paginates(self, admin_client, case_a):
        # Drive enough rows to paginate (status flips back-and-forth).
        for status in ("Pending", "New", "Pending", "New", "Pending"):
            admin_client.patch(
                f"/api/cases/{case_a.pk}/",
                {"status": status},
                content_type="application/json",
            )
        response = admin_client.get(f"/api/cases/{case_a.pk}/activities/?limit=2")
        assert response.status_code == 200
        body = response.json()
        assert "activities" in body
        assert len(body["activities"]) == 2
        assert body["count"] >= 5

    def test_activities_endpoint_cross_org_isolated(self, admin_client, case_b):
        response = admin_client.get(f"/api/cases/{case_b.pk}/activities/")
        assert response.status_code == 404

    def test_activities_endpoint_unauthenticated(self, unauthenticated_client, case_a):
        response = unauthenticated_client.get(f"/api/cases/{case_a.pk}/activities/")
        # DRF without a WWW-Authenticate challenge returns 403 here, matching
        # every other Cases endpoint.
        assert response.status_code in (401, 403)


@pytest.mark.django_db
class TestTheActorIsRecorded:
    """Who did it, not "System".

    `_resolve_actor` used to read `get_current_user().profile`, an attribute
    that cannot exist (`Profile.user` is a reverse FK named `profiles`), so
    every row in the audit log was written with `user=None` and every client
    rendered the whole timeline as the work of a system account.
    """

    def test_a_change_made_through_the_api_names_the_person_who_made_it(
        self, admin_client, admin_profile, case_a
    ):
        response = admin_client.patch(
            f"/api/cases/{case_a.pk}/",
            {"status": "Pending"},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

        row = (
            _case_activities(case_a)
            .filter(action="STATUS_CHANGED")
            .latest("created_at")
        )
        assert row.user_id == admin_profile.id

    def test_the_feed_serves_that_person_and_a_readable_verb(
        self, admin_client, admin_profile, case_a
    ):
        admin_client.patch(
            f"/api/cases/{case_a.pk}/",
            {"priority": "Urgent"},
            content_type="application/json",
        )

        response = admin_client.get(f"/api/cases/{case_a.pk}/activities/")
        assert response.status_code == 200
        row = next(
            r
            for r in response.json()["activities"]
            if r["action"] == "PRIORITY_CHANGED"
        )
        assert row["user"]["user_details"]["email"] == admin_profile.user.email
        # The label off ACTION_CHOICES, so no client keeps its own verb list.
        assert row["action_display"] == "Priority Changed"

    def test_work_with_no_request_behind_it_stays_a_system_action(self, case_a):
        from cases.signals import _create_activity

        # No request in flight: a Celery task, a management command, the
        # auto-stop sweep. Attributing those to a person would be a lie.
        _create_activity(case_a, "UPDATE", {"changes": {}})
        assert _case_activities(case_a).latest("created_at").user_id is None


@pytest.mark.django_db
class TestMetadataTruncation:
    def test_oversized_metadata_truncated(self, admin_client, case_a, org_a):
        from cases.signals import _create_activity

        oversized = {"blob": "x" * 5000}
        _create_activity(case_a, "UPDATE", oversized)
        row = _case_activities(case_a).filter(action="UPDATE").latest("created_at")
        assert row.metadata == {"_truncated": True}
