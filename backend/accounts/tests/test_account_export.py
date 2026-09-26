"""``GET /api/accounts/export/``: the account list, every page of it, as CSV."""

import csv
import io

import pytest

from accounts.models import Account
from common.models import Tags
from common.tests.export_personas import make_personas, stamp_creator

LIST_URL = "/api/accounts/"
EXPORT_URL = "/api/accounts/export/"
BOM = chr(0xFEFF)


def _rows(response):
    assert response.status_code == 200, response.content
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith(BOM)
    return list(csv.reader(io.StringIO(body[1:])))


def _names(response):
    rows = _rows(response)
    column = rows[0].index("Name")
    return {row[column] for row in rows[1:]}


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    assigned = Account.objects.create(org=org_a, name="Assigned Co")
    assigned.assigned_to.add(people["assignee"].profile)
    stamp_creator(
        Account,
        Account.objects.create(org=org_a, name="Created Co"),
        people["creator"].user,
    )
    Account.objects.create(org=org_a, name="Orphan Co")
    Account.objects.create(org=org_a, name="Dormant Co", is_active=False)
    return {"people": people, "assigned": assigned}


EVERYONE = {"Assigned Co", "Created Co", "Orphan Co", "Dormant Co"}
EXPECTED = {
    "admin": EVERYONE,
    "superuser": EVERYONE,
    "assignee": {"Assigned Co"},
    "creator": {"Created Co"},
    "unrelated": set(),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_export_holds_what_the_list_shows(world, who):
    client = world["people"][who].client()
    listed = client.get(LIST_URL, {"limit": 100}).data
    shown = {a["name"] for a in listed["active_accounts"]["open_accounts"]} | {
        a["name"] for a in listed["closed_accounts"]["close_accounts"]
    }
    assert shown == EXPECTED[who]
    assert _names(client.get(EXPORT_URL)) == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    assert _rows(org_b_client.get(EXPORT_URL))[1:] == []


def test_is_active_picks_the_half_the_page_shows(world):
    admin = world["people"]["admin"].client()
    assert _names(admin.get(EXPORT_URL, {"is_active": "true"})) == {
        "Assigned Co",
        "Created Co",
        "Orphan Co",
    }
    assert _names(admin.get(EXPORT_URL, {"is_active": "false"})) == {"Dormant Co"}
    listed = admin.get(LIST_URL, {"is_active": "true"}).data
    assert listed["closed_accounts"]["close_accounts"] == []


def test_filters_honoured(world, org_a):
    admin = world["people"]["admin"].client()
    tag = Tags.objects.create(name="VIP", org=org_a)
    world["assigned"].tags.add(tag)
    assert _names(admin.get(EXPORT_URL, {"tags": str(tag.id)})) == {"Assigned Co"}
    assert _names(
        admin.get(
            EXPORT_URL, {"assigned_to": str(world["people"]["assignee"].profile.id)}
        )
    ) == {"Assigned Co"}
    assert _names(admin.get(EXPORT_URL, {"search": "orph"})) == {"Orphan Co"}


def test_columns_and_injection_guard(world, org_a):
    Account.objects.create(org=org_a, name="=cmd|' /C calc'!A0", industry="FINANCE")
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "cmd"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["Name"] == "'=cmd|' /C calc'!A0"
    assert record["Industry"] == "FINANCE"
    assert record["Active"] == "yes"


def test_filename(world):
    response = world["people"]["admin"].client().get(EXPORT_URL)
    assert response["Content-Disposition"].startswith('attachment; filename="accounts-')


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    for i in range(30):
        account = Account.objects.create(org=org_a, name=f"Bulk {i}")
        account.assigned_to.add(world["people"]["assignee"].profile)
    with django_assert_max_num_queries(12):
        rows = _rows(world["people"]["admin"].client().get(EXPORT_URL))
    assert len(rows) == 1 + 34
