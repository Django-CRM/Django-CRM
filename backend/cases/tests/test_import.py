"""Tests for the cases CSV import endpoints (preview + commit)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from cases.models import Case
from common.models import Tags, Teams
from conftest import rls_org
from contacts.models import Contact


def _csv(headers: list[str], rows: list[list[str]]) -> SimpleUploadedFile:
    """Build an in-memory CSV upload from header + row lists."""
    import csv

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return SimpleUploadedFile(
        "tickets.csv",
        buf.getvalue().encode("utf-8"),
        content_type="text/csv",
    )


@pytest.fixture
def account_a(org_a, admin_user):
    return Account.objects.create(name="Acme Corp", org=org_a, created_by=admin_user)


@pytest.fixture
def contact_a(org_a, admin_user):
    return Contact.objects.create(
        first_name="Pat",
        last_name="Lee",
        email="pat@acme.test",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def team_a(org_a):
    return Teams.objects.create(name="Support", description="", org=org_a)


@pytest.mark.django_db
class TestImportPreview:
    def test_happy_path(self, admin_client, org_a, account_a, contact_a, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "account_name", "contact_emails", "tags"],
            [["Login bug", "New", "High", "Acme Corp", "pat@acme.test", "auth;urgent"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["header_error"] is None
        assert body["summary"] == {"total": 1, "valid": 1, "invalid": 0}
        row = body["valid"][0]
        assert row["name"] == "Login bug"
        assert row["account_id"] == str(account_a.id)
        assert row["contact_ids"] == [str(contact_a.id)]
        assert row["tag_names"] == ["auth", "urgent"]
        # Preview must not create anything
        assert Case.objects.filter(org=org_a).count() == 0

    def test_missing_required_header(self, admin_client, admin_profile):
        csv_file = _csv(["name", "status"], [["X", "New"]])
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200
        body = response.json()
        assert "priority" in (body["header_error"] or "")

    def test_invalid_enum_and_unknown_fk(self, admin_client, admin_profile, org_a):
        csv_file = _csv(
            ["name", "status", "priority", "account_name"],
            [
                ["Row1", "Bogus", "High", ""],
                ["Row2", "New", "AlsoBogus", ""],
                ["Row3", "New", "High", "Nonexistent Co"],
            ],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 3
        fields = {(e["row"], e["field"]) for e in body["errors"]}
        assert (1, "status") in fields
        assert (2, "priority") in fields
        assert (3, "account_name") in fields

    def test_duplicate_within_org_rejected(self, admin_client, case_a, admin_profile):
        # case_a.name == "Bug in login page"
        csv_file = _csv(
            ["name", "status", "priority"],
            [["Bug in login page", "New", "High"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert body["errors"][0]["field"] == "name"

    def test_duplicate_within_file_rejected(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority"],
            [
                ["Same ticket", "New", "High"],
                ["Same ticket", "New", "High"],
            ],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["valid"] == 1
        assert body["summary"]["invalid"] == 1
        assert body["errors"][0]["row"] == 2

    def test_cross_org_account_rejected(
        self, admin_client, admin_profile, org_b, user_b
    ):
        # Account belongs to org_b but admin is in org_a, must not resolve.
        # Seeded into another tenant: the insert-check policy compares
        # against `app.current_org`, so writing this row means being that
        # tenant for the length of the write.
        with rls_org(org_b):
            Account.objects.create(name="Secret Co", org=org_b, created_by=user_b)
        csv_file = _csv(
            ["name", "status", "priority", "account_name"],
            [["X", "New", "High", "Secret Co"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert body["errors"][0]["field"] == "account_name"

    def test_non_admin_without_sales_access_forbidden(self, user_client):
        # default user_profile has no has_sales_access
        csv_file = _csv(["name", "status", "priority"], [["X", "New", "High"]])
        response = user_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 403

    def test_unauthenticated_rejected(self, unauthenticated_client):
        csv_file = _csv(["name", "status", "priority"], [["X", "New", "High"]])
        response = unauthenticated_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        # DRF returns 403 for IsAuthenticated when no auth header is present;
        # 401 would require a WWW-Authenticate challenge. Either signals "blocked".
        assert response.status_code in (401, 403)

    def test_no_file_uploaded(self, admin_client, admin_profile):
        response = admin_client.post(
            "/api/cases/import/preview/", {}, format="multipart"
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestImportCommit:
    def test_commits_valid_rows(
        self, admin_client, org_a, account_a, contact_a, admin_profile
    ):
        csv_file = _csv(
            ["name", "status", "priority", "account_name", "contact_emails", "tags"],
            [["Login bug", "New", "High", "Acme Corp", "pat@acme.test", "auth"]],
        )
        response = admin_client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["created"] == 1
        case = Case.objects.get(id=body["ids"][0])
        assert case.name == "Login bug"
        assert case.account_id == account_a.id
        assert list(case.contacts.values_list("id", flat=True)) == [contact_a.id]
        # Tag auto-created within org
        assert Tags.objects.filter(name="auth", org=org_a).exists()
        assert case.tags.filter(name="auth").exists()

    def test_rolls_back_on_any_error(self, admin_client, org_a, admin_profile):
        # Mix one good row with one invalid row. Neither should be created.
        csv_file = _csv(
            ["name", "status", "priority"],
            [
                ["Good row", "New", "High"],
                ["Bad row", "Bogus", "High"],
            ],
        )
        response = admin_client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        assert Case.objects.filter(org=org_a).count() == 0

    def test_non_admin_without_sales_access_forbidden(self, user_client):
        csv_file = _csv(["name", "status", "priority"], [["X", "New", "High"]])
        response = user_client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 403

    def test_member_with_sales_access_allowed(
        self, regular_user, org_a, user_profile, org_a_with_profile_grant=None
    ):
        # Promote the regular user with sales access, build a fresh client.
        from rest_framework.test import APIClient

        from common.serializer import OrgAwareRefreshToken

        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])

        client = APIClient()
        token = OrgAwareRefreshToken.for_user_and_org(regular_user, org_a, user_profile)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

        csv_file = _csv(["name", "status", "priority"], [["Allowed", "New", "High"]])
        response = client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        assert Case.objects.filter(name="Allowed", org=org_a).count() == 1


@pytest.mark.django_db
class TestImportEdgeCases:
    """Coverage for the less-trodden paths: bad encodings, limits, optional cols."""

    def test_non_utf8_file_reports_header_error(self, admin_client, admin_profile):
        # Latin-1 with a non-ASCII byte that's invalid UTF-8 must be rejected
        # rather than silently mojibake'd into a passing import.
        payload = "name,status,priority\nCaf\xe9 ticket,New,High\n".encode("latin-1")
        upload = SimpleUploadedFile("bad.csv", payload, content_type="text/csv")
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": upload}, format="multipart"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["header_error"] and "UTF-8" in body["header_error"]
        assert body["valid"] == [] and body["errors"] == []

    def test_unknown_header_rejected(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "wat"],
            [["X", "New", "High", "extra"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert "wat" in (body["header_error"] or "")

    def test_too_many_rows_rejected(self, admin_client, admin_profile):
        # MAX_ROWS is 5000; build 5001 rows to trip the guard without writing.
        rows = [[f"Row {i}", "New", "High"] for i in range(5001)]
        csv_file = _csv(["name", "status", "priority"], rows)
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert "5001" in (body["header_error"] or "") or "limit" in (
            body["header_error"] or ""
        )

    def test_malformed_email_reported(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "contact_emails"],
            [["X", "New", "High", "not-an-email"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert body["errors"][0]["field"] == "contact_emails"
        assert "valid email" in body["errors"][0]["message"]

    def test_existing_tag_reused_not_duplicated(
        self, admin_client, org_a, admin_profile
    ):
        # Pre-create a tag with the same slug; commit must reuse it.
        Tags.objects.create(name="urgent", slug="urgent", org=org_a)
        csv_file = _csv(
            ["name", "status", "priority", "tags"],
            [["T1", "New", "High", "urgent"]],
        )
        response = admin_client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        assert Tags.objects.filter(slug="urgent", org=org_a).count() == 1

    def test_teams_resolved_and_attached(
        self, admin_client, org_a, team_a, admin_profile
    ):
        csv_file = _csv(
            ["name", "status", "priority", "team_names"],
            [["With team", "New", "High", "Support"]],
        )
        response = admin_client.post(
            "/api/cases/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        case = Case.objects.get(id=response.json()["ids"][0])
        assert list(case.teams.values_list("id", flat=True)) == [team_a.id]

    def test_unknown_team_reported(self, admin_client, org_a, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "team_names"],
            [["No team", "New", "High", "Ghost"]],
        )
        response = admin_client.post(
            "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert body["errors"][0]["field"] == "team_names"

    def test_validation_uses_constant_query_count(
        self, admin_client, org_a, admin_profile, django_assert_max_num_queries
    ):
        # Two contacts, two assignees, an account, a team: each ref type should
        # cost one bulk SELECT regardless of row count. We don't pin an exact
        # number (auth + RLS + view-level queries vary), but capping at a small
        # constant prevents the N+1 from regressing.
        Account.objects.create(
            name="Acme Corp", org=org_a, created_by=admin_profile.user
        )
        Contact.objects.create(
            first_name="A",
            last_name="B",
            email="a@x.test",
            org=org_a,
            created_by=admin_profile.user,
        )
        Contact.objects.create(
            first_name="C",
            last_name="D",
            email="c@x.test",
            org=org_a,
            created_by=admin_profile.user,
        )
        Teams.objects.create(name="Support", description="", org=org_a)

        rows = [
            [f"Bulk {i}", "New", "High", "Acme Corp", "a@x.test;c@x.test", "Support"]
            for i in range(50)
        ]
        csv_file = _csv(
            [
                "name",
                "status",
                "priority",
                "account_name",
                "contact_emails",
                "team_names",
            ],
            rows,
        )
        # Bulk prefetch should keep this well under 50 queries even at 50 rows.
        with django_assert_max_num_queries(50):
            response = admin_client.post(
                "/api/cases/import/preview/", {"file": csv_file}, format="multipart"
            )
        assert response.status_code == 200, response.json()
        assert response.json()["summary"]["valid"] == 50


@pytest.mark.django_db
class TestMalformedInputIsARowErrorNotA500:
    """Input the parser or the database refuses must come back as a message.

    A field over the csv module's 128 KB limit raised `csv.Error`, and an
    impossible date matched `DATE_RE` and raised at `Case.objects.create`,
    both as 500s after a preview that called the row valid. A tag longer than
    `Tags.name` passes SQLite and raises `DataError` on Postgres.
    """

    PREVIEW = "/api/cases/import/preview/"
    COMMIT = "/api/cases/import/commit/"

    def test_field_over_csv_limit_is_a_header_error(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "description"],
            [["Big", "New", "High", "x" * 200_000]],
        )
        response = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200
        assert "could not be read" in response.json()["header_error"]

    def test_field_over_csv_limit_refused_on_commit(
        self, admin_client, org_a, admin_profile
    ):
        csv_file = _csv(
            ["name", "status", "priority", "description"],
            [["Big", "New", "High", "x" * 200_000]],
        )
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        assert not Case.objects.filter(org=org_a, name="Big").exists()

    def test_impossible_date_is_a_row_error(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "closed_on"],
            [["Leap", "Closed", "High", "2025-02-30"]],
        )
        body = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        ).json()
        assert body["summary"]["valid"] == 0
        assert body["errors"][0]["field"] == "closed_on"

    def test_impossible_date_refused_on_commit(
        self, admin_client, org_a, admin_profile
    ):
        csv_file = _csv(
            ["name", "status", "priority", "closed_on"],
            [["Leap", "Closed", "High", "2025-02-30"]],
        )
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        assert not Case.objects.filter(org=org_a, name="Leap").exists()

    def test_tag_longer_than_the_column(self, admin_client, admin_profile):
        csv_file = _csv(
            ["name", "status", "priority", "tags"], [["X", "New", "High", "t" * 51]]
        )
        body = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        ).json()
        assert body["summary"]["valid"] == 0
        assert body["errors"][0]["field"] == "tags"

    def test_real_date_and_tag_at_the_limit_still_import(
        self, admin_client, org_a, admin_profile
    ):
        """The allowed direction: the new checks must not refuse legal values."""
        csv_file = _csv(
            ["name", "status", "priority", "closed_on", "tags"],
            [["Leap day", "Closed", "High", "2024-02-29", "t" * 50]],
        )
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        case = Case.objects.get(org=org_a, name="Leap day")
        assert str(case.closed_on) == "2024-02-29"
        assert case.tags.get().name == "t" * 50
