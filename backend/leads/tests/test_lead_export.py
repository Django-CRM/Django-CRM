"""``GET /api/leads/export/``: the lead list, every page of it, as CSV."""

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from common.models import Tags
from common.tests.export_personas import make_personas, stamp_creator
from leads.models import Lead

LIST_URL = "/api/leads/"
EXPORT_URL = "/api/leads/export/"


def _rows(response):
    assert response.status_code == 200, response.content
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith("\ufeff")
    return list(csv.reader(io.StringIO(body[1:])))


def _first_names(response):
    rows = _rows(response)
    column = rows[0].index("First name")
    return {row[column] for row in rows[1:]}


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    assigned = Lead.objects.create(org=org_a, first_name="Assigned", status="assigned")
    assigned.assigned_to.add(people["assignee"].profile)
    stamp_creator(
        Lead,
        Lead.objects.create(org=org_a, first_name="Created", status="in process"),
        people["creator"].user,
    )
    Lead.objects.create(org=org_a, first_name="Orphan", status="recycled")
    Lead.objects.create(org=org_a, first_name="Closed", status="closed")
    Lead.objects.create(org=org_a, first_name="Converted", status="converted")
    return {"people": people, "assigned": assigned}


EVERYONE = {"Assigned", "Created", "Orphan", "Closed"}
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
    listed = client.get(LIST_URL, {"limit": 100}).data
    shown = {
        lead["first_name"]
        for half, key in (("open_leads", "open_leads"), ("close_leads", "close_leads"))
        for lead in listed[half][key]
    }
    assert shown == EXPECTED[who]
    assert _first_names(client.get(EXPORT_URL)) == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    assert _rows(org_b_client.get(EXPORT_URL))[1:] == []


def test_unauthenticated_refused(unauthenticated_client):
    assert unauthenticated_client.get(EXPORT_URL).status_code in (401, 403)


def test_open_leaves_out_closed_in_list_and_export(world):
    admin = world["people"]["admin"].client()
    listed = admin.get(LIST_URL, {"open": "true"}).data
    assert listed["close_leads"]["close_leads"] == []
    assert _first_names(admin.get(EXPORT_URL, {"open": "true"})) == {
        "Assigned",
        "Created",
        "Orphan",
    }


def test_every_page_not_just_the_first(world, org_a):
    Lead.objects.bulk_create(
        [Lead(org=org_a, first_name=f"Bulk{i}", status="assigned") for i in range(40)]
    )
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"limit": 5}))
    assert len(rows) == 1 + 44


def test_filters_honoured(world, org_a):
    admin = world["people"]["admin"].client()
    tag = Tags.objects.create(name="VIP", org=org_a)
    world["assigned"].tags.add(tag)

    assert _first_names(admin.get(EXPORT_URL, {"status": "recycled"})) == {"Orphan"}
    assert _first_names(
        admin.get(
            EXPORT_URL, {"assigned_to": str(world["people"]["assignee"].profile.id)}
        )
    ) == {"Assigned"}
    assert _first_names(admin.get(EXPORT_URL, {"tags": str(tag.id)})) == {"Assigned"}
    assert _first_names(admin.get(EXPORT_URL, {"search": "orph"})) == {"Orphan"}


def test_malformed_filter_is_a_400(world):
    admin = world["people"]["admin"].client()
    assert admin.get(EXPORT_URL, {"tags": "nope"}).status_code == 400
    assert admin.get(EXPORT_URL, {"created_at__gte": "banana"}).status_code == 400


def test_formula_cells_are_neutralised(world, org_a):
    Lead.objects.create(
        org=org_a,
        first_name='=HYPERLINK("https://evil.example")',
        last_name="+1",
        company_name="@SUM(A1)",
        job_title="-2",
        email="\t=cmd",
        status="assigned",
    )
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "HYPER"}))
    record = dict(zip(rows[0], rows[1]))
    assert record["First name"] == '\'=HYPERLINK("https://evil.example")'
    assert record["Last name"] == "'+1"
    assert record["Company"] == "'@SUM(A1)"
    assert record["Job title"] == "'-2"
    assert record["Email"] == "'\t=cmd"


def test_columns_are_readable(world, org_a):
    world["assigned"].tags.add(Tags.objects.create(name="VIP", org=org_a))
    rows = _rows(
        world["people"]["admin"].client().get(EXPORT_URL, {"search": "Assigned"})
    )
    record = dict(zip(rows[0], rows[1]))
    assert record["Status"] == "Assigned"
    assert record["Assigned to"] == "export-assignee@test.com"
    assert record["Tags"] == "VIP"


def test_dates_and_filename_follow_the_org_timezone(world, org_a):
    org_a.timezone = "Asia/Kolkata"
    org_a.save()
    Lead.objects.filter(pk=world["assigned"].pk).update(
        created_at=datetime(2026, 3, 1, 20, 0, tzinfo=ZoneInfo("UTC"))
    )
    response = world["people"]["admin"].client().get(EXPORT_URL, {"search": "Assigned"})
    assert response["Content-Disposition"].startswith('attachment; filename="leads-')
    assert response["Content-Disposition"].endswith('.csv"')
    rows = _rows(response)
    assert dict(zip(rows[0], rows[1]))["Created"] == "2026-03-02T01:30:00+05:30"


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    tag = Tags.objects.create(name="Bulk", org=org_a)
    for i in range(30):
        lead = Lead.objects.create(org=org_a, first_name=f"Bulk{i}", status="assigned")
        lead.assigned_to.add(world["people"]["assignee"].profile)
        lead.tags.add(tag)
    with django_assert_max_num_queries(12):
        rows = _rows(world["people"]["admin"].client().get(EXPORT_URL))
    assert len(rows) == 1 + 34
