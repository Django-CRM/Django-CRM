"""``GET /api/contacts/export/``: the contact list, every page of it, as CSV."""

import csv
import io

import pytest

from accounts.models import Account
from common.models import Tags
from common.tests.export_personas import make_personas, stamp_creator
from contacts.models import Contact

LIST_URL = "/api/contacts/"
EXPORT_URL = "/api/contacts/export/"


def _rows(response):
    assert response.status_code == 200, response.content
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith(BOM)
    return list(csv.reader(io.StringIO(body[1:])))


BOM = chr(0xFEFF)


def _first_names(response):
    rows = _rows(response)
    column = rows[0].index("First name")
    return {row[column] for row in rows[1:]}


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    assigned = Contact.objects.create(org=org_a, first_name="Assigned")
    assigned.assigned_to.add(people["assignee"].profile)
    stamp_creator(
        Contact,
        Contact.objects.create(org=org_a, first_name="Created"),
        people["creator"].user,
    )
    Contact.objects.create(org=org_a, first_name="Orphan")
    Contact.objects.create(org=org_a, first_name="Gone", is_active=False)
    return {"people": people, "assigned": assigned}


EVERYONE = {"Assigned", "Created", "Orphan", "Gone"}
EXPECTED = {
    "admin": EVERYONE,
    "superuser": EVERYONE,
    "assignee": {"Assigned"},
    "creator": {"Created"},
    "unrelated": set(),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_export_holds_what_the_list_shows(world, who):
    client = world["people"][who].client()
    listed = client.get(LIST_URL, {"limit": 100}).data["results"]
    assert {c["first_name"] for c in listed} == EXPECTED[who]
    assert _first_names(client.get(EXPORT_URL)) == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    assert _rows(org_b_client.get(EXPORT_URL))[1:] == []


def test_is_active_narrows_rows_but_not_the_list_totals(world):
    admin = world["people"]["admin"].client()
    listed = admin.get(LIST_URL, {"is_active": "true"}).data
    assert (listed["active_count"], listed["inactive_count"]) == (3, 1)
    assert _first_names(admin.get(EXPORT_URL, {"is_active": "true"})) == {
        "Assigned",
        "Created",
        "Orphan",
    }
    assert _first_names(admin.get(EXPORT_URL, {"is_active": "false"})) == {"Gone"}


def test_filters_honoured(world, org_a):
    admin = world["people"]["admin"].client()
    tag = Tags.objects.create(name="VIP", org=org_a)
    world["assigned"].tags.add(tag)
    assert _first_names(admin.get(EXPORT_URL, {"tags": str(tag.id)})) == {"Assigned"}
    assert _first_names(
        admin.get(
            EXPORT_URL, {"assigned_to": str(world["people"]["assignee"].profile.id)}
        )
    ) == {"Assigned"}
    assert _first_names(admin.get(EXPORT_URL, {"search": "orph"})) == {"Orphan"}


def test_columns_are_readable(world, org_a):
    account = Account.objects.create(name="Acme", org=org_a)
    account.contacts.add(world["assigned"])
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "Assig"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["Linked accounts"] == "Acme"
    assert record["Assigned to"] == "export-assignee@test.com"
    assert record["Active"] == "yes"


def test_formula_cells_are_neutralised(world, org_a):
    Contact.objects.create(org=org_a, first_name="=1+1", last_name="@x")
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "=1"}))
    record = dict(zip(rows[0], rows[1]))
    assert (record["First name"], record["Last name"]) == ("'=1+1", "'@x")


def test_filename(world):
    response = world["people"]["admin"].client().get(EXPORT_URL)
    assert response["Content-Disposition"].startswith('attachment; filename="contacts-')


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    account = Account.objects.create(name="Acme", org=org_a)
    for i in range(30):
        contact = Contact.objects.create(
            org=org_a, first_name=f"Bulk{i}", account=account
        )
        contact.assigned_to.add(world["people"]["assignee"].profile)
    with django_assert_max_num_queries(12):
        rows = _rows(world["people"]["admin"].client().get(EXPORT_URL))
    assert len(rows) == 1 + 34
