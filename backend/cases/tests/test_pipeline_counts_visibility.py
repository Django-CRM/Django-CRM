"""``case_count`` on a ticket pipeline counts only tickets the caller can open (D47).

It used to count every case in the org, so a member whose board showed one
card read ``case_count: 2``, a number that included a ticket they cannot open.
It follows ``cases.access.visible_cases_qs`` now, the rule the board and the
list use, in all four places it appears: the pipeline list, the pipeline
detail, each of its nested stages, and the pipeline block on the board. The
same fix D9 made for lead pipelines.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cases.models import Case, CasePipeline, CaseStage, CaseWatcher
from conftest import rls_org


def _pipeline(org, user, name="Support"):
    pipeline = CasePipeline.objects.create(name=name, org=org, created_by=user)
    first = CaseStage.objects.create(
        pipeline=pipeline, name="Triage", order=1, org=org, created_by=user
    )
    second = CaseStage.objects.create(
        pipeline=pipeline, name="Working", order=2, org=org, created_by=user
    )
    return pipeline, first, second


def _case(org, user, name, stage):
    return Case.objects.create(
        name=name,
        status="New",
        priority="Normal",
        org=org,
        created_by=user,
        stage=stage,
    )


@pytest.fixture
def seeded(admin_user, admin_profile, user_profile, org_a, org_b, user_b):
    pipeline, first, _second = _pipeline(org_a, admin_user)
    _case(org_a, admin_user, "Hidden from the member", first)
    mine = _case(org_a, admin_user, "Handed to the member", first)
    # Two assignees: a join on the M2M would count this ticket twice.
    mine.assigned_to.add(user_profile, admin_profile)
    # Another org's ticket, in another org's pipeline, never counted here.
    with rls_org(org_b):
        _b_pipeline, b_stage, _ = _pipeline(org_b, user_b, "Org B")
        _case(org_b, user_b, "Org B ticket", b_stage)
    return pipeline


def _counts(client, pipeline):
    """case_count from the list, the detail, its first stage and the board."""
    (row,) = client.get("/api/cases/pipelines/").json()["pipelines"]
    assert row["id"] == str(pipeline.id)
    detail = client.get(f"/api/cases/pipelines/{pipeline.id}/").json()
    board = client.get("/api/cases/kanban/", {"pipeline_id": str(pipeline.id)}).json()
    assert [s["case_count"] for s in detail["stages"][1:]] == [0]
    return (
        row["case_count"],
        detail["case_count"],
        detail["stages"][0]["case_count"],
        board["pipeline"]["case_count"],
    )


@pytest.mark.django_db
class TestCasePipelineCountsFollowVisibility:
    def test_member_counts_only_visible_cases(self, user_client, seeded):
        assert _counts(user_client, seeded) == (1, 1, 1, 1)

    def test_admin_counts_every_case_in_the_org(self, admin_client, seeded):
        assert _counts(admin_client, seeded) == (2, 2, 2, 2)
        board = admin_client.get(
            "/api/cases/kanban/", {"pipeline_id": str(seeded.id)}
        ).json()
        assert board["columns"][0]["case_count"] == 2
        assert board["pipeline"]["stage_count"] == 2

    def test_member_board_column_and_pipeline_agree(self, user_client, seeded):
        board = user_client.get(
            "/api/cases/kanban/", {"pipeline_id": str(seeded.id)}
        ).json()
        assert board["columns"][0]["case_count"] == 1
        assert len(board["columns"][0]["cases"]) == 1
        assert board["pipeline"]["case_count"] == 1

    def test_watcher_counts_the_ticket_they_follow(
        self, user_client, user_profile, admin_user, org_a
    ):
        pipeline, first, _second = _pipeline(org_a, admin_user, "Watched")
        watched = _case(org_a, admin_user, "Watched ticket", first)
        CaseWatcher.objects.create(case=watched, profile=user_profile, org=org_a)
        detail = user_client.get(f"/api/cases/pipelines/{pipeline.id}/").json()
        assert detail["case_count"] == 1

    def test_write_responses_carry_case_count(self, admin_client, admin_user, org_a):
        created = admin_client.post(
            "/api/cases/pipelines/", {"name": "Fresh"}, format="json"
        ).json()
        assert created["case_count"] == 0
        assert {s["case_count"] for s in created["stages"]} == {0}

        _pipeline_obj, first, _second = _pipeline(org_a, admin_user, "Other")
        _case(org_a, admin_user, "Staged", first)
        updated = admin_client.put(
            f"/api/cases/stages/{first.id}/", {"name": "Renamed"}, format="json"
        ).json()
        assert updated["case_count"] == 1

    def test_pipeline_list_query_count_does_not_grow_per_pipeline(
        self, admin_client, admin_user, org_a, seeded
    ):
        with CaptureQueriesContext(connection) as one:
            admin_client.get("/api/cases/pipelines/")

        for name in ("Second", "Third"):
            _p, stage, _s = _pipeline(org_a, admin_user, name)
            _case(org_a, admin_user, f"{name} ticket", stage)
        with CaptureQueriesContext(connection) as three:
            response = admin_client.get("/api/cases/pipelines/")

        assert len(response.json()["pipelines"]) == 3
        assert len(three.captured_queries) == len(one.captured_queries)
