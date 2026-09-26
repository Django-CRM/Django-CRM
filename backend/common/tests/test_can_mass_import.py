"""`can_mass_import`: one gate for the contact, ticket and lead CSV importers.

The three importers each carried their own copy of the rule, and none of them
admitted a Django superuser, while every read and write rule next to them
(accounts, contacts, deals, invoices) does.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from common.permissions import can_mass_import

PREVIEWS = [
    "/api/contacts/import/preview/",
    "/api/cases/import/preview/",
    "/api/leads/import/preview/",
]


def _upload():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["first_name", "last_name", "email", "name"])
    writer.writerow(["Ada", "Byte", "ada@example.com", "Printer on fire"])
    return SimpleUploadedFile("rows.csv", buf.getvalue().encode("utf-8"), "text/csv")


@pytest.mark.django_db
class TestPredicate:
    def test_no_profile_is_refused(self):
        assert can_mass_import(None) is False

    def test_plain_member_is_refused(self, user_profile):
        assert can_mass_import(user_profile) is False

    def test_admin_is_admitted(self, admin_profile):
        assert can_mass_import(admin_profile) is True

    def test_superuser_member_is_admitted(self, user_profile, regular_user):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        user_profile.refresh_from_db()
        assert can_mass_import(user_profile) is True

    def test_sales_access_member_is_admitted(self, user_profile):
        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])
        assert can_mass_import(user_profile) is True


@pytest.mark.django_db
class TestEndpoints:
    @pytest.mark.parametrize("url", PREVIEWS)
    def test_plain_member_gets_403(self, user_client, url):
        response = user_client.post(url, {"file": _upload()}, format="multipart")
        assert response.status_code == 403

    @pytest.mark.parametrize("url", PREVIEWS)
    def test_superuser_member_is_let_through(self, user_client, regular_user, url):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        response = user_client.post(url, {"file": _upload()}, format="multipart")
        assert response.status_code == 200, response.content
