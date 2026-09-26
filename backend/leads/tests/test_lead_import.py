"""Lead CSV import: `import/preview/` and `import/commit/`.

The web Leads page had an Import button wired to nothing, and the only lead
importer was a single-step uploader nobody called. These endpoints share the
contacts and cases contract, validate each row with `LeadCreateSerializer`,
and resolve every name in the file inside the caller's org only.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from common.models import CustomFieldDefinition, Tags, Teams
from leads import csv_import
from leads.models import Lead

PREVIEW = "/api/leads/import/preview/"
COMMIT = "/api/leads/import/commit/"
BASE = ["first_name", "last_name", "email"]


def _csv(headers, rows, name="leads.csv"):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return SimpleUploadedFile(name, buf.getvalue().encode("utf-8"), "text/csv")


def _raw(data: bytes, name="leads.csv"):
    return SimpleUploadedFile(name, data, "text/csv")


def _post(client, url, upload, **extra):
    return client.post(url, {"file": upload, **extra}, format="multipart")


def _one_lead():
    return _csv(BASE, [["Ada", "Byte", "ada@example.com"]])


@pytest.mark.django_db
class TestPermission:
    """`_can_import` must be able to answer both ways, on both endpoints."""

    @pytest.mark.parametrize("url", [PREVIEW, COMMIT])
    def test_a_plain_member_is_refused(self, user_client, url):
        response = _post(user_client, url, _one_lead())
        assert response.status_code == 403
        assert not Lead.objects.exists()

    def test_an_admin_can_commit(self, admin_client, org_a):
        response = _post(admin_client, COMMIT, _one_lead())
        assert response.status_code == 200, response.json()
        assert Lead.objects.filter(org=org_a, email="ada@example.com").count() == 1

    def test_sales_access_can_commit(self, user_client, user_profile, org_a):
        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])
        response = _post(user_client, COMMIT, _one_lead())
        assert response.status_code == 200, response.json()
        assert Lead.objects.filter(org=org_a).count() == 1


@pytest.mark.django_db
class TestPreview:
    def test_reports_valid_rows_and_writes_nothing(self, admin_client):
        upload = _csv(
            BASE + ["title", "status", "company_name"],
            [["Ada", "Byte", "ada@example.com", "Big deal", "In Process", "Acme"]],
        )
        response = _post(admin_client, PREVIEW, upload)
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["header_error"] is None
        assert body["summary"] == {"total": 1, "valid": 1, "invalid": 0}
        row = body["valid"][0]
        assert row["row"] == 1
        # Matched on the label, stored as the value.
        assert row["status"] == "in process"
        assert row["company_name"] == "Acme"
        assert not Lead.objects.exists()

    def test_row_errors_come_from_the_create_serializer(self, admin_client):
        upload = _csv(
            BASE + ["status", "probability", "country"],
            [
                ["Ada", "Byte", "not-an-email", "", "", ""],
                ["Bea", "", "", "sideways", "", ""],
                ["Cy", "", "", "", "-5", ""],
                ["Di", "", "", "", "", "Atlantis"],
                ["", "", "", "assigned", "", ""],
            ],
        )
        body = _post(admin_client, PREVIEW, upload).json()
        assert body["summary"] == {"total": 5, "valid": 0, "invalid": 5}
        fields = {(e["row"], e["field"]) for e in body["errors"]}
        assert fields == {
            (1, "email"),
            (2, "status"),
            (3, "probability"),
            (4, "country"),
            (5, "first_name"),
        }

    def test_a_converted_lead_cannot_be_imported(self, admin_client):
        upload = _csv(
            BASE + ["status"], [["Ada", "Byte", "a@example.com", "Converted"]]
        )
        body = _post(admin_client, PREVIEW, upload).json()
        assert [(e["row"], e["field"]) for e in body["errors"]] == [(1, "status")]

    def test_duplicate_email_in_the_file_is_a_row_error(self, admin_client):
        upload = _csv(
            BASE,
            [["Ada", "Byte", "ada@example.com"], ["Ada", "Two", "ADA@example.com"]],
        )
        body = _post(admin_client, PREVIEW, upload).json()
        assert body["summary"]["valid"] == 1
        assert body["errors"][0]["row"] == 2
        assert body["errors"][0]["field"] == "email"

    def test_email_already_used_in_this_org_is_a_row_error(self, admin_client, org_a):
        Lead.objects.create(first_name="Old", email="ada@example.com", org=org_a)
        body = _post(admin_client, PREVIEW, _one_lead()).json()
        assert [(e["row"], e["field"]) for e in body["errors"]] == [(1, "email")]

    def test_email_used_in_another_org_does_not_block(self, admin_client, org_b):
        Lead.objects.create(first_name="Theirs", email="ada@example.com", org=org_b)
        body = _post(admin_client, PREVIEW, _one_lead()).json()
        assert body["errors"] == []
        assert body["summary"]["valid"] == 1


@pytest.mark.django_db
class TestOldHeaderNames:
    """The 1.9.2 column names map to fields on every import endpoint."""

    def test_no_alias_is_a_field_name(self):
        assert not set(csv_import.HEADER_ALIASES) & set(csv_import.KNOWN_HEADERS)

    def test_preview_reads_the_old_names(self, admin_client):
        upload = _csv(["First Name", "last name", "address"], [["Ada", "B", "1 Main"]])
        body = _post(admin_client, PREVIEW, upload).json()
        assert body["header_error"] is None
        assert body["valid"][0]["first_name"] == "Ada"

    @pytest.mark.parametrize("url", [PREVIEW, COMMIT, "/api/leads/upload/"])
    def test_an_alias_and_its_field_together_are_refused(self, admin_client, url):
        upload = _csv(["first name", "first_name", "last_name"], [["A", "B", "C"]])
        field = "leads_file" if url.endswith("upload/") else "file"
        response = admin_client.post(url, {field: upload}, format="multipart")
        body = response.json()
        message = body.get("header_error") or body.get("errors")
        assert "more than once: first_name" in message
        assert not Lead.objects.exists()


@pytest.mark.django_db
class TestCommit:
    def test_creates_leads_in_the_callers_org_with_references(
        self, admin_client, org_a, admin_user, admin_profile, user_profile
    ):
        team = Teams.objects.create(name="Field Sales", org=org_a)
        upload = _csv(
            BASE
            + ["opportunity_amount", "rating", "assigned_emails", "team_names", "tags"],
            [
                [
                    "Ada",
                    "Byte",
                    "ada@example.com",
                    "1200.50",
                    "hot",
                    "admin@test.com;USER@test.com",
                    "field sales",
                    "vip;Trade Show",
                ],
                ["", "Solo", "", "", "", "", "", ""],
            ],
        )
        response = _post(admin_client, COMMIT, upload)
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["created"] == 2
        assert len(body["ids"]) == 2

        lead = Lead.objects.get(email="ada@example.com")
        assert lead.org_id == org_a.id
        assert lead.created_by_id == admin_user.id
        assert lead.rating == "HOT"
        assert str(lead.opportunity_amount) == "1200.50"
        assert set(lead.assigned_to.all()) == {admin_profile, user_profile}
        assert list(lead.teams.all()) == [team]
        assert {t.name for t in lead.tags.all()} == {"vip", "Trade Show"}
        assert all(t.org_id == org_a.id for t in lead.tags.all())
        assert Lead.objects.get(last_name="Solo").org_id == org_a.id

    def test_org_in_the_request_body_is_ignored(self, admin_client, org_a, org_b):
        response = _post(admin_client, COMMIT, _one_lead(), org=str(org_b.id))
        assert response.status_code == 200, response.json()
        assert Lead.objects.get(email="ada@example.com").org_id == org_a.id

    def test_org_or_created_by_columns_are_refused(self, admin_client):
        for column in ("org", "created_by"):
            upload = _csv(BASE + [column], [["Ada", "Byte", "a@example.com", "x"]])
            response = _post(admin_client, COMMIT, upload)
            assert response.status_code == 400
            assert column in response.json()["header_error"]
        assert not Lead.objects.exists()

    def test_one_bad_row_creates_nothing(self, admin_client):
        upload = _csv(
            BASE, [["Ada", "Byte", "ada@example.com"], ["Bea", "Byte", "nope"]]
        )
        response = _post(admin_client, COMMIT, upload)
        assert response.status_code == 400
        body = response.json()
        assert body["created"] == 0
        assert body["errors"][0]["row"] == 2
        assert not Lead.objects.exists()


@pytest.mark.django_db
class TestReferencesStayInTheOrg:
    """A name that matches another org's record must not link to it."""

    def test_another_orgs_user_is_not_assigned(self, admin_client, profile_b):
        upload = _csv(
            BASE + ["assigned_emails"],
            [["Ada", "Byte", "a@example.com", "userb@test.com"]],
        )
        response = _post(admin_client, COMMIT, upload)
        assert response.status_code == 400
        assert response.json()["errors"][0]["field"] == "assigned_emails"
        assert not Lead.objects.exists()

    def test_another_orgs_team_is_not_linked(self, admin_client, org_b):
        Teams.objects.create(name="Theirs", org=org_b)
        upload = _csv(
            BASE + ["team_names"], [["Ada", "Byte", "a@example.com", "Theirs"]]
        )
        response = _post(admin_client, COMMIT, upload)
        assert response.status_code == 400
        assert response.json()["errors"][0]["field"] == "team_names"
        assert not Lead.objects.exists()

    def test_another_orgs_tag_is_not_reused(self, admin_client, org_a, org_b):
        theirs = Tags.objects.create(name="vip", slug="vip", org=org_b)
        upload = _csv(BASE + ["tags"], [["Ada", "Byte", "a@example.com", "vip"]])
        response = _post(admin_client, COMMIT, upload)
        assert response.status_code == 200, response.json()
        tag = Lead.objects.get().tags.get()
        assert tag.org_id == org_a.id
        assert tag.id != theirs.id


