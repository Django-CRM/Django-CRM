"""The contact import judges phone and name clashes among visible contacts (D48).

`can_mass_import` admits a member with sales access. The preview checked a
row's phone and full name against every contact in the org, so such a member
got "A contact with this phone number already exists" for a number held only
by a contact they cannot open, which confirmed that contact existed. Neither
field has a DB constraint (the only one on ``Contact`` is the per-org email
index), so both checks now look only at ``visible_contacts_qs``. Email stays
org-wide, because the database refuses the duplicate either way.
"""

import csv
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from contacts.models import Contact

PREVIEW = "/api/contacts/import/preview/"
PHONE_CLASH = "A contact with this phone number already exists in your organization"
NAME_CLASH = (
    "A contact with this name already exists; add an email or phone to confirm "
    "it's a different person"
)


def _csv(headers, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return SimpleUploadedFile(
        "contacts.csv", buf.getvalue().encode("utf-8"), content_type="text/csv"
    )


@pytest.fixture
def importer(user_profile):
    user_profile.has_sales_access = True
    user_profile.save(update_fields=["has_sales_access"])
    return user_profile


def _contact(org, creator, first, last, phone, email):
    return Contact.objects.create(
        first_name=first,
        last_name=last,
        phone=phone,
        email=email,
        org=org,
        created_by=creator,
    )


@pytest.fixture
def hidden(org_a, admin_user):
    return _contact(
        org_a, admin_user, "Hidden", "Person", "+1 (202) 555-0101", "hid@x.test"
    )


@pytest.fixture
def mine(org_a, regular_user):
    return _contact(
        org_a, regular_user, "Mine", "Visible", "+1 (202) 555-0202", "mine@x.test"
    )


def _preview(client, headers, rows):
    response = client.post(PREVIEW, {"file": _csv(headers, rows)}, format="multipart")
    assert response.status_code == 200, response.content
    body = response.json()
    return body, [(e["field"], e["message"]) for e in body["errors"]]


@pytest.mark.django_db
class TestPhoneClash:
    HEADERS = ["first_name", "last_name", "phone"]

    def test_hidden_contact_phone_is_not_reported(self, user_client, importer, hidden):
        body, errors = _preview(
            user_client, self.HEADERS, [["New", "Person", "202-555-0101"]]
        )
        assert errors == []
        assert body["summary"]["valid"] == 1

    def test_visible_contact_phone_is_reported(self, user_client, importer, mine):
        body, errors = _preview(
            user_client, self.HEADERS, [["New", "Person", "202-555-0202"]]
        )
        assert errors == [("phone", PHONE_CLASH)]
        assert body["summary"]["valid"] == 0

    def test_admin_sees_every_phone_clash(self, admin_client, admin_profile, hidden):
        _body, errors = _preview(
            admin_client, self.HEADERS, [["New", "Person", "202-555-0101"]]
        )
        assert errors == [("phone", PHONE_CLASH)]


@pytest.mark.django_db
class TestFullNameClash:
    HEADERS = ["first_name", "last_name"]

    def test_hidden_contact_name_is_not_reported(self, user_client, importer, hidden):
        body, errors = _preview(user_client, self.HEADERS, [["hidden", "PERSON"]])
        assert errors == []
        assert body["summary"]["valid"] == 1

    def test_visible_contact_name_is_reported(self, user_client, importer, mine):
        body, errors = _preview(user_client, self.HEADERS, [["mine", "VISIBLE"]])
        assert errors == [("first_name", NAME_CLASH)]
        assert body["summary"]["valid"] == 0

    def test_admin_sees_every_name_clash(self, admin_client, admin_profile, hidden):
        _body, errors = _preview(admin_client, self.HEADERS, [["Hidden", "Person"]])
        assert errors == [("first_name", NAME_CLASH)]


@pytest.mark.django_db
def test_email_clash_stays_org_wide(user_client, importer, hidden):
    """The per-org email index refuses the row at commit anyway, so the
    preview reports it whoever holds the address."""
    _body, errors = _preview(
        user_client, ["first_name", "last_name", "email"], [["A", "B", "HID@x.test"]]
    )
    assert [field for field, _msg in errors] == ["email"]
