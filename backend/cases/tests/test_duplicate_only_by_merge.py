"""A ticket reaches Duplicate only through the merge endpoint.

Duplicate hides a ticket from the list and the board. The merge is what
records where it went; set any other way, the ticket just vanished.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from cases.models import Case, CasePipeline, CaseStage
from cases.workflow import DUPLICATE_BY_MERGE_ONLY


@pytest.fixture
def pipeline(org_a, admin_user):
    return CasePipeline.objects.create(name="Support", org=org_a, created_by=admin_user)


def _case(org, **fields):
    fields = {"name": "Printer", "status": "New", "priority": "Normal", **fields}
    return Case.objects.create(org=org, **fields)


@pytest.mark.django_db
class TestBoardMove:
    def test_status_duplicate_is_refused(self, admin_client, org_a):
        case = _case(org_a)
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/", {"status": "Duplicate"}, format="json"
        )
        assert response.status_code == 400
        assert DUPLICATE_BY_MERGE_ONLY in str(response.json())
        case.refresh_from_db()
        assert case.status == "New"

    def test_other_status_still_moves(self, admin_client, org_a):
        case = _case(org_a)
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/", {"status": "Pending"}, format="json"
        )
        assert response.status_code == 200
        case.refresh_from_db()
        assert case.status == "Pending"

    def test_stage_mapped_to_duplicate_is_refused(self, admin_client, org_a, pipeline):
        # A mapping stored before the stage serializer refused it.
        stage = CaseStage.objects.create(
            pipeline=pipeline,
            name="Dupes",
            order=0,
            org=org_a,
            maps_to_status="Duplicate",
        )
        case = _case(org_a)
        response = admin_client.patch(
            f"/api/cases/{case.id}/move/", {"stage_id": str(stage.id)}, format="json"
        )
        assert response.status_code == 400
        assert response.json()["errors"] == {"status": [DUPLICATE_BY_MERGE_ONLY]}
        case.refresh_from_db()
        assert (case.status, case.stage_id) == ("New", None)


@pytest.mark.django_db
class TestStageMapping:
    def test_create_mapped_to_duplicate_is_refused(self, admin_client, pipeline):
        response = admin_client.post(
            f"/api/cases/pipelines/{pipeline.id}/stages/",
            {"name": "Dupes", "maps_to_status": "Duplicate"},
            format="json",
        )
        assert response.status_code == 400
        assert not pipeline.stages.exists()

    def test_update_to_duplicate_is_refused(self, admin_client, org_a, pipeline):
        stage = CaseStage.objects.create(
            pipeline=pipeline, name="Done", order=0, org=org_a, maps_to_status="Closed"
        )
        response = admin_client.put(
            f"/api/cases/stages/{stage.id}/",
            {"maps_to_status": "Duplicate"},
            format="json",
        )
        assert response.status_code == 400
        stage.refresh_from_db()
        assert stage.maps_to_status == "Closed"

    def test_other_mapping_is_accepted(self, admin_client, pipeline):
        response = admin_client.post(
            f"/api/cases/pipelines/{pipeline.id}/stages/",
            {"name": "Waiting", "maps_to_status": "Pending"},
            format="json",
        )
        assert response.status_code == 201
        assert pipeline.stages.get().maps_to_status == "Pending"


@pytest.mark.django_db
class TestTicketWrites:
    def test_create_as_duplicate_is_refused(self, admin_client, org_a):
        response = admin_client.post(
            "/api/cases/",
            {"name": "Ghost", "status": "Duplicate", "priority": "Normal"},
            format="json",
        )
        assert response.status_code == 400
        assert not Case.objects.filter(org=org_a, name="Ghost").exists()

    def test_edit_into_duplicate_is_refused(self, admin_client, org_a):
        case = _case(org_a)
        response = admin_client.patch(
            f"/api/cases/{case.id}/", {"status": "Duplicate"}, format="json"
        )
        assert response.status_code == 400
        case.refresh_from_db()
        assert case.status == "New"

    def test_a_merged_ticket_can_still_be_edited(self, admin_client, org_a):
        primary = _case(org_a, name="Primary")
        duplicate = _case(org_a, status="Duplicate", merged_into=primary)
        response = admin_client.patch(
            f"/api/cases/{duplicate.id}/",
            {"status": "Duplicate", "description": "see primary"},
            format="json",
        )
        assert response.status_code == 200, response.json()
        duplicate.refresh_from_db()
        assert duplicate.description == "see primary"

    def test_bulk_update_to_duplicate_is_refused(self, admin_client, org_a):
        case = _case(org_a)
        response = admin_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(case.id)], "fields": {"status": "Duplicate"}},
            format="json",
        )
        assert response.status_code == 400
        case.refresh_from_db()
        assert case.status == "New"

    def test_import_row_as_duplicate_is_refused(self, admin_client, org_a):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "status", "priority"])
        writer.writerow(["Imported", "Duplicate", "Normal"])
        upload = SimpleUploadedFile(
            "t.csv", buf.getvalue().encode(), content_type="text/csv"
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": upload}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert {"row": 1, "field": "status"}.items() <= body["errors"][0].items()
        assert body["errors"][0]["message"] == DUPLICATE_BY_MERGE_ONLY