@pytest.mark.django_db
class TestBadFiles:
    @pytest.mark.parametrize(
        "upload, fragment",
        [
            (_raw(b""), "empty"),
            (_raw("first_name,last_name\nZo\xeb,X\n".encode("latin-1")), "UTF-8"),
            (_raw(b"first_name,email\nAda,a@example.com\n"), "last_name"),
            (_raw(b"first_name,last_name,shoe_size\nAda,B,9\n"), "shoe_size"),
            (_raw(b"first_name,last_name,tags\nAda,B,v\x00ip\n"), "could not be read"),
            (_raw(b"first_name,last_name\n" + b"A" * 200_000 + b",B\n"), "could not"),
        ],
        ids=[
            "empty",
            "not-utf8",
            "missing-header",
            "unknown-header",
            "nul-byte",
            "field-over-csv-limit",
        ],
    )
    def test_whole_file_problems_are_a_header_error(
        self, admin_client, upload, fragment
    ):
        preview = _post(admin_client, PREVIEW, upload)
        assert preview.status_code == 200
        assert fragment in preview.json()["header_error"]
        upload.seek(0)
        commit = _post(admin_client, COMMIT, upload)
        assert commit.status_code == 400
        assert fragment in commit.json()["header_error"]
        assert not Lead.objects.exists()

    def test_too_many_rows(self, admin_client, monkeypatch):
        monkeypatch.setattr(csv_import, "MAX_ROWS", 2)
        upload = _csv(BASE, [["A", "B", ""]] * 3)
        body = _post(admin_client, PREVIEW, upload).json()
        assert "Too many rows" in body["header_error"]

    def test_oversized_file_is_refused_on_bytes_read(self, admin_client):
        big = b"first_name,last_name\n" + b"Ada,Byte\n" * 700_000
        assert len(big) > 5 * 1024 * 1024
        response = _post(admin_client, COMMIT, _raw(big))
        assert response.status_code == 400
        assert "5 MB" in response.json()["message"]

    def test_non_csv_extension_is_refused(self, admin_client):
        response = _post(admin_client, PREVIEW, _raw(b"first_name\n", name="x.txt"))
        assert response.status_code == 400

    def test_missing_file_is_refused(self, admin_client):
        response = admin_client.post(PREVIEW, {}, format="multipart")
        assert response.status_code == 400

    def test_required_custom_field_blocks_the_import(self, admin_client, org_a):
        """The create API refuses a lead without it; the importer cannot set it."""
        CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Lead",
            key="region",
            label="Region",
            field_type="text",
            is_required=True,
            is_active=True,
        )
        response = _post(admin_client, COMMIT, _one_lead())
        assert response.status_code == 400
        assert "region" in response.json()["header_error"]
        assert not Lead.objects.exists()


