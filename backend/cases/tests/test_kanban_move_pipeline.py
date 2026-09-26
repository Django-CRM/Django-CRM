"""A ticket board move stays inside the ticket's pipeline, and only enters a
live one.

The lead move has refused both since G4; the ticket move checked only the
stage's org. So a ticket in one pipeline could be dropped into another's stage
by id, and a deleted pipeline's stages kept accepting tickets. A ticket in no
stage may still enter any live pipeline: the move is the only way a ticket gets
a stage at all.
"""

import pytest

from cases.models import Case, CasePipeline, CaseStage


def _stage(org, pipeline, name, order=1):
    return CaseStage.objects.create(pipeline=pipeline, name=name, order=order, org=org)


@pytest.fixture
def support(org_a):
    return CasePipeline.objects.create(name="Support", org=org_a)


@pytest.fixture
def billing(org_a):
    return CasePipeline.objects.create(name="Billing", org=org_a)


def _case(org, **kw):
    return Case.objects.create(
        name="A ticket", status="New", priority="Normal", org=org, **kw
    )


def _move(client, case, stage):
    return client.patch(
        f"/api/cases/{case.id}/move/", {"stage_id": str(stage.id)}, format="json"
    )


class TestTicketMovePipeline:
    def test_a_move_within_the_tickets_pipeline_is_allowed(
        self, admin_client, org_a, support
    ):
        triage = _stage(org_a, support, "Triage")
        doing = _stage(org_a, support, "Doing", 2)
        case = _case(org_a, stage=triage)

        assert _move(admin_client, case, doing).status_code == 200
        case.refresh_from_db()
        assert case.stage_id == doing.id

    def test_a_move_into_another_pipeline_is_refused(
        self, admin_client, org_a, support, billing
    ):
        triage = _stage(org_a, support, "Triage")
        invoiced = _stage(org_a, billing, "Invoiced")
        case = _case(org_a, stage=triage)

        response = _move(admin_client, case, invoiced)

        assert response.status_code == 400
        assert response.json() == {
            "error": True,
            "errors": "This ticket is in the Support pipeline. "
            "Move it to one of that pipeline's stages.",
        }
        case.refresh_from_db()
        assert case.stage_id == triage.id

    def test_a_ticket_in_no_stage_may_enter_any_live_pipeline(
        self, admin_client, org_a, billing
    ):
        invoiced = _stage(org_a, billing, "Invoiced")
        case = _case(org_a)

        assert _move(admin_client, case, invoiced).status_code == 200
        case.refresh_from_db()
        assert case.stage_id == invoiced.id

    @pytest.mark.parametrize("staged", [False, True])
    def test_a_deleted_pipelines_stage_is_not_found(
        self, admin_client, org_a, support, staged
    ):
        """404 as for a stage id that does not exist, from no stage or from
        a stage of the same (now deleted) pipeline."""
        triage = _stage(org_a, support, "Triage")
        doing = _stage(org_a, support, "Doing", 2)
        case = _case(org_a, stage=triage if staged else None)
        support.is_active = False
        support.save(update_fields=["is_active"])

        assert _move(admin_client, case, doing).status_code == 404
        case.refresh_from_db()
        assert case.stage_id == (triage.id if staged else None)
