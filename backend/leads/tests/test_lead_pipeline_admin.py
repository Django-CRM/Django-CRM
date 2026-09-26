"""Lead pipeline administration and the move back to "No stage".

The settings pages on both clients write through these endpoints, so each
refusal they surface is pinned here: who may write (admins and superusers,
never a plain member), what a reorder must contain, and which deletes are
refused while leads remain.
"""

import uuid

import pytest

from leads.models import Lead, LeadPipeline, LeadStage
from leads.tests.test_leads_kanban import _lead, _pipeline, _set_rls


@pytest.fixture
def superuser_client(user_client, regular_user):
    """The `user_client` caller, still a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return user_client


def _reorder(client, pipeline, stage_ids):
    return client.post(
        f"/api/leads/pipelines/{pipeline.id}/stages/reorder/",
        {"stage_ids": stage_ids},
        format="json",
    )


def _orders(pipeline):
    return list(pipeline.stages.order_by("order").values_list("name", flat=True))


# --------------------------------------------------------------------------
# Who may administer pipelines
# --------------------------------------------------------------------------

# (method, url template, body). `{p}` is a pipeline id, `{s}` a stage id.
ADMIN_WRITES = [
    ("post", "/api/leads/pipelines/", {"name": "Created"}),
    ("put", "/api/leads/pipelines/{p}/", {"name": "Renamed"}),
    ("delete", "/api/leads/pipelines/{p}/", None),
    ("post", "/api/leads/pipelines/{p}/stages/", {"name": "Added"}),
    ("put", "/api/leads/stages/{s}/", {"name": "Renamed stage"}),
    ("delete", "/api/leads/stages/{s}/", None),
    ("post", "/api/leads/pipelines/{p}/stages/reorder/", "REORDER"),
]
ADMIN_WRITE_IDS = [
    "create-pipeline",
    "update-pipeline",
    "delete-pipeline",
    "create-stage",
    "update-stage",
    "delete-stage",
    "reorder",
]


def _admin_write(client, admin_user, org, method, url, body):
    _set_rls(org)
    pipeline, stages = _pipeline(org, admin_user, "Admin write", "One", "Two")
    if body == "REORDER":
        body = {"stage_ids": [str(s.id) for s in reversed(stages)]}
    url = url.format(p=pipeline.id, s=stages[0].id)
    return getattr(client, method)(url, body, format="json"), pipeline


@pytest.mark.django_db
class TestPipelineWritesNeedAdmin:
    @pytest.mark.parametrize(
        ("method", "url", "body"), ADMIN_WRITES, ids=ADMIN_WRITE_IDS
    )
    def test_member_is_refused(self, user_client, admin_user, org_a, method, url, body):
        response, pipeline = _admin_write(
            user_client, admin_user, org_a, method, url, body
        )
        assert response.status_code == 403
        pipeline.refresh_from_db()
        assert pipeline.name == "Admin write"
        assert pipeline.is_active
        assert _orders(pipeline) == ["One", "Two"]

    @pytest.mark.parametrize(
        ("method", "url", "body"), ADMIN_WRITES, ids=ADMIN_WRITE_IDS
    )
    def test_admin_is_allowed(self, admin_client, admin_user, org_a, method, url, body):
        response, _pipeline_obj = _admin_write(
            admin_client, admin_user, org_a, method, url, body
        )
        assert response.status_code in (200, 201, 204)

    @pytest.mark.parametrize(
        ("method", "url", "body"), ADMIN_WRITES, ids=ADMIN_WRITE_IDS
    )
    def test_superuser_member_is_allowed(
        self, superuser_client, admin_user, org_a, method, url, body
    ):
        response, _pipeline_obj = _admin_write(
            superuser_client, admin_user, org_a, method, url, body
        )
        assert response.status_code in (200, 201, 204)

    def test_another_orgs_pipeline_is_404(self, org_b_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, (one, _two) = _pipeline(org_a, admin_user)
        assert (
            org_b_client.put(
                f"/api/leads/pipelines/{pipeline.id}/", {"name": "x"}, format="json"
            ).status_code
            == 404
        )
        assert _reorder(org_b_client, pipeline, [str(one.id)]).status_code == 404
        assert org_b_client.delete(f"/api/leads/stages/{one.id}/").status_code == 404


# --------------------------------------------------------------------------
# Reorder
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestStageReorder:
    def _seed(self, org, user):
        _set_rls(org)
        return _pipeline(org, user, "Reorder", "A", "B", "C")

    def test_full_set_reorders(self, admin_client, admin_user, org_a):
        pipeline, (a, b, c) = self._seed(org_a, admin_user)
        response = _reorder(admin_client, pipeline, [str(c.id), str(a.id), str(b.id)])
        assert response.status_code == 200
        assert _orders(pipeline) == ["C", "A", "B"]

    def test_missing_id_is_refused(self, admin_client, admin_user, org_a):
        pipeline, (a, b, _c) = self._seed(org_a, admin_user)
        response = _reorder(admin_client, pipeline, [str(b.id), str(a.id)])
        assert response.status_code == 400
        assert _orders(pipeline) == ["A", "B", "C"]

    def test_duplicate_id_is_refused(self, admin_client, admin_user, org_a):
        pipeline, (a, b, c) = self._seed(org_a, admin_user)
        response = _reorder(
            admin_client, pipeline, [str(c.id), str(a.id), str(b.id), str(c.id)]
        )
        assert response.status_code == 400
        assert _orders(pipeline) == ["A", "B", "C"]

    def test_extra_stage_from_another_pipeline_is_refused(
        self, admin_client, admin_user, org_a
    ):
        pipeline, (a, b, c) = self._seed(org_a, admin_user)
        _other, (stray, _x) = _pipeline(org_a, admin_user, "Other")
        response = _reorder(
            admin_client, pipeline, [str(c.id), str(a.id), str(b.id), str(stray.id)]
        )
        assert response.status_code == 400
        assert _orders(pipeline) == ["A", "B", "C"]
        stray.refresh_from_db()
        assert stray.order == 1

    def test_another_orgs_stage_is_refused(
        self, admin_client, admin_user, org_a, user_b, org_b
    ):
        pipeline, (a, b, _c) = self._seed(org_a, admin_user)
        _set_rls(org_b)
        _foreign, (theirs, _x) = _pipeline(org_b, user_b, "Theirs")
        _set_rls(org_a)
        response = _reorder(
            admin_client, pipeline, [str(theirs.id), str(a.id), str(b.id)]
        )
        assert response.status_code == 400
        assert _orders(pipeline) == ["A", "B", "C"]

    def test_unknown_id_is_refused(self, admin_client, admin_user, org_a):
        pipeline, (a, b, _c) = self._seed(org_a, admin_user)
        response = _reorder(
            admin_client, pipeline, [str(uuid.uuid4()), str(a.id), str(b.id)]
        )
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "stage_ids", [[], "not-a-list", ["not-a-uuid"], None], ids=str
    )
    def test_malformed_body_is_400(self, admin_client, admin_user, org_a, stage_ids):
        pipeline, _stages = self._seed(org_a, admin_user)
        response = _reorder(admin_client, pipeline, stage_ids)
        assert response.status_code == 400
        assert _orders(pipeline) == ["A", "B", "C"]

    def test_deleted_pipeline_is_404(self, admin_client, admin_user, org_a):
        pipeline, (a, b, c) = self._seed(org_a, admin_user)
        LeadPipeline.objects.filter(pk=pipeline.pk).update(is_active=False)
        response = _reorder(admin_client, pipeline, [str(c.id), str(b.id), str(a.id)])
        assert response.status_code == 404


# --------------------------------------------------------------------------
# Pipeline and stage writes
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestPipelineAndStageWrites:
    def test_seeded_won_stage_can_be_entered(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        response = admin_client.post(
            "/api/leads/pipelines/", {"name": "Seeded"}, format="json"
        )
        assert response.status_code == 201
        won = LeadStage.objects.get(pipeline_id=response.json()["id"], name="Won")
        assert not won.maps_to_status
        lead = _lead(org_a, admin_user, "seedwon@example.com")

        moved = admin_client.patch(
            f"/api/leads/{lead.id}/move/", {"stage_id": str(won.id)}, format="json"
        )

        assert moved.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == won

    def test_second_default_demotes_the_first(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        first, _ = _pipeline(org_a, admin_user, "First", is_default=True)
        second, _ = _pipeline(org_a, admin_user, "Second")

        response = admin_client.put(
            f"/api/leads/pipelines/{second.id}/", {"is_default": True}, format="json"
        )

        assert response.status_code == 200
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.is_default, second.is_default) == (False, True)

    def test_new_default_pipeline_demotes_the_old(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        first, _ = _pipeline(org_a, admin_user, "First", is_default=True)
        response = admin_client.post(
            "/api/leads/pipelines/",
            {"name": "Newer", "is_default": True, "create_default_stages": False},
            format="json",
        )
        assert response.status_code == 201
        first.refresh_from_db()
        assert first.is_default is False

    def test_put_cannot_deactivate_a_pipeline_with_leads(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, (one, _two) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "keepme@example.com", stage=one)

        response = admin_client.put(
            f"/api/leads/pipelines/{pipeline.id}/", {"is_active": False}, format="json"
        )

        assert response.status_code == 200
        pipeline.refresh_from_db()
        assert pipeline.is_active is True

    def test_deleted_pipeline_is_gone_by_id(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, _ = _pipeline(org_a, admin_user)
        assert (
            admin_client.delete(f"/api/leads/pipelines/{pipeline.id}/").status_code
            == 204
        )
        assert (
            admin_client.get(f"/api/leads/pipelines/{pipeline.id}/").status_code == 404
        )
        assert (
            admin_client.put(
                f"/api/leads/pipelines/{pipeline.id}/", {"name": "Back"}, format="json"
            ).status_code
            == 404
        )

    def test_delete_refusal_names_the_count(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, (one, _two) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "busy1@example.com", stage=one)
        _lead(org_a, admin_user, "busy2@example.com", stage=one)

        response = admin_client.delete(f"/api/leads/pipelines/{pipeline.id}/")

        assert response.status_code == 400
        assert "2 lead(s)" in response.json()["error"]
        pipeline.refresh_from_db()
        assert pipeline.is_active is True

    def test_converted_lead_does_not_block_stage_delete(
        self, admin_client, admin_user, org_a
    ):
        """A converted lead cannot be moved, so it cannot hold a stage hostage."""
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        done = _lead(
            org_a, admin_user, "done@example.com", status="converted", stage=one
        )

        response = admin_client.delete(f"/api/leads/stages/{one.id}/")

        assert response.status_code == 204
        done.refresh_from_db()
        assert done.stage is None

    def test_movable_lead_blocks_stage_delete(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "done2@example.com", status="converted", stage=one)
        _lead(org_a, admin_user, "open@example.com", stage=one)

        response = admin_client.delete(f"/api/leads/stages/{one.id}/")

        assert response.status_code == 400
        assert "1 lead(s)" in response.json()["error"]
        assert LeadStage.objects.filter(pk=one.pk).exists()

    def test_new_stage_goes_last(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, _stages = _pipeline(org_a, admin_user, "Order", "A", "B")
        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/", {"name": "C"}, format="json"
        )
        assert response.status_code == 201
        assert _orders(pipeline) == ["A", "B", "C"]

    def test_duplicate_stage_name_is_400(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, (one, two) = _pipeline(org_a, admin_user, "Dupes", "New", "Won")
        created = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {"name": "new"},
            format="json",
        )
        renamed = admin_client.put(
            f"/api/leads/stages/{two.id}/", {"name": "New"}, format="json"
        )
        assert created.status_code == 400
        assert renamed.status_code == 400
        assert "name" in renamed.json()["errors"]

    def test_same_stage_name_in_another_pipeline_is_fine(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        _pipeline(org_a, admin_user, "One", "New")
        other, _ = _pipeline(org_a, admin_user, "Two", "Other")
        response = admin_client.post(
            f"/api/leads/pipelines/{other.id}/stages/", {"name": "New"}, format="json"
        )
        assert response.status_code == 201

    def test_rename_keeping_its_own_name_is_fine(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        response = admin_client.put(
            f"/api/leads/stages/{one.id}/",
            {"name": one.name, "win_probability": 40},
            format="json",
        )
        assert response.status_code == 200

    def test_mapping_a_stage_to_converted_is_refused(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, (one, _two) = _pipeline(org_a, admin_user)
        created = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {"name": "Convert", "maps_to_status": "converted"},
            format="json",
        )
        updated = admin_client.put(
            f"/api/leads/stages/{one.id}/",
            {"maps_to_status": "converted"},
            format="json",
        )
        assert created.status_code == 400
        assert updated.status_code == 400
        one.refresh_from_db()
        assert not one.maps_to_status

    def test_existing_converted_mapping_survives_a_round_trip(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, _ = _pipeline(org_a, admin_user)
        won = LeadStage.objects.create(
            pipeline=pipeline,
            name="Won",
            order=9,
            org=org_a,
            maps_to_status="converted",
        )
        response = admin_client.put(
            f"/api/leads/stages/{won.id}/",
            {"name": "Won", "maps_to_status": "converted", "win_probability": 90},
            format="json",
        )
        assert response.status_code == 200
        won.refresh_from_db()
        assert won.win_probability == 90


# --------------------------------------------------------------------------
# Move: the write rule and "No stage"
# --------------------------------------------------------------------------


def _move(client, lead, **body):
    return client.patch(f"/api/leads/{lead.id}/move/", body, format="json")


@pytest.mark.django_db
class TestMoveWriteRuleAndNoStage:
    def test_admin_moves_a_lead_back_to_no_stage(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "unstage@example.com", stage=one)

        response = _move(admin_client, lead, stage_id=None)

        assert response.status_code == 200
        assert response.json()["lead"]["stage"] is None
        lead.refresh_from_db()
        assert lead.stage is None
        assert lead.status == "assigned"

    def test_unstaged_lead_shows_in_the_boards_unstaged_group(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, (one, _two) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "back@example.com", stage=one)
        _move(admin_client, lead, stage_id=None)

        board = admin_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        ).json()

        assert [c["email"] for c in board["unstaged"]["leads"]] == ["back@example.com"]
        assert all(not col["leads"] for col in board["columns"])

    def test_member_who_can_write_the_lead_may_unstage_it(
        self, user_client, admin_user, org_a, user_profile
    ):
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "assigned@example.com", stage=one)
        lead.assigned_to.add(user_profile)

        response = _move(user_client, lead, stage_id=None)

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage is None

    def test_member_who_cannot_write_the_lead_gets_404(
        self, user_client, admin_user, org_a, user_profile
    ):
        _set_rls(org_a)
        _p, (one, _two) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "hiddenmove@example.com", stage=one)

        response = _move(user_client, lead, stage_id=None)

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.stage == one

    def test_superuser_member_may_move_any_lead(
        self, superuser_client, admin_user, org_a
    ):
        _set_rls(org_a)
        _p, (one, two) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "super@example.com", stage=one)

        response = _move(superuser_client, lead, stage_id=str(two.id))

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == two

    def test_empty_body_is_still_400(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "empty@example.com")
        assert _move(admin_client, lead).status_code == 400

    def test_another_orgs_lead_is_404(self, org_b_client, admin_user, org_a):
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "otherorg@example.com")
        assert _move(org_b_client, lead, stage_id=None).status_code == 404
        assert Lead.objects.get(pk=lead.pk).stage is None
