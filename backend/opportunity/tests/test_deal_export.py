"""The deal list's read rule, and the CSV export that shares it.

The list used to carry an inline copy of `visible_deals_qs`. The first class
pins the rows the list returns for each kind of caller, so swapping the copy
for the helper is shown to change nothing; the rest pin the export.
"""

import csv
import io
from decimal import Decimal

import pytest

from accounts.models import Account
from common.models import Tags
from common.tests.export_personas import make_personas, stamp_creator
from opportunity.models import DealPipeline, DealStage, Opportunity

LIST_URL = "/api/opportunities/"
EXPORT_URL = "/api/opportunities/export/"


def _rows(response):
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith("\ufeff")
    return list(csv.reader(io.StringIO(body[1:])))


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    account = Account.objects.create(name="Acme", org=org_a)
    assigned = Opportunity.objects.create(
        org=org_a, name="Assigned deal", stage="QUALIFICATION", account=account
    )
    assigned.assigned_to.add(people["assignee"].profile)
    created = stamp_creator(
        Opportunity,
        Opportunity.objects.create(
            org=org_a, name="Created deal", stage="QUALIFICATION"
        ),
        people["creator"].user,
    )
    orphan = Opportunity.objects.create(
        org=org_a, name="Nobody's deal", stage="QUALIFICATION"
    )
    return {
        "people": people,
        "account": account,
        "deals": {"assigned": assigned, "created": created, "orphan": orphan},
    }


EXPECTED = {
    "admin": {"Assigned deal", "Created deal", "Nobody's deal"},
    "superuser": {"Assigned deal", "Created deal", "Nobody's deal"},
    "assignee": {"Assigned deal"},
    "creator": {"Created deal"},
    "unrelated": set(),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_list_rows_per_caller(world, who):
    response = world["people"][who].client().get(LIST_URL, {"limit": 50})
    assert response.status_code == 200
    assert {d["name"] for d in response.data["opportunities"]} == EXPECTED[who]


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_export_rows_per_caller(world, who):
    response = world["people"][who].client().get(EXPORT_URL)
    assert response.status_code == 200
    names = {row[1] for row in _rows(response)[1:]}
    assert names == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    response = org_b_client.get(EXPORT_URL)
    assert response.status_code == 200
    assert _rows(response)[1:] == []


def test_unauthenticated_refused(world, unauthenticated_client):
    assert unauthenticated_client.get(EXPORT_URL).status_code in (401, 403)


def test_columns_are_readable(world, org_a):
    deal = world["deals"]["assigned"]
    deal.amount = Decimal("1200.50")
    deal.currency = "EUR"
    deal.save()
    tag = Tags.objects.create(name="Hot", org=org_a)
    deal.tags.add(tag)
    pipeline = DealPipeline.default_for(org_a)
    label = DealStage.objects.get(
        org=org_a, pipeline=pipeline, code="QUALIFICATION"
    ).label

    rows = _rows(
        world["people"]["admin"].client().get(EXPORT_URL, {"search": "Assigned"})
    )
    header, row = rows[0], rows[1]
    record = dict(zip(header, row))
    assert record["Account"] == "Acme"
    assert record["Pipeline"] == pipeline.name
    assert record["Stage"] == label
    assert record["Amount"] == "1200.50"
    assert record["Currency"] == "EUR"
    assert record["Assigned to"] == "export-assignee@test.com"
    assert record["Tags"] == "Hot"


def test_filters_honoured(world, org_a):
    admin = world["people"]["admin"].client()
    by_account = _rows(admin.get(EXPORT_URL, {"account": str(world["account"].id)}))
    assert [r[1] for r in by_account[1:]] == ["Assigned deal"]
    by_assignee = _rows(
        admin.get(
            EXPORT_URL, {"assigned_to": str(world["people"]["assignee"].profile.id)}
        )
    )
    assert [r[1] for r in by_assignee[1:]] == ["Assigned deal"]
    Opportunity.objects.filter(pk=world["deals"]["orphan"].pk).update(
        stage="CLOSED_WON"
    )
    open_only = _rows(admin.get(EXPORT_URL, {"open": "true"}))
    assert "Nobody's deal" not in {r[1] for r in open_only[1:]}


def test_bad_filter_is_a_400_not_a_stream(world):
    response = world["people"]["admin"].client().get(EXPORT_URL, {"account": "nope"})
    assert response.status_code == 400


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    admin = world["people"]["admin"].client()
    for i in range(30):
        deal = Opportunity.objects.create(
            org=org_a, name=f"Bulk {i}", stage="QUALIFICATION"
        )
        deal.assigned_to.add(world["people"]["assignee"].profile)
    with django_assert_max_num_queries(15):
        response = admin.get(EXPORT_URL)
        rows = _rows(response)
    assert len(rows) == 1 + 33


def test_filename_uses_org_day(world):
    response = world["people"]["admin"].client().get(EXPORT_URL)
    assert response["Content-Disposition"].startswith('attachment; filename="deals-')


def test_formula_cells_are_neutralised(world, org_a):
    Opportunity.objects.create(org=org_a, name="+SUM(A1)", stage="QUALIFICATION")
    rows = _rows(world["people"]["admin"].client().get(EXPORT_URL, {"search": "SUM"}))
    assert rows[1][1] == "'+SUM(A1)"