@pytest.mark.django_db
class TestCreateApiRejectsNegatives:
    """The floors added for the importer also turn a create-API 500 into a 400."""

    @pytest.mark.parametrize(
        "field, value", [("probability", -1), ("opportunity_amount", "-10")]
    )
    def test_negative_value_is_a_400(self, admin_client, field, value):
        response = admin_client.post(
            "/api/leads/", {"first_name": "Ada", field: value}, format="json"
        )
        assert response.status_code == 400
        assert field in response.json()["errors"]

    def test_zero_is_still_accepted(self, admin_client):
        response = admin_client.post(
            "/api/leads/",
            {"first_name": "Ada", "probability": 0, "opportunity_amount": "0"},
            format="json",
        )
        assert response.status_code == 200, response.json()


@pytest.mark.django_db
class TestDeprecatedUploadAlias:
    """`upload/` keeps its URL, field name and shapes, but runs the new commit."""

    URL = "/api/leads/upload/"

    def _post(self, client, upload):
        return client.post(self.URL, {"leads_file": upload}, format="multipart")

    def test_a_permitted_role_creates_rows_in_its_own_org(
        self, user_client, user_profile, org_a
    ):
        user_profile.has_sales_access = True
        user_profile.save(update_fields=["has_sales_access"])
        response = self._post(user_client, _one_lead())
        assert response.status_code == 200, response.json()
        assert response.json() == {
            "error": False,
            "message": "Leads created Successfully",
        }
        lead = Lead.objects.get(email="ada@example.com")
        assert lead.org_id == org_a.id

    def test_a_plain_member_is_refused(self, user_client):
        response = self._post(user_client, _one_lead())
        assert response.status_code == 403
        assert response.json() == {"error": True, "errors": "Admin access required"}
        assert not Lead.objects.exists()

    def test_a_bad_row_is_a_400_that_creates_nothing(self, admin_client):
        upload = _csv(BASE, [["Ada", "Byte", "ada@example.com"], ["Bea", "B", "no"]])
        response = self._post(admin_client, upload)
        assert response.status_code == 400
        body = response.json()
        assert body["error"] is True
        assert body["errors"][0]["row"] == 2
        assert not Lead.objects.exists()

    def test_a_bad_file_is_a_400(self, admin_client):
        response = self._post(admin_client, _raw(b""))
        assert response.status_code == 400
        assert "empty" in response.json()["errors"]

    OLD_HEADERS = [
        "title",
        "first name",
        "last name",
        "website",
        "email",
        "phone",
        "address",
        "city",
        "state",
        "postcode",
        "country",
        "description",
        "status",
        "account_name",
    ]

    def test_the_1_9_2_header_set_imports_every_column(self, admin_client, org_a):
        row = [
            "Big deal",
            "Ada",
            "Byte",
            "https://acme.example",
            "ada@example.com",
            "+14155550100",
            "1 Main St",
            "Springfield",
            "IL",
            "62701",
            "US",
            "Met at the expo",
            "in process",
            "Acme",
        ]
        response = self._post(admin_client, _csv(self.OLD_HEADERS, [row]))
        assert response.status_code == 200, response.json()
        lead = Lead.objects.get(org=org_a)
        assert [
            lead.title,
            lead.first_name,
            lead.last_name,
            lead.website,
            lead.email,
            lead.phone,
            lead.address_line,
            lead.city,
            lead.state,
            lead.postcode,
            lead.country,
            lead.description,
            lead.status,
        ] == row[:-1]

    def test_headers_match_in_any_case_and_padding(self, admin_client, org_a):
        upload = _csv(
            [" Title ", "FIRST NAME", "Last Name", "Email", " ADDRESS"],
            [["Big deal", "Ada", "Byte", "ada@example.com", "1 Main St"]],
        )
        response = self._post(admin_client, upload)
        assert response.status_code == 200, response.json()
        lead = Lead.objects.get(org=org_a)
        assert (lead.title, lead.first_name, lead.last_name, lead.address_line) == (
            "Big deal",
            "Ada",
            "Byte",
            "1 Main St",
        )

    def test_account_name_is_accepted_and_links_nothing(
        self, admin_client, org_a, org_b
    ):
        """Lead has no account FK. 1.9.2 assigned the match to an attribute
        that was never saved, so the column never stored anything; it is
        still accepted, and a same-named account in either org is untouched."""
        from accounts.models import Account

        Account.objects.create(name="Acme", org=org_a)
        Account.objects.create(name="Acme", org=org_b)
        upload = _csv(["title", "email", "account_name"], [["Deal", "a@x.com", "acme"]])
        response = self._post(admin_client, upload)
        assert response.status_code == 200, response.json()
        lead = Lead.objects.get(org=org_a)
        assert lead.company_name is None
        assert not lead.contacts.exists()
        assert Account.objects.filter(name="Acme").count() == 2

    def test_unknown_columns_are_ignored_and_cannot_set_the_org(
        self, admin_client, org_a, org_b
    ):
        upload = _csv(
            ["title", "org", "created_by", "shoe_size"],
            [["Deal", str(org_b.id), "someone@example.com", "9"]],
        )
        response = self._post(admin_client, upload)
        assert response.status_code == 200, response.json()
        assert Lead.objects.get().org_id == org_a.id

    def test_a_title_only_row_imports(self, admin_client, org_a):
        response = self._post(admin_client, _raw(b"title\nAcme deal\n"))
        assert response.status_code == 200, response.json()
        assert Lead.objects.get(org=org_a).title == "Acme deal"

    def test_a_row_with_no_name_or_title_is_a_400_that_creates_nothing(
        self, admin_client
    ):
        upload = _csv(["title", "email"], [["Deal", "a@x.com"], ["", "b@x.com"]])
        response = self._post(admin_client, upload)
        assert response.status_code == 400
        (error,) = response.json()["errors"]
        assert error["row"] == 2
        assert "first name, last name or title" in error["message"]
        assert not Lead.objects.exists()

    def test_any_file_name_is_accepted(self, admin_client, org_a):
        response = self._post(admin_client, _raw(b"title\nDeal\n", name="leads.txt"))
        assert response.status_code == 200, response.json()
        assert Lead.objects.filter(org=org_a).count() == 1

    def test_a_non_csv_body_under_any_name_is_still_refused(self, admin_client):
        response = self._post(admin_client, _raw(b"\x00\x01\x02", name="leads.bin"))
        assert response.status_code == 400
        assert not Lead.objects.exists()

    def test_an_iso_8859_1_file_imports_as_1_9_2_read_it(self, admin_client, org_a):
        upload = _raw("title,first name\nCaf\xe9 deal,Jos\xe9\n".encode("latin-1"))
        response = self._post(admin_client, upload)
        assert response.status_code == 200, response.json()
        lead = Lead.objects.get(org=org_a)
        assert (lead.title, lead.first_name) == ("Caf\xe9 deal", "Jos\xe9")

    def test_the_old_field_name_is_required(self, admin_client):
        response = admin_client.post(
            self.URL, {"file": _one_lead()}, format="multipart"
        )
        assert response.status_code == 400
        assert "leads_file" in response.json()["errors"]

    def test_an_oversized_file_is_a_400(self, admin_client):
        big = b"first_name,last_name\n" + b"Ada,Byte\n" * 700_000
        response = self._post(admin_client, _raw(big))
        assert response.status_code == 400
        assert "5 MB" in response.json()["errors"]
        assert not Lead.objects.exists()


