"""A ticket-name clash is judged among tickets the caller can open (D48).

Ticket names carry no DB constraint (``Case.name`` is a plain ``CharField``
with no unique index or ``UniqueConstraint``), so refusing a duplicate name is
a courtesy against filing the same ticket twice. Both places that offered it
checked the whole org: the CSV import answered "A ticket with this name
already exists" and ``POST /api/cases/`` answered "Case already exists with
this name" for a name held only by a ticket the caller cannot open, which
confirmed that ticket existed. Both now look only at ``visible_cases_qs``.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from cases.models import Case

PREVIEW = "/api/cases/import/preview/"
IMPORT_CLASH = "A ticket with this name already exists"
CREATE_CLASH = "Case already exists with this name"


def _csv(names):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "status", "priority"])
    for name in names:
        writer.writerow([name, "New", "High"])
    return SimpleUploadedFile(
        "tickets.csv", buf.getvalue().encode("utf-8"), content_type="text/csv"
    )


@pytest.fixture
def importer(user_profile):
    """A member with sales access, which `can_mass_import` admits."""
    user_profile.has_sales_access = True
    user_profile.save(update_fields=["has_sales_access"])
    return user_profile


@pytest.fixture
def hidden(org_a, admin_user):
    return Case.objects.create(
        name="Hidden outage",
        status="New",
        priority="High",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def mine(org_a, regular_user):
    return Case.objects.create(
        name="My outage",
        status="New",
        priority="High",
        org=org_a,
        created_by=regular_user,
    )


def _preview_errors(client, names):
    response = client.post(PREVIEW, {"file": _csv(names)}, format="multipart")
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.django_db
class TestImportNameClash:
    def test_hidden_ticket_name_is_not_reported(self, user_client, importer, hidden):
        body = _preview_errors(user_client, ["hidden OUTAGE"])
        assert body["errors"] == []
        assert body["summary"]["valid"] == 1

    def test_visible_ticket_name_is_reported(self, user_client, importer, mine):
        body = _preview_errors(user_client, ["my outage"])
        assert body["summary"]["valid"] == 0
        assert [(e["field"], e["message"]) for e in body["errors"]] == [
            ("name", IMPORT_CLASH)
        ]

    def test_admin_sees_every_clash_in_the_org(self, admin_client, hidden):
        body = _preview_errors(admin_client, ["Hidden outage"])
        assert [e["message"] for e in body["errors"]] == [IMPORT_CLASH]


def _create(client, name):
    return client.post(
        "/api/cases/",
        {"name": name, "status": "New", "priority": "High"},
        format="json",
    )


@pytest.mark.django_db
class TestCreateNameClash:
    def test_hidden_ticket_name_does_not_refuse_the_create(
        self, user_client, org_a, hidden
    ):
        response = _create(user_client, "Hidden outage")
        assert response.status_code == 200, response.content
        assert Case.objects.filter(org=org_a, name="Hidden outage").count() == 2

    def test_visible_ticket_name_refuses_the_create(self, user_client, org_a, mine):
        response = _create(user_client, "MY OUTAGE")
        assert response.status_code == 400, response.content
        assert response.json()["errors"]["name"] == [CREATE_CLASH]
        assert Case.objects.filter(org=org_a, name__iexact="my outage").count() == 1

    def test_rename_onto_a_visible_name_is_refused(
        self, user_client, org_a, regular_user, mine
    ):
        other = Case.objects.create(
            name="Second",
            status="New",
            priority="High",
            org=org_a,
            created_by=regular_user,
        )
        response = user_client.patch(
            f"/api/cases/{other.id}/", {"name": "My outage"}, format="json"
        )
        assert response.status_code == 400, response.content
        assert response.json()["errors"]["name"] == [CREATE_CLASH]

    def test_keeping_its_own_name_is_not_a_clash(self, user_client, mine):
        response = user_client.patch(
            f"/api/cases/{mine.id}/", {"name": "My outage"}, format="json"
        )
        assert response.status_code == 200, response.content
