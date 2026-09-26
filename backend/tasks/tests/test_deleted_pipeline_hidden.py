"""A deleted task pipeline is gone on every verb, as a deleted lead pipeline is.

`TaskPipelineDetailView.get_object` and the stage views under it looked a pipeline
up by id and org only, so a pipeline removed from the list and the board could
still be read, renamed and given stages by id. Each now answers 404 with the
body of an id that does not exist.

Around that, the lead pipeline rules are mirrored: `is_active` is read-only
(a PUT could set it False and skip DELETE's refusal while tasks remain), a
deleted pipeline gives up the default slot, and taking the default demotes
the old one instead of failing the one-default-per-org constraint with a 500.
"""

import uuid

import pytest

from tasks.models import TaskPipeline, TaskStage

BASE = "/api/tasks"


@pytest.fixture
def live(org_a):
    return TaskPipeline.objects.create(name="Live", org=org_a)


@pytest.fixture
def gone(org_a):
    pipeline = TaskPipeline.objects.create(name="Gone", org=org_a, is_active=False)
    TaskStage.objects.create(pipeline=pipeline, name="Old stage", order=1, org=org_a)
    return pipeline


def _stage(pipeline):
    return pipeline.stages.first() or TaskStage.objects.create(
        pipeline=pipeline, name="Stage", order=1, org=pipeline.org
    )


class TestDeletedPipelineIsNotFound:
    def test_a_live_pipeline_answers_every_verb(self, admin_client, live):
        url = f"{BASE}/pipelines/{live.id}/"
        assert admin_client.get(url).status_code == 200
        assert (
            admin_client.put(url, {"name": "Renamed"}, format="json").status_code == 200
        )
        assert admin_client.delete(url).status_code == 204

    def test_a_deleted_pipeline_is_the_same_404_as_a_missing_id(
        self, admin_client, gone
    ):
        missing = f"{BASE}/pipelines/{uuid.uuid4()}/"
        deleted = f"{BASE}/pipelines/{gone.id}/"
        for verb, body in (("get", None), ("put", {"name": "Back"}), ("delete", None)):
            args = {"data": body, "format": "json"} if body else {}
            a = getattr(admin_client, verb)(deleted, **args)
            b = getattr(admin_client, verb)(missing, **args)
            assert a.status_code == b.status_code == 404, verb
            assert a.json() == b.json(), verb
        gone.refresh_from_db()
        assert gone.name == "Gone"

    def test_a_member_reads_a_live_pipeline_but_not_a_deleted_one(
        self, user_client, live, gone
    ):
        assert user_client.get(f"{BASE}/pipelines/{live.id}/").status_code == 200
        assert user_client.get(f"{BASE}/pipelines/{gone.id}/").status_code == 404

    def test_no_stage_is_added_to_a_deleted_pipeline(self, admin_client, live, gone):
        body = {"name": "New stage", "order": 2}
        assert (
            admin_client.post(
                f"{BASE}/pipelines/{live.id}/stages/", body, format="json"
            ).status_code
            == 201
        )
        assert (
            admin_client.post(
                f"{BASE}/pipelines/{gone.id}/stages/", body, format="json"
            ).status_code
            == 404
        )
        assert not gone.stages.filter(name="New stage").exists()

    def test_a_deleted_pipelines_stage_is_not_found(self, admin_client, live, gone):
        live_url = f"{BASE}/stages/{_stage(live).id}/"
        gone_stage = _stage(gone)
        gone_url = f"{BASE}/stages/{gone_stage.id}/"

        assert (
            admin_client.put(live_url, {"name": "Kept"}, format="json").status_code
            == 200
        )
        assert (
            admin_client.put(gone_url, {"name": "Back"}, format="json").status_code
            == 404
        )
        assert admin_client.delete(gone_url).status_code == 404
        gone_stage.refresh_from_db()
        assert gone_stage.name == "Old stage"
        assert admin_client.delete(live_url).status_code == 204


class TestActiveAndDefault:
    def test_a_put_cannot_delete_a_pipeline(self, admin_client, live):
        response = admin_client.put(
            f"{BASE}/pipelines/{live.id}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        live.refresh_from_db()
        assert live.is_active is True

    def test_deleting_the_default_frees_the_slot(self, admin_client, org_a):
        old = TaskPipeline.objects.create(name="Old", org=org_a, is_default=True)
        assert admin_client.delete(f"{BASE}/pipelines/{old.id}/").status_code == 204
        old.refresh_from_db()
        assert (old.is_active, old.is_default) == (False, False)

    def test_taking_the_default_demotes_the_old_one(self, admin_client, org_a, live):
        old = TaskPipeline.objects.create(name="Old", org=org_a, is_default=True)

        response = admin_client.put(
            f"{BASE}/pipelines/{live.id}/", {"is_default": True}, format="json"
        )

        assert response.status_code == 200, response.content
        old.refresh_from_db()
        live.refresh_from_db()
        assert (old.is_default, live.is_default) == (False, True)

    def test_a_new_default_demotes_one_a_deleted_pipeline_still_holds(
        self, admin_client, org_a
    ):
        """A pipeline deleted before it gave up the slot is out of reach by id
        now, so taking the default has to clear it."""
        stuck = TaskPipeline.objects.create(
            name="Stuck", org=org_a, is_default=True, is_active=False
        )

        response = admin_client.post(
            f"{BASE}/pipelines/",
            {"name": "Fresh", "is_default": True, "create_default_stages": False},
            format="json",
        )

        assert response.status_code == 201, response.content
        stuck.refresh_from_db()
        assert stuck.is_default is False
        assert TaskPipeline.objects.get(pk=response.json()["id"]).is_default is True
