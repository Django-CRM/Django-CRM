"""A task move the model refuses answers 400, as PATCH does, not 500.

``Task.save()`` calls ``full_clean()``. The PATCH and PUT paths catch the
Django ``ValidationError`` that raises and answer 400 (`_model_errors`); the
board move did not, so any move the model refused was a 500. It is reachable
through configuration alone: ``TaskStage.maps_to_status`` is free text, so a
stage mapped to a status ``Task`` does not have made every move into it crash.
Task moves have no completion side effect to mirror: ``Task`` has no
``completed_at`` and no save signal, so status is the whole of it.
"""

import pytest

from tasks.models import Task, TaskPipeline, TaskStage


@pytest.mark.django_db
def test_move_into_a_stage_the_model_refuses_is_a_400(
    admin_client, admin_profile, admin_user, org_a
):
    pipeline = TaskPipeline.objects.create(
        name="Delivery", org=org_a, created_by=admin_user
    )
    stage = TaskStage.objects.create(
        pipeline=pipeline,
        name="Shipped",
        order=1,
        maps_to_status="Shipped",
        org=org_a,
        created_by=admin_user,
    )
    task = Task.objects.create(
        title="Pack the box",
        status="New",
        priority="Low",
        org=org_a,
        created_by=admin_user,
    )

    response = admin_client.patch(
        f"/api/tasks/{task.id}/move/", {"stage_id": str(stage.id)}, format="json"
    )

    assert response.status_code == 400, response.content
    body = response.json()
    assert body["error"] is True
    assert "status" in body["errors"]
    task.refresh_from_db()
    assert (task.status, task.stage_id) == ("New", None)


@pytest.mark.django_db
def test_move_to_completed_still_goes_through(
    admin_client, admin_profile, admin_user, org_a
):
    task = Task.objects.create(
        title="Pack the box",
        status="New",
        priority="Low",
        org=org_a,
        created_by=admin_user,
    )

    response = admin_client.patch(
        f"/api/tasks/{task.id}/move/", {"status": "Completed"}, format="json"
    )

    assert response.status_code == 200, response.content
    task.refresh_from_db()
    assert task.status == "Completed"
