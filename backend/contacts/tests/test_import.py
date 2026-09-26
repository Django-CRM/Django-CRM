"""Tests for the contacts CSV import endpoints (preview + commit).

Edge cases the policy specifically targets:
  * email dup vs DB and within-file (DB-enforced unique per org)
  * phone dup vs DB and within-file (normalized to last-10 digits)
  * full-name dup vs DB, but only when the row has no email and no phone
  * permission gate (non-admin user blocked)
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from common.models import Tags, Teams
from contacts.models import Contact


def _csv(headers: list[str], rows: list[list[str]]) -> SimpleUploadedFile:
    """Build an in-memory CSV upload from header + row lists."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return SimpleUploadedFile(
        "contacts.csv",
        buf.getvalue().encode("utf-8"),
        content_type="text/csv",
    )


@pytest.fixture
def existing_contact(org_a, admin_user):
    """A contact already in org_a. Used for vs-DB duplicate tests."""
    return Contact.objects.create(
        first_name="Pat",
        last_name="Lee",
        email="pat@acme.test",
        phone="+1 (202) 555-1234",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def account_a(org_a, admin_user):
    return Account.objects.create(name="Acme Corp", org=org_a, created_by=admin_user)


@pytest.fixture
def team_a(org_a):
    return Teams.objects.create(name="Sales", description="", org=org_a)


@pytest.mark.django_db
class TestImportPreview:
    def test_happy_path(self, admin_client, org_a, account_a, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "email", "phone", "account_name", "tags"],
            [
                [
                    "Alice",
                    "Smith",
                    "alice@example.com",
                    "+1 555 111 2222",
                    "Acme Corp",
                    "vip;newsletter",
                ]
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["header_error"] is None
        assert body["summary"] == {"total": 1, "valid": 1, "invalid": 0}
        row = body["valid"][0]
        assert row["first_name"] == "Alice"
        assert row["last_name"] == "Smith"
        assert row["account_id"] == str(account_a.id)
        assert row["tag_names"] == ["vip", "newsletter"]
        # Preview must not write anything
        assert Contact.objects.filter(org=org_a).count() == 0

    def test_missing_required_header(self, admin_client, admin_profile):
        csv_file = _csv(["first_name"], [["Alice"]])
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200
        body = response.json()
        assert "last_name" in (body["header_error"] or "")

    def test_unknown_header_rejected(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "totally_made_up"],
            [["Alice", "Smith", "x"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["header_error"] and "totally_made_up" in body["header_error"]

    def test_email_duplicate_vs_db(self, admin_client, existing_contact, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [["Different", "Person", "pat@acme.test"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        fields = {(e["row"], e["field"]) for e in body["errors"]}
        assert (1, "email") in fields

    def test_email_duplicate_case_insensitive(
        self, admin_client, existing_contact, admin_profile
    ):
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [["Different", "Person", "PAT@ACME.TEST"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1

    def test_email_duplicate_within_file(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [
                ["A", "One", "dup@example.com"],
                ["B", "Two", "dup@example.com"],
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["valid"] == 1
        assert body["summary"]["invalid"] == 1
        # The second row is the one that's flagged
        err = body["errors"][0]
        assert err["row"] == 2
        assert err["field"] == "email"
        assert "row 1" in err["message"]

    def test_phone_duplicate_vs_db_normalized(
        self, admin_client, existing_contact, admin_profile
    ):
        # existing_contact.phone == "+1 (202) 555-1234"; this is the same
        # number written differently, normalizer strips to "2025551234".
        csv_file = _csv(
            ["first_name", "last_name", "phone"],
            [["Different", "Person", "202-555-1234"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        fields = {(e["row"], e["field"]) for e in body["errors"]}
        assert (1, "phone") in fields

    def test_phone_duplicate_within_file(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "phone"],
            [
                ["A", "One", "+1 555 000 1111"],
                ["B", "Two", "5550001111"],
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["valid"] == 1
        assert body["summary"]["invalid"] == 1
        err = body["errors"][0]
        assert err["row"] == 2 and err["field"] == "phone"

    def test_full_name_dup_blocked_without_disambiguator(
        self, admin_client, existing_contact, admin_profile
    ):
        # existing_contact is "Pat Lee" with pat@acme.test + a phone. Row has
        # the same name but NO email and NO phone → can't disambiguate.
        csv_file = _csv(
            ["first_name", "last_name"],
            [["Pat", "Lee"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        fields = {(e["row"], e["field"]) for e in body["errors"]}
        assert (1, "first_name") in fields

    def test_full_name_dup_allowed_with_different_email(
        self, admin_client, existing_contact, admin_profile
    ):
        # Same name, different email → allowed; could be a different person.
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [["Pat", "Lee", "other-pat@acme.test"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"] == {"total": 1, "valid": 1, "invalid": 0}

    def test_full_name_dup_within_file_blocked(self, admin_client, admin_profile):
        # Two name-only rows with no email/phone. Second row must be flagged.
        csv_file = _csv(
            ["first_name", "last_name"],
            [
                ["Same", "Name"],
                ["Same", "Name"],
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["valid"] == 1
        assert body["summary"]["invalid"] == 1
        err = body["errors"][0]
        assert err["row"] == 2 and err["field"] == "first_name"
        assert "row 1" in err["message"]

    def test_linkedin_url_with_only_scheme_rejected(self, admin_client, admin_profile):
        # "https://" passes the old regex but is not a valid URL, Django's
        # URLValidator catches it.
        csv_file = _csv(
            ["first_name", "last_name", "linkedin_url"],
            [["A", "B", "https://"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "linkedin_url" for e in body["errors"])

    def test_invalid_email_format(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [["A", "B", "not-an-email"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "email" for e in body["errors"])

    def test_invalid_phone_format(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "phone"],
            [["A", "B", "abc"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "phone" for e in body["errors"])

    def test_invalid_country_code(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "country"],
            [["A", "B", "ZZ"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "country" for e in body["errors"])

    def test_invalid_linkedin_url(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "linkedin_url"],
            [["A", "B", "linkedin.com/in/a"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "linkedin_url" for e in body["errors"])

    def test_unknown_account_ref(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "account_name"],
            [["A", "B", "Nonexistent Co"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert body["summary"]["invalid"] == 1
        assert any(e["field"] == "account_name" for e in body["errors"])

    def test_unknown_assigned_email_ref(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "assigned_emails"],
            [["A", "B", "ghost@nowhere.test"]],
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        body = response.json()
        assert any(e["field"] == "assigned_emails" for e in body["errors"])

    def test_permission_denied_for_regular_user(self, user_client, user_profile):
        csv_file = _csv(
            ["first_name", "last_name"],
            [["A", "B"]],
        )
        response = user_client.post(
            "/api/contacts/import/preview/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 403

    def test_non_csv_extension_rejected(self, admin_client, admin_profile):
        upload = SimpleUploadedFile(
            "contacts.xlsx",
            b"first_name,last_name\nA,B\n",
            content_type="text/csv",
        )
        response = admin_client.post(
            "/api/contacts/import/preview/", {"file": upload}, format="multipart"
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestImportCommit:
    def test_commits_in_one_transaction(
        self, admin_client, org_a, account_a, team_a, admin_profile
    ):
        csv_file = _csv(
            [
                "first_name",
                "last_name",
                "email",
                "phone",
                "account_name",
                "team_names",
                "tags",
            ],
            [
                [
                    "Alice",
                    "Smith",
                    "alice@example.com",
                    "+1 555 111 2222",
                    "Acme Corp",
                    "Sales",
                    "vip",
                ],
                [
                    "Bob",
                    "Jones",
                    "bob@example.com",
                    "+1 555 333 4444",
                    "Acme Corp",
                    "Sales",
                    "vip;newsletter",
                ],
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["error"] is False
        assert body["created"] == 2
        contacts = Contact.objects.filter(org=org_a)
        assert contacts.count() == 2
        alice = contacts.get(email="alice@example.com")
        assert alice.account_id == account_a.id
        assert list(alice.teams.values_list("name", flat=True)) == ["Sales"]
        assert set(alice.tags.values_list("name", flat=True)) == {"vip"}
        # Tag was reused across both rows
        assert Tags.objects.filter(name="vip", org=org_a).count() == 1

    def test_imported_contact_appears_on_its_account(
        self, admin_client, org_a, account_a, admin_profile
    ):
        """Setting the FK is not enough: the account page reads the M2M.

        A Contact joins an Account two ways and only `Account.contacts` drives
        the account's people list. The importer set `account_id` and skipped
        `link_primary_account`, which the three interactive create paths all
        call, so an imported contact was invisible on the account it named.
        `test_commits_in_one_transaction` asserts the FK and passes either way,
        which is why this needs its own assertion on the membership.
        """
        csv_file = _csv(
            ["first_name", "last_name", "email", "account_name"],
            [["Carol", "Klein", "carol@example.com", "Acme Corp"]],
        )
        response = admin_client.post(
            "/api/contacts/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        carol = Contact.objects.get(email="carol@example.com")
        assert carol.account_id == account_a.id
        assert carol in account_a.contacts.all()

    def test_commit_refuses_if_any_row_invalid(
        self, admin_client, org_a, existing_contact, admin_profile
    ):
        # Row 1 is valid; row 2 collides on email with the existing contact.
        # The whole batch must be rejected, atomic = all-or-nothing.
        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [
                ["New", "Person", "newp@example.com"],
                ["Other", "Pat", "pat@acme.test"],
            ],
        )
        response = admin_client.post(
            "/api/contacts/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] is True
        assert body["created"] == 0
        # Only the original contact exists
        assert Contact.objects.filter(org=org_a).count() == 1

    def test_permission_denied_for_regular_user(self, user_client, user_profile):
        csv_file = _csv(
            ["first_name", "last_name"],
            [["A", "B"]],
        )
        response = user_client.post(
            "/api/contacts/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 403

    def test_integrity_error_race_returns_400_not_500(
        self, admin_client, org_a, admin_user, admin_profile, monkeypatch
    ):
        # Simulate the case where another request inserts a colliding contact
        # AFTER parse_and_validate has read the DB but BEFORE the create runs.
        # The DB then raises IntegrityError on the unique_contact_email_per_org
        # constraint. This must surface as a 400 with a friendly message, not
        # a 500 from Django's exception middleware.
        from django.db import IntegrityError

        from contacts.services import csv_import

        def racing_create(**kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(csv_import.Contact.objects, "create", racing_create)

        csv_file = _csv(
            ["first_name", "last_name", "email"],
            [["Race", "Condition", "race@example.com"]],
        )
        response = admin_client.post(
            "/api/contacts/import/commit/", {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] is True
        assert body["created"] == 0
        assert "concurrently" in body["message"].lower()


@pytest.mark.django_db
class TestMalformedInputIsARowErrorNotA500:
    """Input the parser or the database refuses must come back as a message.

    Each of these reached the DB (or the csv module) unchecked. A field over
    the csv module's 128 KB limit raised `csv.Error`; the other three pass
    SQLite, which ignores column widths, and raise `DataError` on Postgres,
    where production runs. All of them read as "valid" in the preview first.
    """

    PREVIEW = "/api/contacts/import/preview/"
    COMMIT = "/api/contacts/import/commit/"

    def test_field_over_csv_limit_is_a_header_error(self, admin_client, admin_profile):
        csv_file = _csv(
            ["first_name", "last_name", "description"],
            [["Big", "Field", "x" * 200_000]],
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
            ["first_name", "last_name", "description"],
            [["Big", "Field", "x" * 200_000]],
        )
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        assert response.json()["created"] == 0
        assert not Contact.objects.filter(org=org_a, first_name="Big").exists()

    def test_email_longer_than_the_column(self, admin_client, admin_profile):
        email = "a" * 250 + "@example.com"
        csv_file = _csv(["first_name", "last_name", "email"], [["A", "B", email]])
        body = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        ).json()
        assert body["summary"]["valid"] == 0
        assert body["errors"][0]["field"] == "email"

    def test_linkedin_url_longer_than_the_column(self, admin_client, admin_profile):
        url = "https://linkedin.com/in/" + "a" * 200
        csv_file = _csv(["first_name", "last_name", "linkedin_url"], [["A", "B", url]])
        body = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        ).json()
        assert body["summary"]["valid"] == 0
        assert body["errors"][0]["field"] == "linkedin_url"

    def test_tag_longer_than_the_column(self, admin_client, admin_profile):
        csv_file = _csv(["first_name", "last_name", "tags"], [["A", "B", "t" * 51]])
        body = admin_client.post(
            self.PREVIEW, {"file": csv_file}, format="multipart"
        ).json()
        assert body["summary"]["valid"] == 0
        assert body["errors"][0]["field"] == "tags"

    def test_values_at_the_limit_still_import(self, admin_client, org_a, admin_profile):
        """The allowed direction: the new bounds must not refuse legal values."""
        email = "a" * 242 + "@example.com"  # 254, the EmailField default
        url = "https://x.io/" + "a" * 187  # 200, the URLField default
        csv_file = _csv(
            ["first_name", "last_name", "email", "linkedin_url", "tags"],
            [["Edge", "Case", email, url, "t" * 50]],
        )
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 200, response.json()
        contact = Contact.objects.get(org=org_a, first_name="Edge")
        assert contact.email == email and contact.linkedin_url == url
        assert contact.tags.get().name == "t" * 50

    def test_integrity_error_does_not_echo_the_database_message(
        self, admin_client, admin_profile, monkeypatch
    ):
        """A Postgres unique violation names the constraint and the key values."""
        from django.db import IntegrityError

        from contacts.services import csv_import

        def racing_create(**kwargs):
            raise IntegrityError('violates unique constraint "unique_contact_email"')

        monkeypatch.setattr(csv_import.Contact.objects, "create", racing_create)
        csv_file = _csv(["first_name", "last_name"], [["Race", "Two"]])
        response = admin_client.post(
            self.COMMIT, {"file": csv_file}, format="multipart"
        )
        assert response.status_code == 400
        assert "unique_contact_email" not in response.content.decode()
