"""``task_count`` on a task pipeline counts only tasks the caller can open (D47).

It used to count every task in the org, so a member whose board showed one
card read ``task_count: 2``, a number that included a task they cannot open.
It follows ``tasks.access.visible_tasks_qs`` now, the rule the board and the
list use, in all four places it appears: the pipeline list, the pipeline
detail, each of its nested stages, and the pipeline block on the board. The
same fix D9 made for lead pipelines.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from conftest import rls_org
from tasks.models import Task, TaskPipeline, TaskStage


def _pipeline(org, user, name="Delivery"):
    pipeline = TaskPipeline.objects.create(name=name, org=org, created_by=user)
    first = TaskStage.objects.create(
        pipeline=pipeline, name="To do", order=1, org=org, created_by=user
    )
    second = TaskStage.objects.create(
        pipeline=pipeline, name="Doing", order=2, org=org, created_by=user
    )
    return pipeline, first, second


def _task(org, user, title, stage):
    return Task.objects.create(
        title=title,
        status="New",
        priority="Low",
        org=org,
        created_by=user,
        stage=stage,
    )


@pytest.fixture
def seeded(admin_user, admin_profile, user_profile, org_a, org_b, user_b):
    pipeline, first, _second = _pipeline(org_a, admin_user)
    _task(org_a, admin_user, "Hidden from the member", first)
    mine = _task(org_a, admin_user, "Handed to the member", first)
    # Two assignees: a join on the M2M would count this task twice.
    mine.assigned_to.add(user_profile, admin_profile)
    # Another org's task, in another org's pipeline, never counted here.
    with rls_org(org_b):
        _b_pipeline, b_stage, _ = _pipeline(org_b, user_b, "Org B")
        _task(org_b, user_b, "Org B task", b_stage)
    return pipeline


def _counts(client, pipeline):
    """task_count from the list, the detail, its first stage and the board."""
    (row,) = client.get("/api/tasks/pipelines/").json()["pipelines"]
    assert row["id"] == str(pipeline.id)
    detail = client.get(f"/api/tasks/pipelines/{pipeline.id}/").json()
    board = client.get("/api/tasks/kanban/", {"pipeline_id": str(pipeline.id)}).json()
    assert [s["task_count"] for s in detail["stages"][1:]] == [0]
    return (
        row["task_count"],
        detail["task_count"],
        detail["stages"][0]["task_count"],
        board["pipeline"]["task_count"],
    )


@pytest.mark.django_db
class TestTaskPipelineCountsFollowVisibility:
    def test_member_counts_only_visible_tasks(self, user_client, seeded):
        assert _counts(user_client, seeded) == (1, 1, 1, 1)

    def test_admin_counts_every_task_in_the_org(self, admin_client, seeded):
        assert _counts(admin_client, seeded) == (2, 2, 2, 2)
        board = admin_client.get(
            "/api/tasks/kanban/", {"pipeline_id": str(seeded.id)}
        ).json()
        assert board["columns"][0]["task_count"] == 2
        assert board["pipeline"]["stage_count"] == 2

    def test_member_board_column_and_pipeline_agree(self, user_client, seeded):
        board = user_client.get(
            "/api/tasks/kanban/", {"pipeline_id": str(seeded.id)}
        ).json()
        assert board["columns"][0]["task_count"] == 1
        assert len(board["columns"][0]["tasks"]) == 1
        assert board["pipeline"]["task_count"] == 1

    def test_write_responses_carry_task_count(self, admin_client, admin_user, org_a):
        created = admin_client.post(
            "/api/tasks/pipelines/", {"name": "Fresh"}, format="json"
        ).json()
        assert created["task_count"] == 0
        assert {s["task_count"] for s in created["stages"]} == {0}

        _pipeline_obj, first, _second = _pipeline(org_a, admin_user, "Other")
        _task(org_a, admin_user, "Staged", first)
        updated = admin_client.put(
            f"/api/tasks/stages/{first.id}/", {"name": "Renamed"}, format="json"
        ).json()
        assert updated["task_count"] == 1

    def test_pipeline_list_query_count_does_not_grow_per_pipeline(
        self, admin_client, admin_user, org_a, seeded
    ):
        with CaptureQueriesContext(connection) as one:
            admin_client.get("/api/tasks/pipelines/")

        for name in ("Second", "Third"):
            _p, stage, _s = _pipeline(org_a, admin_user, name)
            _task(org_a, admin_user, f"{name} task", stage)
        with CaptureQueriesContext(connection) as three:
            response = admin_client.get("/api/tasks/pipelines/")

        assert len(response.json()["pipelines"]) == 3
        assert len(three.captured_queries) == len(one.captured_queries)
