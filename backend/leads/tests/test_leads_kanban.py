import pytest
from django.db import connection

from leads.models import Lead, LeadPipeline, LeadStage


def _set_rls(org):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.mark.django_db
class TestLeadPipelineListCreateView:
    """Tests for GET/POST /api/leads/pipelines/."""

    def test_create_pipeline(self, admin_client, org_a):
        response = admin_client.post(
            "/api/leads/pipelines/",
            {"name": "Sales Pipeline"},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Sales Pipeline"
        assert LeadPipeline.objects.filter(name="Sales Pipeline", org=org_a).exists()

    def test_list_pipelines(self, admin_client, admin_user, org_a):
        LeadPipeline.objects.create(name="Pipeline A", org=org_a, created_by=admin_user)
        response = admin_client.get("/api/leads/pipelines/")
        assert response.status_code == 200
        data = response.json()
        assert "pipelines" in data

    def test_create_pipeline_non_admin(self, user_client):
        response = user_client.post(
            "/api/leads/pipelines/",
            {"name": "Blocked Pipeline"},
            format="json",
        )
        assert response.status_code == 403

    def test_create_pipeline_with_default_stages(self, admin_client, org_a):
        """Creating a pipeline with create_default_stages=True adds 6 stages."""
        response = admin_client.post(
            "/api/leads/pipelines/",
            {"name": "Default Stages Pipeline", "create_default_stages": True},
            format="json",
        )
        assert response.status_code == 201
        pipeline = LeadPipeline.objects.get(name="Default Stages Pipeline")
        assert pipeline.stages.count() == 6

    def test_create_pipeline_without_default_stages(self, admin_client, org_a):
        """Creating a pipeline with create_default_stages=False adds no stages."""
        response = admin_client.post(
            "/api/leads/pipelines/",
            {"name": "No Stages Pipeline", "create_default_stages": False},
            format="json",
        )
        assert response.status_code == 201
        pipeline = LeadPipeline.objects.get(name="No Stages Pipeline")
        assert pipeline.stages.count() == 0

    def test_create_pipeline_invalid_data(self, admin_client, org_a):
        """Creating a pipeline with missing name returns 400."""
        response = admin_client.post(
            "/api/leads/pipelines/",
            {},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestLeadPipelineDetailView:
    """Tests for GET/PUT/DELETE /api/leads/pipelines/<pk>/."""

    def test_get_pipeline_detail(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Detail Pipeline", org=org_a, created_by=admin_user
        )
        response = admin_client.get(f"/api/leads/pipelines/{pipeline.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Detail Pipeline"

    def test_update_pipeline(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Old Name", org=org_a, created_by=admin_user
        )
        response = admin_client.put(
            f"/api/leads/pipelines/{pipeline.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"

    def test_delete_empty_pipeline(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Empty Pipeline", org=org_a, created_by=admin_user
        )
        response = admin_client.delete(f"/api/leads/pipelines/{pipeline.id}/")
        assert response.status_code == 204

    def test_delete_pipeline_with_leads_fails(self, admin_client, admin_user, org_a):
        """Cannot delete a pipeline that has leads associated via stages."""
        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(
            name="Busy Pipeline", org=org_a, created_by=admin_user
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="Active", order=0, org=org_a
        )
        Lead.objects.create(
            first_name="Busy",
            last_name="Lead",
            email="busylead@example.com",
            created_by=admin_user,
            org=org_a,
            stage=stage,
        )
        response = admin_client.delete(f"/api/leads/pipelines/{pipeline.id}/")
        assert response.status_code == 400

    def test_update_pipeline_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user cannot update a pipeline."""
        pipeline = LeadPipeline.objects.create(
            name="No Touch", org=org_a, created_by=admin_user
        )
        response = user_client.put(
            f"/api/leads/pipelines/{pipeline.id}/",
            {"name": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_pipeline_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user cannot delete a pipeline."""
        pipeline = LeadPipeline.objects.create(
            name="Protected Pipeline", org=org_a, created_by=admin_user
        )
        response = user_client.delete(f"/api/leads/pipelines/{pipeline.id}/")
        assert response.status_code == 403

    def test_update_pipeline_invalid_data(self, admin_client, admin_user, org_a):
        """PUT with invalid data returns 400."""
        pipeline = LeadPipeline.objects.create(
            name="Valid Name", org=org_a, created_by=admin_user
        )
        # Sending an empty name should fail
        response = admin_client.put(
            f"/api/leads/pipelines/{pipeline.id}/",
            {"name": ""},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestLeadStageViews:
    """Tests for stage creation, update, delete, and reorder."""

    def test_create_stage(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Stage Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {
                "name": "Qualification",
                "order": 1,
                "color": "#FF5733",
                "stage_type": "open",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Qualification"

    def test_update_stage(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Update Stage Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline,
            name="Initial",
            order=0,
            color="#000000",
            stage_type="open",
            org=org_a,
        )
        response = admin_client.put(
            f"/api/leads/stages/{stage.id}/",
            {"name": "Renamed Stage", "color": "#FFFFFF"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed Stage"

    def test_delete_empty_stage(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Delete Stage Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline,
            name="Disposable",
            order=0,
            color="#000000",
            stage_type="open",
            org=org_a,
        )
        response = admin_client.delete(f"/api/leads/stages/{stage.id}/")
        assert response.status_code == 204

    def test_reorder_stages(self, admin_client, admin_user, org_a):
        pipeline = LeadPipeline.objects.create(
            name="Reorder Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage_a = LeadStage.objects.create(
            pipeline=pipeline,
            name="Stage A",
            order=0,
            org=org_a,
        )
        stage_b = LeadStage.objects.create(
            pipeline=pipeline,
            name="Stage B",
            order=1,
            org=org_a,
        )
        # Reverse the order
        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": [str(stage_b.id), str(stage_a.id)]},
            format="json",
        )
        assert response.status_code == 200
        stage_a.refresh_from_db()
        stage_b.refresh_from_db()
        assert stage_b.order < stage_a.order

    def test_delete_stage_with_leads_fails(self, admin_client, admin_user, org_a):
        """Cannot delete a stage that has leads attached."""
        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(
            name="Stage With Leads Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="Busy Stage", order=0, org=org_a
        )
        Lead.objects.create(
            first_name="Staged",
            last_name="Lead",
            email="stagedlead@example.com",
            created_by=admin_user,
            org=org_a,
            stage=stage,
        )
        response = admin_client.delete(f"/api/leads/stages/{stage.id}/")
        assert response.status_code == 400

    def test_create_stage_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user cannot create a stage."""
        pipeline = LeadPipeline.objects.create(
            name="Admin Only Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {"name": "Blocked", "order": 1},
            format="json",
        )
        assert response.status_code == 403

    def test_update_stage_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user cannot update a stage."""
        pipeline = LeadPipeline.objects.create(
            name="No Update Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="No Touch", order=0, org=org_a
        )
        response = user_client.put(
            f"/api/leads/stages/{stage.id}/",
            {"name": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_stage_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user cannot delete a stage."""
        pipeline = LeadPipeline.objects.create(
            name="No Delete Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="Protected", order=0, org=org_a
        )
        response = user_client.delete(f"/api/leads/stages/{stage.id}/")
        assert response.status_code == 403

    def test_create_stage_invalid_data(self, admin_client, admin_user, org_a):
        """Creating a stage with missing required fields returns 400."""
        pipeline = LeadPipeline.objects.create(
            name="Invalid Stage Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {},
            format="json",
        )
        assert response.status_code == 400

    def test_reorder_stages_invalid_ids(self, admin_client, admin_user, org_a):
        """Reorder with invalid stage IDs returns 400."""
        pipeline = LeadPipeline.objects.create(
            name="Reorder Invalid Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        LeadStage.objects.create(pipeline=pipeline, name="Only", order=0, org=org_a)
        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": ["00000000-0000-0000-0000-000000000001"]},
            format="json",
        )
        assert response.status_code == 400

    def test_reorder_stages_non_admin_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin cannot reorder stages."""
        pipeline = LeadPipeline.objects.create(
            name="Reorder Forbidden Pipeline",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/reorder/",
            {"stage_ids": []},
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestLeadKanbanView:
    """Tests for GET /api/leads/kanban/."""

    def test_kanban_view(self, admin_client, org_a):
        response = admin_client.get("/api/leads/kanban/")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data

    def test_kanban_status_mode(self, admin_client, admin_user, org_a):
        """Kanban without pipeline_id returns status-based columns."""
        _set_rls(org_a)
        Lead.objects.create(
            first_name="Kanban",
            last_name="Lead",
            email="kanban@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get("/api/leads/kanban/")
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "status"
        assert data["pipeline"] is None
        assert data["total_leads"] >= 1
        # Status columns should exist
        column_ids = [col["id"] for col in data["columns"]]
        assert "assigned" in column_ids

    def test_kanban_pipeline_mode(self, admin_client, admin_user, org_a):
        """Kanban with pipeline_id returns pipeline-based columns."""
        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(
            name="Kanban Pipeline", org=org_a, created_by=admin_user
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline, name="New", order=1, org=org_a
        )
        Lead.objects.create(
            first_name="Pipe",
            last_name="Lead",
            email="pipelead@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
            stage=stage,
        )
        response = admin_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "pipeline"
        assert data["pipeline"] is not None
        assert len(data["columns"]) == 1
        assert data["columns"][0]["name"] == "New"

    def test_kanban_search_filter(self, admin_client, admin_user, org_a):
        """Kanban search filter works."""
        _set_rls(org_a)
        Lead.objects.create(
            first_name="Searchable",
            last_name="KanbanLead",
            email="searchkan@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get("/api/leads/kanban/", {"search": "Searchable"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_leads"] >= 1

    def test_kanban_rating_filter(self, admin_client, admin_user, org_a):
        """Kanban rating filter works."""
        _set_rls(org_a)
        Lead.objects.create(
            first_name="Hot",
            last_name="Lead",
            email="hotlead@example.com",
            status="assigned",
            rating="HOT",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.get("/api/leads/kanban/", {"rating": "HOT"})
        assert response.status_code == 200

    def test_kanban_non_admin_sees_assigned_leads(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin user only sees assigned leads in kanban."""
        _set_rls(org_a)
        Lead.objects.create(
            first_name="Admin",
            last_name="Kanban",
            email="adminkb@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        assigned_lead = Lead.objects.create(
            first_name="Assigned",
            last_name="Kanban",
            email="assignedkb@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        assigned_lead.assigned_to.add(user_profile)
        response = user_client.get("/api/leads/kanban/")
        assert response.status_code == 200
        data = response.json()
        all_lead_emails = []
        for col in data["columns"]:
            for lead in col["leads"]:
                all_lead_emails.append(lead["email"])
        assert "assignedkb@example.com" in all_lead_emails
        assert "adminkb@example.com" not in all_lead_emails


@pytest.mark.django_db
class TestLeadMoveView:
    """Tests for PATCH /api/leads/<pk>/move/."""

    def test_move_lead_status(self, admin_client, admin_user, org_a):
        """Move a lead to a different status column."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Move",
            last_name="Lead",
            email="movelead@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/",
            {"status": "in process"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["error"] is False
        lead.refresh_from_db()
        assert lead.status == "in process"

    def test_move_lead_to_stage(self, admin_client, admin_user, org_a):
        """Move a lead to a pipeline stage."""
        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(
            name="Move Pipeline", org=org_a, created_by=admin_user
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline,
            name="Contacted",
            order=1,
            org=org_a,
            maps_to_status="in process",
        )
        lead = Lead.objects.create(
            first_name="Stage",
            last_name="Move",
            email="stagemove@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/",
            {"stage_id": str(stage.id)},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == stage
        assert lead.status == "in process"  # maps_to_status applied

    def test_move_lead_wip_limit_exceeded(self, admin_client, admin_user, org_a):
        """Moving a lead to a stage at WIP limit returns 400."""
        _set_rls(org_a)
        pipeline = LeadPipeline.objects.create(
            name="WIP Pipeline", org=org_a, created_by=admin_user
        )
        stage = LeadStage.objects.create(
            pipeline=pipeline,
            name="Limited",
            order=1,
            org=org_a,
            wip_limit=1,
        )
        Lead.objects.create(
            first_name="Existing",
            last_name="Lead",
            email="existing@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
            stage=stage,
        )
        lead2 = Lead.objects.create(
            first_name="Overflow",
            last_name="Lead",
            email="overflow@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead2.id}/move/",
            {"stage_id": str(stage.id)},
            format="json",
        )
        assert response.status_code == 400

    def test_move_lead_invalid_data(self, admin_client, admin_user, org_a):
        """Move with neither stage_id nor status returns 400."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Invalid",
            last_name="Move",
            email="invalidmove@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/",
            {},
            format="json",
        )
        assert response.status_code == 400

    def test_move_lead_non_admin_not_assigned_forbidden(
        self, user_client, admin_user, org_a, user_profile
    ):
        """Non-admin not assigned/creator cannot move a lead."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="NoMove",
            last_name="Lead",
            email="nomove@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = user_client.patch(
            f"/api/leads/{lead.id}/move/",
            {"status": "closed"},
            format="json",
        )
        # 404, not 403: the move does not confirm a lead the board withholds.
        assert response.status_code == 404

    def test_move_lead_non_admin_as_creator_allowed(
        self, user_client, regular_user, org_a, user_profile
    ):
        """The creator half of the check, which nothing exercised.

        Every module tested the refusal, and cases and tasks tested the
        assignee who is allowed, but no move test anywhere covered a creator
        who is not also an assignee. That is exactly the branch that was dead
        on the opportunities board (it compared a Profile to a User FK, so it
        was always False) and shipped that way. This pins the answer here.
        """
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Mine",
            last_name="Lead",
            email="minelead@example.com",
            status="assigned",
            created_by=regular_user,
            org=org_a,
        )
        assert user_profile not in lead.assigned_to.all()

        response = user_client.patch(
            f"/api/leads/{lead.id}/move/",
            {"status": "closed"},
            format="json",
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.status == "closed"

    def test_move_lead_with_explicit_order(self, admin_client, admin_user, org_a):
        """Move lead with explicit kanban_order."""
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Order",
            last_name="Lead",
            email="orderlead@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/",
            {"status": "in process", "kanban_order": "5000.000000"},
            format="json",
        )
        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.kanban_order == 5000

    def test_move_lead_between_two_leads(self, admin_client, admin_user, org_a):
        """Move lead between two existing leads using above/below hints."""
        _set_rls(org_a)
        lead_above = Lead.objects.create(
            first_name="Above",
            last_name="Lead",
            email="above@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
            kanban_order=1000,
        )
        lead_below = Lead.objects.create(
            first_name="Below",
            last_name="Lead",
            email="below@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
            kanban_order=2000,
        )
        lead_to_move = Lead.objects.create(
            first_name="Moving",
            last_name="Lead",
            email="moving@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )
        response = admin_client.patch(
            f"/api/leads/{lead_to_move.id}/move/",
            {
                "status": "assigned",
                "above_lead_id": str(lead_above.id),
                "below_lead_id": str(lead_below.id),
            },
            format="json",
        )
        assert response.status_code == 200
        lead_to_move.refresh_from_db()
        assert lead_to_move.kanban_order == 1500  # midpoint


def _lead(org, created_by, email, **extra):
    return Lead.objects.create(
        first_name="Board",
        last_name="Lead",
        email=email,
        status=extra.pop("status", "assigned"),
        created_by=created_by,
        org=org,
        **extra,
    )


def _pipeline(org, created_by, name="Admissions", *stage_names, **pipeline_extra):
    pipeline = LeadPipeline.objects.create(
        name=name, org=org, created_by=created_by, **pipeline_extra
    )
    stages = [
        LeadStage.objects.create(pipeline=pipeline, name=n, order=i, org=org)
        for i, n in enumerate(stage_names or ("New enquiry", "Admitted"), start=1)
    ]
    return pipeline, stages


def _board_emails(data):
    emails = [lead["email"] for col in data["columns"] for lead in col["leads"]]
    if data.get("unstaged"):
        emails += [lead["email"] for lead in data["unstaged"]["leads"]]
    return emails


@pytest.mark.django_db
class TestLeadPipelineBoard:
    """The pipeline board that a pack's lead pipeline is shown on (D5)."""

    def test_unstaged_leads_ride_along_so_they_can_enter_the_pipeline(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "staged@example.com", stage=new)
        _lead(org_a, admin_user, "unstaged@example.com")

        response = admin_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )

        assert response.status_code == 200
        data = response.json()
        assert [c["name"] for c in data["columns"]] == ["New enquiry", "Admitted"]
        assert [lead["email"] for lead in data["columns"][0]["leads"]] == [
            "staged@example.com"
        ]
        assert data["unstaged"]["lead_count"] == 1
        assert [lead["email"] for lead in data["unstaged"]["leads"]] == [
            "unstaged@example.com"
        ]

    def test_non_admin_board_hides_leads_the_list_hides(
        self, user_client, admin_user, regular_user, org_a, user_profile
    ):
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "hidden-staged@example.com", stage=new)
        _lead(org_a, admin_user, "hidden-unstaged@example.com")
        mine = _lead(org_a, admin_user, "assigned@example.com", stage=new)
        mine.assigned_to.add(user_profile)
        _lead(org_a, regular_user, "created@example.com")

        response = user_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )

        assert response.status_code == 200
        data = response.json()
        emails = _board_emails(data)
        assert sorted(emails) == ["assigned@example.com", "created@example.com"]
        assert data["columns"][0]["lead_count"] == 1
        assert data["unstaged"]["lead_count"] == 1

    def test_non_admin_sees_own_lead_once_despite_other_assignees(
        self, user_client, admin_profile, regular_user, org_a, user_profile
    ):
        """The assignee join used to repeat a lead once per assignee."""
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, regular_user)
        lead = _lead(org_a, regular_user, "mine@example.com", stage=new)
        lead.assigned_to.add(admin_profile, user_profile)

        response = user_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )

        data = response.json()
        assert _board_emails(data) == ["mine@example.com"]
        assert data["columns"][0]["lead_count"] == 1
        assert data["total_leads"] == 1

    def test_another_orgs_pipeline_is_404(self, org_b_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, _ = _pipeline(org_a, admin_user)

        response = org_b_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )

        assert response.status_code == 404

    def test_inactive_pipeline_is_404(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, _ = _pipeline(org_a, admin_user, is_active=False)

        response = admin_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        )

        assert response.status_code == 404

    def test_malformed_pipeline_id_is_400(self, admin_client):
        response = admin_client.get("/api/leads/kanban/", {"pipeline_id": "nope"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestLeadMoveGuards:
    """What the board may and may not do through PATCH /leads/<id>/move/."""

    def _move(self, client, lead, **body):
        return client.patch(f"/api/leads/{lead.id}/move/", body, format="json")

    def test_unstaged_lead_enters_a_pipeline(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _pipeline_obj, (new, _admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "enter@example.com")

        response = self._move(admin_client, lead, stage_id=str(new.id))

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == new

    def test_moves_within_its_own_pipeline(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _pipeline_obj, (new, admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "within@example.com", stage=new)

        response = self._move(admin_client, lead, stage_id=str(admitted.id))

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == admitted

    def test_refuses_a_stage_from_another_pipeline(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        _p1, (new, _admitted) = _pipeline(org_a, admin_user, "Admissions")
        _p2, (other, _x) = _pipeline(org_a, admin_user, "Inbound", "Fresh", "Done")
        lead = _lead(org_a, admin_user, "cross@example.com", stage=new)

        response = self._move(admin_client, lead, stage_id=str(other.id))

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.stage == new

    def test_refuses_a_stage_of_an_inactive_pipeline(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        _p, (gone, _x) = _pipeline(org_a, admin_user, is_active=False)
        lead = _lead(org_a, admin_user, "gone@example.com")

        response = self._move(admin_client, lead, stage_id=str(gone.id))

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.stage is None

    def test_refuses_another_orgs_stage(
        self, admin_client, admin_user, user_b, org_a, org_b
    ):
        _set_rls(org_b)
        _p, (foreign, _x) = _pipeline(org_b, user_b)
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "foreign@example.com")

        response = self._move(admin_client, lead, stage_id=str(foreign.id))

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.stage is None

    def test_malformed_stage_id_is_400(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "malformed@example.com")

        response = self._move(admin_client, lead, stage_id="not-a-uuid")

        assert response.status_code == 400

    def test_assignee_may_move_to_a_stage(
        self, user_client, admin_user, org_a, user_profile
    ):
        _set_rls(org_a)
        _p, (new, _admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "assignee@example.com")
        lead.assigned_to.add(user_profile)

        response = self._move(user_client, lead, stage_id=str(new.id))

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage == new

    def test_non_owner_may_not_move_to_a_stage(
        self, user_client, admin_user, org_a, user_profile
    ):
        _set_rls(org_a)
        _p, (new, _admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "notmine@example.com")

        response = self._move(user_client, lead, stage_id=str(new.id))

        assert response.status_code == 404
        lead.refresh_from_db()
        assert lead.stage is None

    def test_refuses_to_move_a_converted_lead(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        _p, (new, _admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "done@example.com", status="converted")

        response = self._move(admin_client, lead, stage_id=str(new.id))

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "converted"
        assert lead.stage is None

    def test_refuses_to_convert_by_status(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "sneak@example.com")

        response = self._move(admin_client, lead, status="converted")

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "assigned"

    def test_refuses_a_stage_that_maps_to_converted(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        pipeline, _ = _pipeline(org_a, admin_user)
        won = LeadStage.objects.create(
            pipeline=pipeline,
            name="Won",
            order=9,
            org=org_a,
            stage_type="won",
            maps_to_status="converted",
        )
        lead = _lead(org_a, admin_user, "won@example.com")

        response = self._move(admin_client, lead, stage_id=str(won.id))

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.status == "assigned"
        assert lead.stage is None


@pytest.mark.django_db
class TestLeadDetailPipelineStage:
    def test_detail_names_the_stage_and_pipeline(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, admin_user)
        lead = _lead(org_a, admin_user, "detail@example.com", stage=new)

        response = admin_client.get(f"/api/leads/{lead.id}/")

        assert response.status_code == 200
        assert response.json()["pipeline_stage"] == {
            "id": str(new.id),
            "name": "New enquiry",
            "color": new.color,
            "stage_type": "open",
            "pipeline": {"id": str(pipeline.id), "name": "Admissions"},
        }

    def test_detail_stage_is_null_outside_any_pipeline(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        lead = _lead(org_a, admin_user, "nostage@example.com")

        response = admin_client.get(f"/api/leads/{lead.id}/")

        assert response.status_code == 200
        assert response.json()["pipeline_stage"] is None


@pytest.mark.django_db
class TestLeadPipelineCountsFollowVisibility:
    """``lead_count`` on a pipeline read counts only leads the caller can see.

    It used to count every lead in the org, so a member could learn how many
    leads were withheld from them. It is scoped by ``leads.access.visible_leads_qs``
    now, the rule the lead list and the board use, in all four places it
    appears: the pipeline list, the pipeline detail, each of its nested stages,
    and the pipeline block on the board.
    """

    def _seed(self, admin_user, org_a, user_profile, admin_profile):
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, admin_user)
        _lead(org_a, admin_user, "hidden@example.com", stage=new)
        mine = _lead(org_a, admin_user, "mine@example.com", stage=new)
        # Two assignees: a join on the M2M would count this lead twice.
        mine.assigned_to.add(user_profile, admin_profile)
        return pipeline

    def _counts(self, client, pipeline):
        """lead_count from the list, the detail, its first stage and the board."""
        (row,) = client.get("/api/leads/pipelines/").json()["pipelines"]
        assert row["id"] == str(pipeline.id)
        detail = client.get(f"/api/leads/pipelines/{pipeline.id}/").json()
        board = client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        ).json()
        assert [s["lead_count"] for s in detail["stages"][1:]] == [0]
        return (
            row["lead_count"],
            detail["lead_count"],
            detail["stages"][0]["lead_count"],
            board["pipeline"]["lead_count"],
        )

    def test_member_counts_only_visible_leads(
        self, user_client, admin_user, org_a, user_profile, admin_profile
    ):
        pipeline = self._seed(admin_user, org_a, user_profile, admin_profile)

        assert self._counts(user_client, pipeline) == (1, 1, 1, 1)

    def test_admin_counts_every_lead(
        self, admin_client, admin_user, org_a, user_profile, admin_profile
    ):
        pipeline = self._seed(admin_user, org_a, user_profile, admin_profile)

        assert self._counts(admin_client, pipeline) == (2, 2, 2, 2)
        board = admin_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        ).json()
        assert board["columns"][0]["lead_count"] == 2
        assert board["pipeline"]["stage_count"] == 2

    def test_member_list_board_and_pipeline_agree(
        self, user_client, admin_user, org_a, user_profile, admin_profile
    ):
        pipeline = self._seed(admin_user, org_a, user_profile, admin_profile)

        listed = user_client.get("/api/leads/").json()
        status_board = user_client.get("/api/leads/kanban/").json()
        pipeline_board = user_client.get(
            "/api/leads/kanban/", {"pipeline_id": str(pipeline.id)}
        ).json()

        assert listed["totals"]["count"] == 1
        assert status_board["total_leads"] == 1
        assert pipeline_board["columns"][0]["lead_count"] == 1
        assert pipeline_board["pipeline"]["lead_count"] == 1

    def test_write_responses_carry_lead_count(self, admin_client, admin_user, org_a):
        _set_rls(org_a)
        created = admin_client.post(
            "/api/leads/pipelines/", {"name": "Fresh"}, format="json"
        ).json()
        assert created["lead_count"] == 0
        assert {s["lead_count"] for s in created["stages"]} == {0}

        pipeline, (new, _admitted) = _pipeline(org_a, admin_user, "Other")
        _lead(org_a, admin_user, "staged@example.com", stage=new)
        updated = admin_client.put(
            f"/api/leads/stages/{new.id}/", {"name": "Renamed"}, format="json"
        ).json()
        assert updated["lead_count"] == 1

    def test_pipeline_list_query_count_does_not_grow_per_pipeline(
        self, admin_client, admin_user, org_a, user_profile, admin_profile
    ):
        from django.test.utils import CaptureQueriesContext

        self._seed(admin_user, org_a, user_profile, admin_profile)
        with CaptureQueriesContext(connection) as one:
            admin_client.get("/api/leads/pipelines/")

        for name in ("Second", "Third"):
            _p, (stage, _s) = _pipeline(org_a, admin_user, name)
            _lead(org_a, admin_user, f"{name}@example.com", stage=stage)
        with CaptureQueriesContext(connection) as three:
            response = admin_client.get("/api/leads/pipelines/")

        assert len(response.json()["pipelines"]) == 3
        assert len(three.captured_queries) == len(one.captured_queries)


@pytest.mark.django_db
class TestLeadStageWinProbabilityBounds:
    """A stage's win probability is copied onto a lead moved into it, so it
    has to fit the lead's 0-100 check constraint. Out of range, it used to be
    stored and then turned the next board move into an IntegrityError 500."""

    @pytest.mark.parametrize("value", [150, -5])
    def test_create_refuses_out_of_range(self, admin_client, admin_user, org_a, value):
        _set_rls(org_a)
        pipeline, _stages = _pipeline(org_a, admin_user)

        response = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {"name": "Bad", "order": 9, "win_probability": value},
            format="json",
        )

        assert response.status_code == 400
        assert "win_probability" in response.json()["errors"]
        assert not LeadStage.objects.filter(pipeline=pipeline, name="Bad").exists()

    @pytest.mark.parametrize("value", [150, -5])
    def test_update_refuses_out_of_range(self, admin_client, admin_user, org_a, value):
        _set_rls(org_a)
        _pipeline_obj, (new, _admitted) = _pipeline(org_a, admin_user)

        response = admin_client.put(
            f"/api/leads/stages/{new.id}/",
            {"win_probability": value},
            format="json",
        )

        assert response.status_code == 400
        assert "win_probability" in response.json()["errors"]
        new.refresh_from_db()
        assert new.win_probability == 0

    @pytest.mark.parametrize("value", [0, 100])
    def test_create_and_update_accept_the_bounds(
        self, admin_client, admin_user, org_a, value
    ):
        _set_rls(org_a)
        pipeline, (new, _admitted) = _pipeline(org_a, admin_user)

        created = admin_client.post(
            f"/api/leads/pipelines/{pipeline.id}/stages/",
            {"name": "Edge", "order": 9, "win_probability": value},
            format="json",
        )
        updated = admin_client.put(
            f"/api/leads/stages/{new.id}/",
            {"win_probability": value},
            format="json",
        )

        assert created.status_code == 201
        assert created.json()["win_probability"] == value
        assert updated.status_code == 200
        assert updated.json()["win_probability"] == value

    def test_move_into_a_full_probability_stage_sets_the_lead(
        self, admin_client, admin_user, org_a
    ):
        _set_rls(org_a)
        _pipeline_obj, (new, _admitted) = _pipeline(org_a, admin_user)
        admin_client.put(
            f"/api/leads/stages/{new.id}/", {"win_probability": 100}, format="json"
        )
        lead = _lead(org_a, admin_user, "certain@example.com")

        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/", {"stage_id": str(new.id)}, format="json"
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.probability == 100
