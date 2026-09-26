"""The WIP limit on a task board move into a pipeline stage.

The limit counts the whole stage, as it did before. Two things changed. A
reorder inside the task's own stage adds nothing to it, so the limit never
refuses one, even on a stage already over a limit an admin has since lowered.
And the stage row is locked before the stage is counted, so two moves racing
for its last slot take turns instead of both getting it.
"""

from unittest import mock

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext

from tasks.models import Task, TaskPipeline, TaskStage


@pytest.fixture
def pipeline(org_a):
    return TaskPipeline.objects.create(name="Work", org=org_a)


@pytest.fixture
def stage(org_a, pipeline):
    return TaskStage.objects.create(
        pipeline=pipeline,
        name="Limited",
        order=1,
        org=org_a,
        wip_limit=2,
        maps_to_status="In Progress",
    )


def _task(org, title, **kw):
    kw.setdefault("status", "In Progress")
    return Task.objects.create(title=title, priority="Low", org=org, **kw)


def _move(client, task, stage, **extra):
    return client.patch(
        f"/api/tasks/{task.id}/move/",
        {"stage_id": str(stage.id), **extra},
        format="json",
    )


class TestTaskWipLimit:
    def test_room_under_the_limit_allows_the_move(self, admin_client, org_a, stage):
        _task(org_a, "Present", stage=stage)
        mover = _task(org_a, "Mover", status="New")
        assert _move(admin_client, mover, stage).status_code == 200
        mover.refresh_from_db()
        assert mover.stage_id == stage.id

    def test_a_move_from_another_stage_into_a_full_one_is_refused(
        self, admin_client, org_a, pipeline, stage
    ):
        other = TaskStage.objects.create(
            pipeline=pipeline, name="Other", order=2, org=org_a
        )
        _task(org_a, "One", stage=stage)
        _task(org_a, "Two", stage=stage)
        mover = _task(org_a, "Mover", stage=other)

        response = _move(admin_client, mover, stage)

        assert response.status_code == 400
        assert response.json() == {
            "error": "Stage 'Limited' has reached its WIP limit of 2"
        }
        mover.refresh_from_db()
        assert mover.stage_id == other.id

    def test_a_reorder_inside_a_stage_over_a_lowered_limit_succeeds(
        self, admin_client, org_a, stage
    ):
        first = _task(org_a, "First", stage=stage)
        _task(org_a, "Second", stage=stage)
        mover = _task(org_a, "Third", stage=stage)
        stage.wip_limit = 1
        stage.save(update_fields=["wip_limit"])

        response = _move(admin_client, mover, stage, below_task_id=str(first.id))

        assert response.status_code == 200, response.content
        mover.refresh_from_db()
        first.refresh_from_db()
        assert mover.stage_id == stage.id
        assert mover.kanban_order < first.kanban_order

    def test_the_target_stage_is_locked_before_it_is_counted(
        self, admin_client, org_a, stage
    ):
        """SQLite drops FOR UPDATE, so this asks which querysets were locked;
        the SQL itself is pinned by the postgres_only test below."""
        mover = _task(org_a, "Mover", status="New")
        original = QuerySet.select_for_update
        with mock.patch.object(
            QuerySet, "select_for_update", autospec=True, side_effect=original
        ) as spy:
            assert _move(admin_client, mover, stage).status_code == 200
        assert TaskStage in {call.args[0].model for call in spy.call_args_list}


@pytest.mark.postgres_only
def test_the_stage_select_carries_for_update(admin_client, org_a, stage):
    if connection.vendor != "postgresql":
        pytest.skip("FOR UPDATE needs PostgreSQL")
    mover = _task(org_a, "Mover", status="New")
    with CaptureQueriesContext(connection) as queries:
        assert _move(admin_client, mover, stage).status_code == 200
    stage_selects = [
        q["sql"]
        for q in queries
        if q["sql"].startswith("SELECT") and 'FROM "task_stage"' in q["sql"]
    ]
    assert any(
        sql.rstrip().endswith('FOR UPDATE OF "task_stage"') for sql in stage_selects
    ), stage_selects
