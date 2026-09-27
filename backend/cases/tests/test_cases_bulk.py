"""Tests for the bulk update / bulk delete Case endpoints."""

from unittest.mock import patch

import pytest

from cases.approvals import ApprovalRule
from cases.models import Case, CaseWatcher
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

    def test_close_without_a_date_is_dated_by_the_server(self, admin_client, case_a):
        # The server dates a close that sends none, today in the org's
        # timezone; see `test_close_dated_by_server.py` for the org-day cases.
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"status": "Closed"}},
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["updated"] == 1
        assert body["results"][0]["status"] == "updated"
        case_a.refresh_from_db()
        assert case_a.status == "Closed"
        assert case_a.closed_on is not None

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
    def test_mixed_access_reports_both(
        self, user_client, regular_user, user_profile, org_a, case_a
    ):
        # `case_a` was created by admin_user, so the regular member cannot write
        # it; they watch it, so they may open it and hear `no_access`. A case
        # they own can be written.
        CaseWatcher.objects.create(case=case_a, profile=user_profile, org=org_a)
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
        self, user_client, regular_user, user_profile, org_a, case_a
    ):
        # Watched, so readable: `no_access` is for a ticket the caller may open.
        CaseWatcher.objects.create(case=case_a, profile=user_profile, org=org_a)
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


@pytest.mark.django_db
class TestBulkUpdateSharesTheTicketWrite:
    """The bulk edit writes through `cases.updates.update_case`, the PATCH's
    own path. Its private copy had drifted: it assigned deactivated people and
    archived tags, and emailed nobody it assigned."""

    def _member(self, org, email, active=True):
        from common.models import Profile, User

        user = User.objects.create_user(email=email, password="x")
        return Profile.objects.create(user=user, org=org, role="USER", is_active=active)

    def test_a_deactivated_member_is_not_assigned(self, admin_client, org_a, case_a):
        live = self._member(org_a, "live@a.test")
        gone = self._member(org_a, "gone@a.test", active=False)
        with patch("cases.updates.send_email_to_assigned_user"):
            response = admin_client.post(
                "/api/cases/bulk/update/",
                {
                    "ids": [str(case_a.pk)],
                    "fields": {"assigned_to": [str(live.id), str(gone.id)]},
                },
                content_type="application/json",
            )
        assert response.status_code == 200
        assert list(case_a.assigned_to.all()) == [live]

    def test_an_archived_tag_is_not_added(self, admin_client, org_a, case_a):
        from common.models import Tags

        live = Tags.objects.create(name="live", org=org_a)
        old = Tags.objects.create(name="old", org=org_a, is_active=False)
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case_a.pk)], "fields": {"tags": [str(live.id), str(old.id)]}},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert list(case_a.tags.values_list("name", flat=True)) == ["live"]

    def test_only_the_newly_assigned_are_emailed(
        self,
        admin_client,
        org_a,
        case_a,
        case_b_same_org,
        django_capture_on_commit_callbacks,
    ):
        kept = self._member(org_a, "kept@a.test")
        added = self._member(org_a, "added@a.test")
        case_a.assigned_to.add(kept)
        with patch("cases.updates.send_email_to_assigned_user") as task:
            with django_capture_on_commit_callbacks(execute=True):
                response = admin_client.post(
                    "/api/cases/bulk/update/",
                    {
                        "ids": [str(case_a.pk), str(case_b_same_org.pk)],
                        "fields": {"assigned_to": [str(kept.id), str(added.id)]},
                    },
                    content_type="application/json",
                )
        assert response.status_code == 200
        recipients = sorted(
            (call.args[1], sorted(call.args[0])) for call in task.delay.call_args_list
        )
        assert recipients == sorted(
            [
                (case_a.pk, [added.id]),
                (case_b_same_org.pk, sorted([kept.id, added.id])),
            ]
        )

    def test_the_email_is_queued_only_after_the_commit(
        self, admin_client, org_a, case_a, django_capture_on_commit_callbacks
    ):
        """Queued inside the per-ticket `atomic()`, a worker could read the
        ticket before its new assignees were committed."""
        added = self._member(org_a, "added@a.test")
        with patch("cases.updates.send_email_to_assigned_user") as task:
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                response = admin_client.post(
                    "/api/cases/bulk/update/",
                    {
                        "ids": [str(case_a.pk)],
                        "fields": {"assigned_to": [str(added.id)]},
                    },
                    content_type="application/json",
                )
            assert response.status_code == 200
            task.delay.assert_not_called()
            assert len(callbacks) == 1
            callbacks[0]()
        task.delay.assert_called_once_with([added.id], case_a.pk, str(org_a.id))

    def test_a_malformed_assignee_id_is_400_before_any_write(
        self, admin_client, case_a, case_b_same_org
    ):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk), str(case_b_same_org.pk)],
                "fields": {"priority": "Low", "assigned_to": ["not-a-uuid"]},
            },
            content_type="application/json",
        )
        assert response.status_code == 400
        case_a.refresh_from_db()
        assert case_a.priority == "High"

    def test_a_malformed_closed_on_is_invalid_not_missing(self, admin_client, case_a):
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": "someday"},
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "invalid"
        case_a.refresh_from_db()
        assert case_a.status == "New"
