"""A task board move stays inside the task's pipeline, and only enters a live
one.

The lead move has refused both since G4; the task move checked only the
stage's org. So a task in one pipeline could be dropped into another's stage
by id, and a deleted pipeline's stages kept accepting tasks. A task in no
stage may still enter any live pipeline: the move is the only way a task gets
a stage at all.
"""

import pytest

from tasks.models import Task, TaskPipeline, TaskStage


def _stage(org, pipeline, name, order=1):
    return TaskStage.objects.create(pipeline=pipeline, name=name, order=order, org=org)


@pytest.fixture
def work(org_a):
    return TaskPipeline.objects.create(name="Work", org=org_a)


@pytest.fixture
def hiring(org_a):
    return TaskPipeline.objects.create(name="Hiring", org=org_a)


def _task(org, **kw):
    return Task.objects.create(
        title="A task", status="New", priority="Low", org=org, **kw
    )


def _move(client, task, stage):
    return client.patch(
        f"/api/tasks/{task.id}/move/", {"stage_id": str(stage.id)}, format="json"
    )


class TestTaskMovePipeline:
    def test_a_move_within_the_tasks_pipeline_is_allowed(
        self, admin_client, org_a, work
    ):
        todo = _stage(org_a, work, "To do")
        doing = _stage(org_a, work, "Doing", 2)
        task = _task(org_a, stage=todo)

        assert _move(admin_client, task, doing).status_code == 200
        task.refresh_from_db()
        assert task.stage_id == doing.id

    def test_a_move_into_another_pipeline_is_refused(
        self, admin_client, org_a, work, hiring
    ):
        todo = _stage(org_a, work, "To do")
        screen = _stage(org_a, hiring, "Screen")
        task = _task(org_a, stage=todo)

        response = _move(admin_client, task, screen)

        assert response.status_code == 400
        assert response.json() == {
            "error": True,
            "errors": "This task is in the Work pipeline. "
            "Move it to one of that pipeline's stages.",
        }
        task.refresh_from_db()
        assert task.stage_id == todo.id

    def test_a_task_in_no_stage_may_enter_any_live_pipeline(
        self, admin_client, org_a, hiring
    ):
        screen = _stage(org_a, hiring, "Screen")
        task = _task(org_a)

        assert _move(admin_client, task, screen).status_code == 200
        task.refresh_from_db()
        assert task.stage_id == screen.id

    @pytest.mark.parametrize("staged", [False, True])
    def test_a_deleted_pipelines_stage_is_not_found(
        self, admin_client, org_a, work, staged
    ):
        """404 as for a stage id that does not exist, from no stage or from
        a stage of the same (now deleted) pipeline."""
        todo = _stage(org_a, work, "To do")
        doing = _stage(org_a, work, "Doing", 2)
        task = _task(org_a, stage=todo if staged else None)
        work.is_active = False
        work.save(update_fields=["is_active"])

        assert _move(admin_client, task, doing).status_code == 404
        task.refresh_from_db()
        assert task.stage_id == (todo.id if staged else None)
