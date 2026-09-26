"""`TaskStage.maps_to_status` must be a task status or empty.

The column is free text. A stage mapped to anything else is one no task can
move into, because `Task.save()` runs `full_clean()` and refuses the status
the move would set. The serializer now refuses it at the door with a 400.
"""

import pytest

from tasks.models import TaskPipeline, TaskStage


@pytest.fixture
def pipeline(admin_user, org_a):
    return TaskPipeline.objects.create(
        name="Stage status pipeline", org=org_a, created_by=admin_user
    )


def _create(client, pipeline, **extra):
    return client.post(
        f"/api/tasks/pipelines/{pipeline.id}/stages/",
        {"name": "Stage", "order": 1, "stage_type": "open", **extra},
        format="json",
    )


@pytest.mark.django_db
class TestMapsToStatus:
    def test_unknown_status_is_400(self, admin_client, pipeline):
        resp = _create(admin_client, pipeline, maps_to_status="Shipped")
        assert resp.status_code == 400, resp.content
        body = resp.json()
        assert body["error"] is True
        assert "maps_to_status" in body["errors"]
        assert not TaskStage.objects.filter(pipeline=pipeline).exists()

    def test_real_status_is_201(self, admin_client, pipeline):
        resp = _create(admin_client, pipeline, maps_to_status="In Progress")
        assert resp.status_code == 201, resp.content
        assert resp.json()["maps_to_status"] == "In Progress"

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_is_allowed(self, admin_client, pipeline, value):
        resp = _create(admin_client, pipeline, maps_to_status=value)
        assert resp.status_code == 201, resp.content

    def test_update_to_unknown_status_is_400(
        self, admin_client, admin_user, org_a, pipeline
    ):
        stage = TaskStage.objects.create(
            pipeline=pipeline, name="Existing", order=1, org=org_a
        )
        resp = admin_client.put(
            f"/api/tasks/stages/{stage.id}/",
            {"maps_to_status": "completed"},
            format="json",
        )
        assert resp.status_code == 400, resp.content
        stage.refresh_from_db()
        assert stage.maps_to_status is None
