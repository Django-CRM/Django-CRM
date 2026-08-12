"""
Tests for Case Kanban, Pipeline, and Stage API views.

Run with: pytest cases/tests/test_cases_kanban.py -v
"""

import pytest
from django.db import connection
from rest_framework import status

from cases.models import Case, CasePipeline, CaseStage

PIPELINES_URL = "/api/cases/pipelines/"
KANBAN_URL = "/api/cases/kanban/"


def _pipeline_detail_url(pk):
    return f"/api/cases/pipelines/{pk}/"


def _stage_create_url(pipeline_pk):
    return f"/api/cases/pipelines/{pipeline_pk}/stages/"


def _stage_reorder_url(pipeline_pk):
    return f"/api/cases/pipelines/{pipeline_pk}/stages/reorder/"


def _stage_detail_url(pk):
    return f"/api/cases/stages/{pk}/"


def _set_rls(org):
    """Set PostgreSQL RLS context so direct ORM writes are allowed.
    No-op on SQLite (used in tests).
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.fixture
def pipeline(admin_user, org_a):
    """A case pipeline belonging to org_a with no default stages."""
    _set_rls(org_a)
    return CasePipeline.objects.create(
        name="Support Pipeline",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def stage_open(pipeline, admin_user, org_a):
    """An 'open' stage inside the test pipeline."""
    _set_rls(org_a)
    return CaseStage.objects.create(
        pipeline=pipeline,
        name="New",
        order=0,
        color="#3B82F6",
        stage_type="open",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def stage_closed(pipeline, admin_user, org_a):
    """A 'closed' stage inside the test pipeline."""
    _set_rls(org_a)
    return CaseStage.objects.create(
        pipeline=pipeline,
        name="Resolved",
        order=1,
        color="#22C55E",
        stage_type="closed",
        org=org_a,
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestCasePipeline:
    """Tests for pipeline CRUD: /api/cases/pipelines/"""

    def test_create_pipeline(self, admin_client):
        response = admin_client.post(
            PIPELINES_URL,
            {
                "name": "Engineering Pipeline",
                "create_default_stages": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Engineering Pipeline"

    def test_list_pipelines(self, admin_client, pipeline):
        response = admin_client.get(PIPELINES_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "pipelines" in response.data
        names = [p["name"] for p in response.data["pipelines"]]
        assert "Support Pipeline" in names

    def test_get_pipeline_detail(self, admin_client, pipeline):
        response = admin_client.get(_pipeline_detail_url(pipeline.pk))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Support Pipeline"

    def test_delete_empty_pipeline(self, admin_client, pipeline):
        """Deleting a pipeline with no cases should succeed with 204."""
        response = admin_client.delete(_pipeline_detail_url(pipeline.pk))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_create_pipeline_with_default_stages(self, admin_client, org_a):
        """Creating a pipeline with defaults should create 5 stages."""
        response = admin_client.post(
            PIPELINES_URL,
            {"name": "Default Case Pipeline", "create_default_stages": True},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        pipeline = CasePipeline.objects.get(name="Default Case Pipeline")
        assert pipeline.stages.count() == 5

    def test_create_pipeline_non_admin_forbidden(self, user_client):
        """Non-admin should not be able to create a pipeline."""
        response = user_client.post(
            PIPELINES_URL,
            {"name": "Blocked Pipeline", "create_default_stages": False},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_pipeline(self, admin_client, pipeline):
        """Admin should be able to update a pipeline name."""
        response = admin_client.put(
            _pipeline_detail_url(pipeline.pk),
            {"name": "Renamed Pipeline"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Renamed Pipeline"

    def test_update_pipeline_non_admin_forbidden(self, user_client, pipeline):
        """Non-admin should not be able to update a pipeline."""
        response = user_client.put(
            _pipeline_detail_url(pipeline.pk),
            {"name": "Should Fail"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_pipeline_with_cases_returns_400(
        self, admin_client, admin_user, org_a, pipeline, stage_open
    ):
        """Deleting a pipeline with cases linked to its stages should fail."""
        Case.objects.create(
            name="Blocking Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            stage=stage_open,
        )
        response = admin_client.delete(_pipeline_detail_url(pipeline.pk))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Cannot delete pipeline" in response.data["error"]

    def test_delete_pipeline_non_admin_forbidden(self, user_client, pipeline):
        """Non-admin should not be able to delete a pipeline."""
        response = user_client.delete(_pipeline_detail_url(pipeline.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCaseStage:
    """Tests for stage CRUD and reorder."""

    def test_create_stage(self, admin_client, pipeline):
        response = admin_client.post(
            _stage_create_url(pipeline.pk),
            {
                "name": "In Progress",
                "order": 2,
                "color": "#F59E0B",
                "stage_type": "open",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "In Progress"

    def test_update_stage(self, admin_client, stage_open):
        response = admin_client.put(
            _stage_detail_url(stage_open.pk),
            {
                "name": "Triaged",
                "color": "#8B5CF6",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        stage_open.refresh_from_db()
        assert stage_open.name == "Triaged"

    def test_delete_empty_stage(self, admin_client, stage_open):
        """Deleting a stage with no cases should succeed with 204."""
        response = admin_client.delete(_stage_detail_url(stage_open.pk))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not CaseStage.objects.filter(pk=stage_open.pk).exists()

    def test_reorder_stages(self, admin_client, pipeline, stage_open, stage_closed):
        """Reorder stages so that 'Resolved' comes before 'New'."""
        response = admin_client.post(
            _stage_reorder_url(pipeline.pk),
            {
                "stage_ids": [str(stage_closed.pk), str(stage_open.pk)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        stage_closed.refresh_from_db()
        stage_open.refresh_from_db()
        assert stage_closed.order < stage_open.order

    def test_create_stage_non_admin_forbidden(self, user_client, pipeline):
        """Non-admin should not be able to create a stage."""
        response = user_client.post(
            _stage_create_url(pipeline.pk),
            {"name": "Blocked Stage", "order": 5, "stage_type": "open"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_stage_non_admin_forbidden(self, user_client, stage_open):
        """Non-admin should not be able to update a stage."""
        response = user_client.put(
            _stage_detail_url(stage_open.pk),
            {"name": "Cannot Update"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_stage_non_admin_forbidden(self, user_client, stage_open):
        """Non-admin should not be able to delete a stage."""
        response = user_client.delete(_stage_detail_url(stage_open.pk))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_stage_with_cases_returns_400(
        self, admin_client, admin_user, org_a, stage_open
    ):
        """Deleting a stage with cases should fail with 400."""
        Case.objects.create(
            name="Stage Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            stage=stage_open,
        )
        response = admin_client.delete(_stage_detail_url(stage_open.pk))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Cannot delete stage" in response.data["error"]

    def test_reorder_stages_invalid_ids(self, admin_client, pipeline, stage_open):
        """Reorder with invalid stage IDs should fail."""
        response = admin_client.post(
            _stage_reorder_url(pipeline.pk),
            {"stage_ids": ["00000000-0000-0000-0000-000000000001"]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid stage IDs" in response.data["error"]

    def test_reorder_stages_non_admin_forbidden(
        self, user_client, pipeline, stage_open
    ):
        """Non-admin should not be able to reorder stages."""
        response = user_client.post(
            _stage_reorder_url(pipeline.pk),
            {"stage_ids": [str(stage_open.pk)]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCaseKanban:
    """Tests for the kanban board view: GET /api/cases/kanban/"""

    def test_kanban_view(self, admin_client, org_a, admin_user):
        """The default status-based kanban view should return columns."""
        # Create a case so there is data (RLS context set by _set_rls)
        _set_rls(org_a)
        Case.objects.create(
            name="Kanban test case",
            status="New",
            priority="Normal",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get(KANBAN_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["mode"] == "status"
        assert "columns" in response.data
        assert response.data["total_cases"] >= 1

    def test_kanban_pipeline_mode(
        self, admin_client, admin_user, org_a, pipeline, stage_open
    ):
        """Kanban with pipeline_id should return pipeline-based columns."""
        Case.objects.create(
            name="Pipeline Kanban Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            stage=stage_open,
        )
        response = admin_client.get(f"{KANBAN_URL}?pipeline_id={pipeline.id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["mode"] == "pipeline"
        assert data["pipeline"] is not None
        assert len(data["columns"]) >= 1

    def test_kanban_filter_by_priority(self, admin_client, admin_user, org_a):
        """Kanban should filter cases by priority."""
        Case.objects.create(
            name="Urgent Kanban Case",
            status="New",
            priority="Urgent",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Low Kanban Case",
            status="New",
            priority="Low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{KANBAN_URL}?priority=Urgent")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_cases"] == 1

    def test_kanban_filter_by_search(self, admin_client, admin_user, org_a):
        """Kanban should filter by search in name and description."""
        Case.objects.create(
            name="Login Issue",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Payment Problem",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{KANBAN_URL}?search=Login")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_cases"] == 1

    def test_kanban_filter_by_case_type(self, admin_client, admin_user, org_a):
        """Kanban should filter by case_type."""
        Case.objects.create(
            name="Question Case K",
            status="New",
            priority="Normal",
            case_type="Question",
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Incident Case K",
            status="New",
            priority="Normal",
            case_type="Incident",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{KANBAN_URL}?case_type=Question")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_cases"] == 1

    def test_kanban_filter_by_assigned_to(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """Kanban should filter by assigned_to."""
        case = Case.objects.create(
            name="Assigned Kanban Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.assigned_to.add(admin_profile)
        Case.objects.create(
            name="Unassigned Kanban Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"{KANBAN_URL}?assigned_to={admin_profile.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_cases"] == 1

    def test_kanban_non_admin_sees_only_assigned(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin should only see cases they are assigned to in kanban.

        Note: ORM-created objects have created_by=None (crum middleware not
        active in tests), so only assigned_to filtering works for non-admin.
        """
        Case.objects.create(
            name="Admin Kanban Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        assigned_case = Case.objects.create(
            name="User Assigned Kanban Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        assigned_case.assigned_to.add(user_profile)
        response = user_client.get(KANBAN_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_cases"] == 1

    def test_kanban_columns_sorted(self, admin_client, admin_user, org_a):
        """Kanban columns should be sorted by order."""
        response = admin_client.get(KANBAN_URL)
        assert response.status_code == status.HTTP_200_OK
        columns = response.data["columns"]
        orders = [c["order"] for c in columns]
        assert orders == sorted(orders)


@pytest.mark.django_db
class TestCaseMove:
    """Tests for PATCH /api/cases/<pk>/move/."""

    def test_move_case_status(self, admin_client, admin_user, org_a):
        """Move case to a different status column."""
        case = Case.objects.create(
            name="Move Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/",
            {"status": "Assigned"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["error"] is False
        case.refresh_from_db()
        assert case.status == "Assigned"

    def test_move_case_to_stage(
        self, admin_client, admin_user, org_a, pipeline, stage_closed
    ):
        """Move case to a pipeline stage with maps_to_status."""
        stage_closed.maps_to_status = "Closed"
        stage_closed.save()
        case = Case.objects.create(
            name="Stage Move Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            closed_on="2026-01-01",
        )
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/",
            {"stage_id": str(stage_closed.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case.refresh_from_db()
        assert case.stage == stage_closed
        assert case.status == "Closed"

    def test_move_case_with_kanban_order(self, admin_client, admin_user, org_a):
        """Move case with explicit kanban_order."""
        case = Case.objects.create(
            name="Order Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/",
            {"status": "New", "kanban_order": "5000.000000"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case.refresh_from_db()
        assert float(case.kanban_order) == 5000.0

    def test_move_case_wip_limit_exceeded(
        self, admin_client, admin_user, org_a, pipeline
    ):
        """Moving a case to a stage at WIP limit should fail."""
        stage = CaseStage.objects.create(
            pipeline=pipeline,
            name="Capped",
            order=5,
            wip_limit=1,
            org=org_a,
            created_by=admin_user,
        )
        Case.objects.create(
            name="Existing Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            stage=stage,
        )
        new_case = Case.objects.create(
            name="Overflow Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/cases/{new_case.id}/move/",
            {"stage_id": str(stage.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "WIP limit" in response.data["error"]

    def test_move_case_invalid_data(self, admin_client, admin_user, org_a):
        """Moving without stage_id or status should fail."""
        case = Case.objects.create(
            name="Invalid Move Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_move_case_non_admin_permission_denied(
        self, user_client, admin_user, org_a
    ):
        """Non-admin who is not creator/assignee should get 403."""
        case = Case.objects.create(
            name="Move Deny Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.patch(
            f"/api/cases/{case.id}/move/",
            {"status": "Assigned"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_move_case_non_admin_as_assignee_allowed(
        self, user_client, admin_user, user_profile, org_a
    ):
        """Non-admin who is assigned should be able to move a case."""
        case = Case.objects.create(
            name="Assignee Move Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        case.assigned_to.add(user_profile)
        response = user_client.patch(
            f"/api/cases/{case.id}/move/",
            {"status": "Assigned"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_move_case_non_admin_as_creator_allowed(
        self, user_client, regular_user, user_profile, org_a
    ):
        """Creator, not assigned to themselves. The untested half everywhere.

        The assignee case above was covered; this one was not, in any module.
        It is the branch that shipped dead on the opportunities board, where a
        Profile was compared to a User FK and so never matched.
        """
        case = Case.objects.create(
            name="Creator Move Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=regular_user,
        )
        assert user_profile not in case.assigned_to.all()

        response = user_client.patch(
            f"/api/cases/{case.id}/move/",
            {"status": "Assigned"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        case.refresh_from_db()
        assert case.status == "Assigned"

    def test_move_case_between_with_above_and_below(
        self, admin_client, admin_user, org_a
    ):
        """Move case between two cases using above_case_id and below_case_id."""
        case_a = Case.objects.create(
            name="Case A",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            kanban_order=1000,
        )
        case_b = Case.objects.create(
            name="Case B",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            kanban_order=3000,
        )
        case_c = Case.objects.create(
            name="Case C",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.patch(
            f"/api/cases/{case_c.id}/move/",
            {
                "status": "New",
                "above_case_id": str(case_a.id),
                "below_case_id": str(case_b.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case_c.refresh_from_db()
        assert 1000 < float(case_c.kanban_order) < 3000

    def test_move_case_clear_stage(
        self, admin_client, admin_user, org_a, pipeline, stage_open
    ):
        """Move case to stage_id=null should clear the stage."""
        case = Case.objects.create(
            name="Clear Stage Case",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            stage=stage_open,
        )
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/",
            {"stage_id": None, "status": "New"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        case.refresh_from_db()
        assert case.stage is None