@pytest.mark.django_db
class TestTagSlugs:
    """A tag is stored under `Tags.slug_for(name)`, unique per org and 50
    characters at most, so the importer checks `Tags.name_error` before calling
    a row valid. The old ASCII slug was "" for every non-Latin name, so those
    were refused; the Unicode slug gives each its own key. A name with no
    letter or digit, or a ligature name whose slug expands past 50, is still
    refused."""

    def _tagged(self, email, tag):
        return _csv(BASE + ["tags"], [["Ada", "Byte", email, tag]])

    def test_non_latin_tags_import_and_are_reused(self, admin_client, org_a):
        first = _post(
            admin_client, COMMIT, self._tagged("one@example.com", "日本;中国")
        )
        second = _post(admin_client, COMMIT, self._tagged("two@example.com", "日本"))
        assert first.status_code == 200, first.json()
        assert second.status_code == 200, second.json()
        tags = Tags.objects.filter(org=org_a)
        assert sorted(tags.values_list("slug", flat=True)) == sorted(["日本", "中国"])
        nihon = tags.get(slug="日本")
        assert Lead.objects.filter(org=org_a, tags=nihon).count() == 2

    def test_tag_without_a_letter_or_digit_is_a_row_error(self, admin_client, org_a):
        response = _post(admin_client, COMMIT, self._tagged("one@example.com", "🎉"))
        assert response.status_code == 400, response.json()
        body = response.json()
        assert body["created"] == 0
        assert [(e["row"], e["field"]) for e in body["errors"]] == [(1, "tags")]
        assert "needs at least one letter or digit" in body["errors"][0]["message"]
        assert not Lead.objects.exists()
        assert not Tags.objects.filter(org=org_a).exists()

    def test_tag_whose_slug_is_too_long_is_a_row_error(self, admin_client):
        body = _post(
            admin_client, PREVIEW, self._tagged("a@example.com", "ﬀ" * 50)
        ).json()
        assert body["summary"] == {"total": 1, "valid": 0, "invalid": 1}
        assert body["errors"][0]["field"] == "tags"
        assert "too long" in body["errors"][0]["message"]

    def test_second_import_reuses_the_tag_by_its_slug(self, admin_client, org_a):
        first = _post(admin_client, COMMIT, self._tagged("a@example.com", "Trade Show"))
        second = _post(
            admin_client, COMMIT, self._tagged("b@example.com", "trade show")
        )
        assert first.status_code == 200, first.json()
        assert second.status_code == 200, second.json()
        tag = Tags.objects.get(org=org_a)
        assert tag.slug == "trade-show"
        assert set(Lead.objects.values_list("tags", flat=True)) == {tag.id}
