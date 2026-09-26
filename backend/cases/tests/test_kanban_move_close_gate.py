"""A board move into Closed takes the close gate PATCH takes (D49).

`CaseMoveView.patch` set ``status="Closed"`` itself, directly or through a
stage's ``maps_to_status``, and skipped the gate `CaseCreateSerializer.validate`
applies on the edit path. With a ``pre_close`` approval rule armed, PATCH
refused the close and a drag on the board closed the ticket anyway, with no
approval and no ``closed_on``. Both paths now call
``cases.approvals.close_refusal``: a refused move answers what PATCH answers
and writes nothing, an allowed one stamps ``closed_on``, and moving back out of
Closed clears what PATCH clears.
"""

import datetime

import pytest
from django.utils import timezone

from cases.approvals import Approval, ApprovalRule
from cases.models import Case, CasePipeline, CaseStage


def _move(client, case, **body):
    return client.patch(f"/api/cases/{case.id}/move/", body, format="json")


def _patch(client, case, **body):
    return client.patch(f"/api/cases/{case.id}/", body, format="json")


def _case(org, user, name="Printer on fire", **fields):
    fields.setdefault("status", "New")
    return Case.objects.create(
        name=name, priority="High", org=org, created_by=user, **fields
    )


@pytest.fixture
def rule(org_a):
    return ApprovalRule.objects.create(
        org=org_a, name="High needs sign-off", is_active=True, match_priority="High"
    )


@pytest.fixture
def closing_stage(org_a, admin_user):
    pipeline = CasePipeline.objects.create(
        name="Support", org=org_a, created_by=admin_user
    )
    return CaseStage.objects.create(
        pipeline=pipeline,
        name="Resolved",
        order=1,
        stage_type="closed",
        maps_to_status="Closed",
        org=org_a,
        created_by=admin_user,
    )


def _snapshot(case):
    case.refresh_from_db()
    return (
        case.status,
        case.stage_id,
        case.closed_on,
        case.resolved_at,
        case.kanban_order,
    )


@pytest.mark.django_db
class TestRefusedLikePatch:
    def test_status_move_is_refused_with_the_patch_answer(
        self, admin_client, admin_profile, admin_user, org_a, rule
    ):
        case = _case(org_a, admin_user)
        before = _snapshot(case)

        patched = _patch(
            admin_client,
            case,
            status="Closed",
            closed_on=timezone.localdate().isoformat(),
        )
        moved = _move(admin_client, case, status="Closed")

        assert patched.status_code == 400, patched.content
        assert moved.status_code == patched.status_code, moved.content
        assert moved.json() == patched.json()
        assert moved.json()["errors"] == {
            "status": [
                "An approval is required before this case can be closed "
                "(rule: High needs sign-off)."
            ]
        }
        assert _snapshot(case) == before

    def test_stage_move_that_maps_to_closed_is_refused(
        self, admin_client, admin_profile, admin_user, org_a, rule, closing_stage
    ):
        case = _case(org_a, admin_user)
        before = _snapshot(case)

        moved = _move(admin_client, case, stage_id=str(closing_stage.id))

        assert moved.status_code == 400, moved.content
        assert list(moved.json()["errors"]) == ["status"]
        assert _snapshot(case) == before


@pytest.mark.django_db
class TestAllowedClose:
    def test_approved_close_goes_through_and_stamps_closed_on(
        self, admin_client, admin_profile, admin_user, org_a, rule
    ):
        case = _case(org_a, admin_user)
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="approved",
        )

        moved = _move(admin_client, case, status="Closed")

        assert moved.status_code == 200, moved.content
        case.refresh_from_db()
        assert case.status == "Closed"
        assert case.closed_on == timezone.localdate()
        assert case.resolved_at is not None

    def test_close_without_a_rule_stamps_closed_on(
        self, admin_client, admin_profile, admin_user, org_a, closing_stage
    ):
        case = _case(org_a, admin_user)

        moved = _move(admin_client, case, stage_id=str(closing_stage.id))

        assert moved.status_code == 200, moved.content
        case.refresh_from_db()
        assert (case.status, case.stage_id) == ("Closed", closing_stage.id)
        assert case.closed_on == timezone.localdate()

    def test_a_case_already_closed_is_not_re_approved(
        self, admin_client, admin_profile, admin_user, org_a, rule
    ):
        closed_on = timezone.localdate() - datetime.timedelta(days=3)
        case = _case(org_a, admin_user, status="Closed", closed_on=closed_on)

        moved = _move(admin_client, case, status="Closed")

        assert moved.status_code == 200, moved.content
        case.refresh_from_db()
        assert case.closed_on == closed_on


@pytest.mark.django_db
def test_reopen_by_move_clears_what_patch_clears(
    admin_client, admin_profile, admin_user, org_a
):
    closed_on = timezone.localdate() - datetime.timedelta(days=2)
    by_move = _case(org_a, admin_user, "A", status="Closed", closed_on=closed_on)
    by_patch = _case(org_a, admin_user, "B", status="Closed", closed_on=closed_on)
    for case in (by_move, by_patch):
        case.refresh_from_db()
        assert case.resolved_at is not None

    assert _move(admin_client, by_move, status="Pending").status_code == 200
    assert _patch(admin_client, by_patch, status="Pending").status_code == 200

    for case in (by_move, by_patch):
        case.refresh_from_db()
    assert (by_move.status, by_move.closed_on, by_move.resolved_at) == (
        by_patch.status,
        by_patch.closed_on,
        by_patch.resolved_at,
    )
    assert (by_move.status, by_move.closed_on, by_move.resolved_at) == (
        "Pending",
        None,
        None,
    )
