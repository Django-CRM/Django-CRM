"""``GET /api/cases/export/``: the ticket queue, every page of it, as CSV."""

import csv
import io

import pytest

from accounts.models import Account
from cases.models import Case, CaseWatcher
from common.models import Tags
from common.tests.export_personas import make_personas, stamp_creator

LIST_URL = "/api/cases/"
EXPORT_URL = "/api/cases/export/"
BOM = chr(0xFEFF)


def _rows(response):
    assert response.status_code == 200, response.content
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith(BOM)
    return list(csv.reader(io.StringIO(body[1:])))


def _subjects(response):
    rows = _rows(response)
    column = rows[0].index("Subject")
    return {row[column] for row in rows[1:]}


def _case(org, name, **kwargs):
    kwargs.setdefault("status", "New")
    return Case.objects.create(org=org, name=name, priority="Normal", **kwargs)


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    assigned = _case(org_a, "Assigned")
    assigned.assigned_to.add(people["assignee"].profile)
    stamp_creator(Case, _case(org_a, "Created"), people["creator"].user)
    watched = _case(org_a, "Watched")
    CaseWatcher.objects.create(
        case=watched, profile=people["unrelated"].profile, org=org_a
    )
    _case(org_a, "Orphan")
    deleted = _case(org_a, "Deleted", is_active=False)
    deleted.assigned_to.add(people["assignee"].profile)
    duplicate = _case(org_a, "Duplicate", status="Duplicate")
    duplicate.assigned_to.add(people["assignee"].profile)
    merged = _case(org_a, "Merged", status="Closed", merged_into=assigned)
    merged.assigned_to.add(people["assignee"].profile)
    return {"people": people, "assigned": assigned}


EVERYONE = {"Assigned", "Created", "Watched", "Orphan"}
EXPECTED = {
    "admin": EVERYONE,
    "assignee": {"Assigned"},
    "creator": {"Created"},
    # A watcher may open the ticket, so it is theirs to export too.
    "unrelated": {"Watched"},
    # `visible_cases_qs` reads the role only; a superuser holding a member
    # profile gets a member's queue, in the list and in the file alike.
    "superuser": set(),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_export_holds_what_the_list_shows(world, who):
    client = world["people"][who].client()
    listed = client.get(LIST_URL, {"limit": 100, "slim": "true"}).data["cases"]
    assert {c["name"] for c in listed} == EXPECTED[who]
    assert _subjects(client.get(EXPORT_URL)) == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    assert _rows(org_b_client.get(EXPORT_URL))[1:] == []


def test_admin_may_include_deleted_and_merged(world):
    admin = world["people"]["admin"].client()
    assert "Deleted" in _subjects(admin.get(EXPORT_URL, {"include_deleted": "true"}))
    shown = _subjects(admin.get(EXPORT_URL, {"show_merged": "true"}))
    assert {"Duplicate", "Merged"} <= shown


def test_member_asking_for_deleted_gets_none(world):
    assignee = world["people"]["assignee"].client()
    assert _subjects(assignee.get(EXPORT_URL, {"include_deleted": "true"})) == {
        "Assigned"
    }
    listed = assignee.get(LIST_URL, {"include_deleted": "true"}).data["cases"]
    assert {c["name"] for c in listed} == {"Assigned"}


def test_filters_honoured(world, org_a):
    admin = world["people"]["admin"].client()
    account = Account.objects.create(name="Acme", org=org_a)
    Case.objects.filter(pk=world["assigned"].pk).update(
        account=account, priority="Urgent"
    )
    tag = Tags.objects.create(name="VIP", org=org_a)
    world["assigned"].tags.add(tag)

    assert _subjects(admin.get(EXPORT_URL, {"priority": "Urgent"})) == {"Assigned"}
    assert _subjects(admin.get(EXPORT_URL, {"account": str(account.id)})) == {
        "Assigned"
    }
    assert _subjects(admin.get(EXPORT_URL, {"tags": str(tag.id)})) == {"Assigned"}
    assert _subjects(
        admin.get(
            EXPORT_URL, {"assigned_to": str(world["people"]["assignee"].profile.id)}
        )
    ) == {"Assigned"}
    assert _subjects(admin.get(f"{EXPORT_URL}?status=New&status=Assigned")) == EVERYONE
    assert _subjects(admin.get(EXPORT_URL, {"search": "orph"})) == {"Orphan"}


def test_columns_and_injection_guard(world, org_a):
    _case(org_a, "@SUM(1+1)*cmd|' /C calc'!A0")
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "SUM"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["Subject"] == "'@SUM(1+1)*cmd|' /C calc'!A0"
    assert record["Status"] == "New"
    assert record["Priority"] == "Normal"


def test_filename(world):
    response = world["people"]["admin"].client().get(EXPORT_URL)
    assert response["Content-Disposition"].startswith('attachment; filename="tickets-')


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    for i in range(30):
        case = _case(org_a, f"Bulk {i}")
        case.assigned_to.add(world["people"]["assignee"].profile)
    with django_assert_max_num_queries(12):
        rows = _rows(world["people"]["admin"].client().get(EXPORT_URL))
    assert len(rows) == 1 + 34
