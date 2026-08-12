"""Tests for the bulk update / bulk delete Case endpoints."""

import pytest

from cases.approvals import ApprovalRule
from cases.models import Case
from conftest import rls_org


@pytest.mark.django_db
class TestBulkUpdateCases:
    def test_bulk_update_status(self, admin_client, case_a, case_b_same_org):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk), str(case_b_same_org.pk)],
                "fields": {"status": "Pending"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        case_a.refresh_from_db()
        case_b_same_org.refresh_from_db()
        assert case_a.status == "Pending"
        assert case_b_same_org.status == "Pending"

    def test_bulk_update_priority(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"priority": "Urgent"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        case_a.refresh_from_db()
        assert case_a.priority == "Urgent"

    def test_bulk_update_rejects_unknown_field(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"name": "hacker"}},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bulk_update_skips_other_org(self, admin_client, case_a, case_b):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk), str(case_b.pk)],
                "fields": {"status": "Closed", "closed_on": "2026-05-09"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 1
        # `case_b` belongs to the other tenant, so reading it back means
        # looking as that tenant.
        with rls_org(case_b.org):
            case_b.refresh_from_db()
        assert case_b.status != "Closed"

    def test_bulk_update_empty_ids(self, admin_client):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [], "fields": {"status": "Pending"}},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bulk_update_rejects_off_enum_status(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"status": "Hacked"}},
            content_type="application/json",
        )
        assert response.status_code == 400
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_bulk_update_rejects_nonstring_choice_value(self, admin_client, case_a):
        # A crafted unhashable payload must be a clean 400, not a 500 from
        # `value in valid_values` raising TypeError on a list.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"status": ["Closed"]}},
            content_type="application/json",
        )
        assert response.status_code == 400
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_bulk_update_rejects_nonstring_closed_on(self, admin_client, case_a):
        # `closed_on` is a scalar date; a non-string payload must 400 here rather
        # than reach `save()` and surface as a 500 DB error.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": ["2026-05-09"]},
            },
            content_type="application/json",
        )
        assert response.status_code == 400
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_bulk_update_malformed_id_is_not_500(self, admin_client):
        # A malformed UUID in `ids` must not crash the query. It names no real
        # case, so it drops out and the request reads as "no valid ids" (400),
        # never a 500 from `pk__in` raising ValidationError.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": ["not-a-uuid"], "fields": {"status": "Pending"}},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bulk_update_drops_malformed_id_keeps_valid(self, admin_client, case_a):
        # A mix of one good and one malformed id processes the good one and
        # silently ignores the garbage, matching how a nonexistent id behaves.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk), "not-a-uuid"],
                "fields": {"priority": "Urgent"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 1
        case_a.refresh_from_db()
        assert case_a.priority == "Urgent"


@pytest.mark.django_db
class TestBulkUpdateCasesAuthz:
    """A regular member may only bulk-edit cases they may write."""

    def test_non_writer_cannot_modify(self, user_client, case_a):
        # `case_a` was created by admin_user; the regular member is neither its
        # creator nor an assignee, so it must be left untouched.
        response = user_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"priority": "Urgent"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 0
        case_a.refresh_from_db()
        assert case_a.priority == "High"

    def test_creator_can_modify_own_case(self, user_client, regular_user, org_a):
        case = Case.objects.create(
            name="My own case",
            status="New",
            priority="Low",
            created_by=regular_user,
            org=org_a,
        )
        response = user_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case.pk)], "fields": {"priority": "Urgent"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 1
        case.refresh_from_db()
        assert case.priority == "Urgent"


@pytest.mark.django_db
class TestBulkUpdateCasesCloseGate:
    """Closing through the bulk path runs the same guard as the single case."""

    def test_close_requires_closed_on(self, admin_client, case_a):
        # Per-record outcomes mean a blocked close is now a 200 with a
        # `closed_on_required` outcome for this ticket, not a request-level
        # 400: see `TestBulkUpdatePerRecord.test_close_missing_date`.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"status": "Closed"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 0
        assert body["results"][0]["status"] == "closed_on_required"
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_close_requires_approval_when_rule_matches(
        self, admin_client, case_a, org_a
    ):
        # A rule with no match filters applies to every case in the org. Per-
        # record outcomes mean this is now a 200 with an `approval_required`
        # outcome, not a request-level 400: see
        # `TestBulkUpdatePerRecord.test_close_blocked_is_partial`.
        ApprovalRule.objects.create(
            name="Close gate",
            org=org_a,
            trigger_event="pre_close",
            is_active=True,
        )
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": "2026-05-09"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 0
        assert body["results"][0]["status"] == "approval_required"
        case_a.refresh_from_db()
        assert case_a.status == "New"


@pytest.mark.django_db
class TestBulkUpdatePerRecord:
    def test_mixed_access_reports_both(self, user_client, regular_user, org_a, case_a):
        # `case_a` was created by admin_user, so the regular member cannot write
        # it. A case they own can be written.
        mine = Case.objects.create(
            name="Mine",
            status="New",
            priority="Low",
            created_by=regular_user,
            org=org_a,
        )
        response = user_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(mine.pk), str(case_a.pk)], "fields": {"priority": "Urgent"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 1
        by_id = {r["id"]: r["status"] for r in body["results"]}
        assert by_id[str(mine.pk)] == "updated"
        assert by_id[str(case_a.pk)] == "no_access"

    def test_close_blocked_is_partial(
        self, admin_client, org_a, case_a, case_b_same_org
    ):
        ApprovalRule.objects.create(
            name="Close gate", org=org_a, trigger_event="pre_close", is_active=True
        )
        # A rule with no match filters applies to every case, so both closes are
        # gated; both come back approval_required and neither is closed.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk), str(case_b_same_org.pk)],
                "fields": {"status": "Closed", "closed_on": "2026-05-09"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 0
        assert {r["status"] for r in body["results"]} == {"approval_required"}
        case_a.refresh_from_db()
        assert case_a.status == "New"

    def test_close_missing_date(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"status": "Closed"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["results"][0]["status"] == "closed_on_required"
        assert body["updated"] == 0

    def test_tags_append_not_replace(self, admin_client, org_a, case_a):
        from common.models import Tags

        keep = Tags.objects.create(name="keep", org=org_a)
        add = Tags.objects.create(name="add", org=org_a)
        case_a.tags.add(keep)
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"tags": [str(add.pk)]}},
            content_type="application/json",
        )
        assert response.status_code == 200
        case_a.refresh_from_db()
        assert set(case_a.tags.values_list("name", flat=True)) == {"keep", "add"}

    def test_empty_fields_rejected(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {}},
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestBulkDeleteCases:
    def test_bulk_delete_soft(self, admin_client, case_a, case_b_same_org):
        response = admin_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(case_a.pk), str(case_b_same_org.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 2
        case_a.refresh_from_db()
        case_b_same_org.refresh_from_db()
        assert case_a.is_active is False
        assert case_b_same_org.is_active is False

    def test_bulk_delete_skips_other_org(self, admin_client, case_a, case_b):
        response = admin_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(case_a.pk), str(case_b.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 1
        # `case_b` belongs to the other tenant, so reading it back means
        # looking as that tenant.
        with rls_org(case_b.org):
            case_b.refresh_from_db()
        assert case_b.is_active is True

    def test_bulk_delete_empty(self, admin_client):
        response = admin_client.post(
            "/api/cases/bulk/delete/",
            {"ids": []},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bulk_delete_malformed_id_is_not_500(self, admin_client):
        # Same malformed-id guard as bulk update: a bad UUID must 400, never
        # 500 from the queryset raising ValidationError on `pk__in`.
        response = admin_client.post(
            "/api/cases/bulk/delete/",
            {"ids": ["not-a-uuid"]},
            content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestBulkDeleteCasesAuthz:
    """Deleting is admin-or-creator only; an assignee is not enough."""

    def test_non_creator_cannot_delete(self, user_client, case_a):
        response = user_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(case_a.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 0
        case_a.refresh_from_db()
        assert case_a.is_active is True

    def test_creator_can_delete_own_case(self, user_client, regular_user, org_a):
        case = Case.objects.create(
            name="My own case",
            status="New",
            priority="Low",
            created_by=regular_user,
            org=org_a,
        )
        response = user_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(case.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 1
        case.refresh_from_db()
        assert case.is_active is False


@pytest.mark.django_db
class TestBulkDeletePerRecord:
    def test_reports_deleted_and_no_access(
        self, user_client, regular_user, org_a, case_a
    ):
        mine = Case.objects.create(
            name="Mine",
            status="New",
            priority="Low",
            created_by=regular_user,
            org=org_a,
        )
        response = user_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(mine.pk), str(case_a.pk)]},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["deleted"] == 1
        by_id = {r["id"]: r["status"] for r in body["results"]}
        assert by_id[str(mine.pk)] == "deleted"
        assert by_id[str(case_a.pk)] == "no_access"
        case_a.refresh_from_db()
        assert case_a.is_active is True
