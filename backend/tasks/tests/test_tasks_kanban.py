import pytest
from django.db import connection

from tasks.models import Task, TaskPipeline, TaskStage


def _set_rls(org):
    """Set PostgreSQL RLS context so direct ORM writes are allowed.
    No-op on SQLite (used in tests).
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.mark.django_db
class TestTaskPipeline:
    """Tests for /api/tasks/pipelines/ endpoints."""

    def test_create_pipeline(self, admin_client, org_a):
        response = admin_client.post(
            "/api/tasks/pipelines/",
            {"name": "Development Pipeline", "create_default_stages": False},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Development Pipeline"
        assert TaskPipeline.objects.filter(
            name="Development Pipeline", org=org_a
        ).exists()

    def test_list_pipelines(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        TaskPipeline.objects.create(name="Pipeline 1", org=org_a, created_by=admin_user)
        response = admin_client.get("/api/tasks/pipelines/")
        assert response.status_code == 200
        data = response.json()
        assert "pipelines" in data
        assert len(data["pipelines"]) >= 1

    def test_get_pipeline_detail(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Detail Pipeline", org=org_a, created_by=admin_user
        )
        response = admin_client.get(f"/api/tasks/pipelines/{pipeline.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Detail Pipeline"

    def test_delete_empty_pipeline(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Empty Pipeline", org=org_a, created_by=admin_user
        )
        response = admin_client.delete(f"/api/tasks/pipelines/{pipeline.id}/")
        assert response.status_code == 204

    def test_create_pipeline_with_default_stages(self, admin_client, org_a):
        """Creating a pipeline with create_default_stages=True should create 4 stages."""
        response = admin_client.post(
            "/api/tasks/pipelines/",
            {"name": "Default Stages Pipeline", "create_default_stages": True},
            format="json",
        )
        assert response.status_code == 201
        pipeline = TaskPipeline.objects.get(name="Default Stages Pipeline")
        assert pipeline.stages.count() == 4

    def test_create_pipeline_non_admin_forbidden(self, user_client):
        """Non-admin should not be able to create a pipeline."""
        response = user_client.post(
            "/api/tasks/pipelines/",
            {"name": "Should Fail", "create_default_stages": False},
            format="json",
        )
        assert response.status_code == 403

    def test_update_pipeline(self, admin_client, admin_user, org_a):
        """Admin should be able to update a pipeline name."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Old Name", org=org_a, created_by=admin_user
        )
        response = admin_client.put(
            f"/api/tasks/pipelines/{pipeline.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    def test_update_pipeline_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to update a pipeline."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Non-Admin Pipeline", org=org_a, created_by=admin_user
        )
        response = user_client.put(
            f"/api/tasks/pipelines/{pipeline.id}/",
            {"name": "Updated"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_pipeline_with_tasks_returns_400(
        self, admin_client, admin_user, org_a
    ):
        """Deleting a pipeline that has tasks linked to its stages should fail."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Busy Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Active",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Blocking Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        response = admin_client.delete(f"/api/tasks/pipelines/{pipeline.id}/")
        assert response.status_code == 400
        assert "Cannot delete pipeline" in response.json()["error"]

    def test_delete_pipeline_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to delete a pipeline."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Non-Admin Delete Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.delete(f"/api/tasks/pipelines/{pipeline.id}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestTaskStage:
    """Tests for /api/tasks/pipelines/<pk>/stages/ and /api/tasks/stages/<pk>/."""

    def test_create_stage(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Stage Pipeline", org=org_a, created_by=admin_user
        )
        response = admin_client.post(
            f"/api/tasks/pipelines/{pipeline.id}/stages/",
            {
                "name": "To Do",
                "order": 1,
                "color": "#3B82F6",
                "stage_type": "open",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "To Do"
        assert TaskStage.objects.filter(pipeline=pipeline, name="To Do").exists()

    def test_update_stage(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Update Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Old Stage",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/tasks/stages/{stage.id}/",
            {"name": "Renamed Stage", "order": 2},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed Stage"

    def test_delete_empty_stage(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Delete Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Delete Me",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.delete(f"/api/tasks/stages/{stage.id}/")
        assert response.status_code == 204
        assert not TaskStage.objects.filter(id=stage.id).exists()

    def test_reorder_stages(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Reorder Pipeline", org=org_a, created_by=admin_user
        )
        stage_a = TaskStage.objects.create(
            pipeline=pipeline, name="A", order=0, org=org_a, created_by=admin_user
        )
        stage_b = TaskStage.objects.create(
            pipeline=pipeline, name="B", order=1, org=org_a, created_by=admin_user
        )
        # Reverse the order: B first, then A
        response = admin_client.post(
            f"/api/tasks/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": [str(stage_b.id), str(stage_a.id)]},
            format="json",
        )
        assert response.status_code == 200
        stage_a.refresh_from_db()
        stage_b.refresh_from_db()
        assert stage_b.order < stage_a.order

    def test_create_stage_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to create a stage."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Non-Admin Stage Pipeline", org=org_a, created_by=admin_user
        )
        response = user_client.post(
            f"/api/tasks/pipelines/{pipeline.id}/stages/",
            {"name": "Blocked Stage", "order": 1, "stage_type": "open"},
            format="json",
        )
        assert response.status_code == 403

    def test_update_stage_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to update a stage."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Non-Admin Update Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Locked Stage",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.put(
            f"/api/tasks/stages/{stage.id}/",
            {"name": "Cannot Update"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_stage_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to delete a stage."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Non-Admin Del Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Cannot Delete",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.delete(f"/api/tasks/stages/{stage.id}/")
        assert response.status_code == 403

    def test_delete_stage_with_tasks_returns_400(self, admin_client, admin_user, org_a):
        """Deleting a stage with tasks should fail with 400."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Busy Stage Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Full Stage",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Stage Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        response = admin_client.delete(f"/api/tasks/stages/{stage.id}/")
        assert response.status_code == 400
        assert "Cannot delete stage" in response.json()["error"]

    def test_reorder_stages_invalid_ids(self, admin_client, admin_user, org_a):
        """Reorder with invalid stage IDs should fail."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Invalid Reorder Pipeline", org=org_a, created_by=admin_user
        )
        TaskStage.objects.create(
            pipeline=pipeline, name="X", order=0, org=org_a, created_by=admin_user
        )
        response = admin_client.post(
            f"/api/tasks/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": ["00000000-0000-0000-0000-000000000001"]},
            format="json",
        )
        assert response.status_code == 400
        assert "Invalid stage IDs" in response.json()["error"]

    def test_reorder_stages_non_admin_forbidden(self, user_client, admin_user, org_a):
        """Non-admin should not be able to reorder stages."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Reorder Non-Admin Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline, name="Only", order=0, org=org_a, created_by=admin_user
        )
        response = user_client.post(
            f"/api/tasks/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": [str(stage.id)]},
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestTaskKanban:
    """Tests for GET /api/tasks/kanban/."""

    def test_kanban_view(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        Task.objects.create(
            title="Kanban Task",
            status="New",
            priority="Medium",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get("/api/tasks/kanban/")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert data["mode"] == "status"
        assert data["total_tasks"] >= 1

    def test_kanban_pipeline_mode(self, admin_client, admin_user, org_a):
        """Kanban with pipeline_id should return pipeline-based columns."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Kanban Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Backlog",
            order=1,
            color="#3B82F6",
            stage_type="open",
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Pipeline Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        response = admin_client.get(f"/api/tasks/kanban/?pipeline_id={pipeline.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "pipeline"
        assert data["pipeline"] is not None
        assert len(data["columns"]) >= 1
        assert data["columns"][0]["name"] == "Backlog"

    def test_kanban_filter_by_priority(self, admin_client, admin_user, org_a):
        """Kanban should filter tasks by priority."""
        _set_rls(org_a)
        Task.objects.create(
            title="High Kanban",
            status="New",
            priority="High",
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Low Kanban",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get("/api/tasks/kanban/?priority=High")
        assert response.status_code == 200
        data = response.json()
        assert data["total_tasks"] == 1

    def test_kanban_filter_by_search(self, admin_client, admin_user, org_a):
        """Kanban should filter tasks by search query."""
        _set_rls(org_a)
        Task.objects.create(
            title="Find Me",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Hidden",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get("/api/tasks/kanban/?search=Find")
        assert response.status_code == 200
        data = response.json()
        assert data["total_tasks"] == 1

    def test_kanban_filter_by_assigned_to(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """Kanban should filter tasks by assigned_to."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Assigned Kanban",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        task.assigned_to.add(admin_profile)
        Task.objects.create(
            title="Unassigned Kanban",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(
            f"/api/tasks/kanban/?assigned_to={admin_profile.id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_tasks"] == 1

    def test_kanban_non_admin_sees_only_assigned(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin should only see tasks they are assigned to.

        Note: ORM-created tasks have created_by=None (crum middleware not active
        in tests), so only assigned_to filtering works for non-admin visibility.
        """
        _set_rls(org_a)
        Task.objects.create(
            title="Admin Kanban Only",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        assigned_task = Task.objects.create(
            title="User Assigned Kanban",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        assigned_task.assigned_to.add(user_profile)
        response = user_client.get("/api/tasks/kanban/")
        assert response.status_code == 200
        data = response.json()
        # User should see only the task assigned to them
        assert data["total_tasks"] == 1

    def test_kanban_columns_sorted(self, admin_client, admin_user, org_a):
        """Kanban columns should be sorted by order."""
        response = admin_client.get("/api/tasks/kanban/")
        assert response.status_code == 200
        data = response.json()
        columns = data["columns"]
        orders = [c["order"] for c in columns]
        assert orders == sorted(orders)


@pytest.mark.django_db
class TestTaskMove:
    """Tests for PATCH /api/tasks/<pk>/move/."""

    def test_move_task_status(self, admin_client, admin_user, org_a):
        """Move task to a different status column."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Move Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"status": "In Progress"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["error"] is False
        task.refresh_from_db()
        assert task.status == "In Progress"

    def test_move_task_to_stage(self, admin_client, admin_user, org_a):
        """Move task to a pipeline stage."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Move Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="In Review",
            order=2,
            org=org_a,
            created_by=admin_user,
            maps_to_status="In Progress",
        )
        task = Task.objects.create(
            title="Stage Move Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"stage_id": str(stage.id)},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.stage == stage
        # Auto-maps to status
        assert task.status == "In Progress"

    def test_move_task_with_kanban_order(self, admin_client, admin_user, org_a):
        """Move task with explicit kanban_order."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Order Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"status": "New", "kanban_order": "5000.000000"},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert float(task.kanban_order) == 5000.0

    def test_move_task_wip_limit_exceeded(self, admin_client, admin_user, org_a):
        """Moving a task to a stage at WIP limit should fail."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="WIP Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Capped Stage",
            order=1,
            wip_limit=1,
            org=org_a,
            created_by=admin_user,
        )
        Task.objects.create(
            title="Existing Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        new_task = Task.objects.create(
            title="Overflow Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{new_task.id}/move/",
            {"stage_id": str(stage.id)},
            format="json",
        )
        assert response.status_code == 400
        assert "WIP limit" in response.json()["error"]

    def test_move_task_invalid_data(self, admin_client, admin_user, org_a):
        """Moving without stage_id or status should fail validation."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Invalid Move Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{task.id}/move/",
            {},
            format="json",
        )
        assert response.status_code == 400

    def test_move_task_non_admin_permission_denied(
        self, user_client, admin_user, org_a
    ):
        """Non-admin who is not creator/assignee should get 403."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Move Forbidden Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"status": "Completed"},
            format="json",
        )
        assert response.status_code == 403

    def test_move_task_non_admin_as_assignee_allowed(
        self, user_client, admin_user, user_profile, org_a
    ):
        """Non-admin who is assigned should be able to move a task."""
        _set_rls(org_a)
        task = Task.objects.create(
            title="Assignee Move Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        task.assigned_to.add(user_profile)
        response = user_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"status": "In Progress"},
            format="json",
        )
        assert response.status_code == 200

    def test_move_task_non_admin_as_creator_allowed(
        self, user_client, regular_user, user_profile, org_a
    ):
        """Creator, not assigned to themselves. The untested half everywhere.

        The assignee case above was covered; this one was not, in any module.
        It is the branch that shipped dead on the opportunities board, where a
        Profile was compared to a User FK and so never matched.
        """
        _set_rls(org_a)
        task = Task.objects.create(
            title="Creator Move Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=regular_user,
        )
        assert user_profile not in task.assigned_to.all()

        response = user_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"status": "In Progress"},
            format="json",
        )

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.status == "In Progress"

    def test_move_task_between_with_above_and_below(
        self, admin_client, admin_user, org_a
    ):
        """Move task between two tasks using above_task_id and below_task_id."""
        _set_rls(org_a)
        task_a = Task.objects.create(
            title="Task A",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            kanban_order=1000,
        )
        task_b = Task.objects.create(
            title="Task B",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            kanban_order=3000,
        )
        task_c = Task.objects.create(
            title="Task C",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/tasks/{task_c.id}/move/",
            {
                "status": "New",
                "above_task_id": str(task_a.id),
                "below_task_id": str(task_b.id),
            },
            format="json",
        )
        assert response.status_code == 200
        task_c.refresh_from_db()
        # Should be between 1000 and 3000
        assert 1000 < float(task_c.kanban_order) < 3000

    def test_move_task_clear_stage(self, admin_client, admin_user, org_a):
        """Move task to stage_id=null should clear the stage."""
        _set_rls(org_a)
        pipeline = TaskPipeline.objects.create(
            name="Clear Stage Pipeline", org=org_a, created_by=admin_user
        )
        stage = TaskStage.objects.create(
            pipeline=pipeline,
            name="Temp Stage",
            order=1,
            org=org_a,
            created_by=admin_user,
        )
        task = Task.objects.create(
            title="Clear Stage Task",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        response = admin_client.patch(
            f"/api/tasks/{task.id}/move/",
            {"stage_id": None, "status": "New"},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.stage is None
