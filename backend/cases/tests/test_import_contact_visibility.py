"""The ticket import preview does not reveal whether a contact exists.

`can_mass_import` admits members with `has_sales_access`, not only admins. The
preview resolved `contact_emails` across the whole org, so such a member got
"No contact with email ..." for an address nobody holds and a valid row for
one held by a contact they cannot open: an existence oracle for any address.
Contact emails now resolve only among contacts the importer may open, so
both come back as the same row error.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from cases.models import Case
from contacts.models import Contact

PREVIEW = "/api/cases/import/preview/"
COMMIT = "/api/cases/import/commit/"


def _csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "status", "priority", "contact_emails"])
    writer.writerows(rows)
    return SimpleUploadedFile(
        "tickets.csv", buf.getvalue().encode("utf-8"), content_type="text/csv"
    )


@pytest.fixture
def importer(user_profile):
    user_profile.has_sales_access = True
    user_profile.save(update_fields=["has_sales_access"])
    return user_profile


@pytest.fixture
def hidden(org_a, admin_user):
    return Contact.objects.create(
        first_name="Hidden",
        last_name="Person",
        email="hidden@x.test",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def mine(org_a, regular_user):
    return Contact.objects.create(
        first_name="Mine",
        last_name="Visible",
        email="mine@x.test",
        org=org_a,
        created_by=regular_user,
    )


def _messages(body):
    return {e["row"]: e["message"] for e in body["errors"]}


@pytest.mark.django_db
class TestImportContactVisibility:
    def test_hidden_and_missing_contacts_get_the_same_error(
        self, user_client, importer, hidden
    ):
        response = user_client.post(
            PREVIEW,
            {
                "file": _csv(
                    [
                        ["Hidden", "New", "High", "hidden@x.test"],
                        ["Missing", "New", "High", "nobody@x.test"],
                    ]
                )
            },
            format="multipart",
        )

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["summary"]["valid"] == 0
        messages = _messages(body)
        assert messages[1].replace("hidden@x.test", "EMAIL") == messages[2].replace(
            "nobody@x.test", "EMAIL"
        )

    def test_a_contact_the_importer_can_open_resolves(
        self, user_client, org_a, importer, mine
    ):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["Mine", "New", "High", "mine@x.test"]])},
            format="multipart",
        )

        assert response.status_code == 200, response.content
        case = Case.objects.get(org=org_a, name="Mine")
        assert list(case.contacts.all()) == [mine]

    def test_admin_resolves_any_contact_in_the_org(
        self, admin_client, admin_profile, hidden
    ):
        response = admin_client.post(
            PREVIEW,
            {"file": _csv([["Hidden", "New", "High", "hidden@x.test"]])},
            format="multipart",
        )

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["summary"]["valid"] == 1
        assert body["errors"] == []
