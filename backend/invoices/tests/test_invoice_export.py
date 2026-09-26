"""The invoice list's read rule, and the CSV export that shares it.

The list used to carry an inline copy of `visible_invoices_qs`. The per-caller
list test pins what it returns so moving it onto the helper is shown to change
nothing; the rest pin the export.
"""

import csv
import io
from decimal import Decimal

import pytest

from accounts.models import Account
from common.tests.export_personas import make_personas, stamp_creator
from contacts.models import Contact
from invoices.models import Invoice

LIST_URL = "/api/invoices/"
EXPORT_URL = "/api/invoices/export/"


def _rows(response):
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.startswith("\ufeff")
    return list(csv.reader(io.StringIO(body[1:])))


def _names(rows):
    header = rows[0]
    title = header.index("Title")
    return {row[title] for row in rows[1:]}


@pytest.fixture
def world(org_a):
    people = make_personas(org_a)
    account = Account.objects.create(name="Acme", org=org_a)
    contact = Contact.objects.create(first_name="Ada", last_name="Byron", org=org_a)

    def invoice(title, **kwargs):
        return Invoice.objects.create(
            org=org_a, account=account, invoice_title=title, currency="USD", **kwargs
        )

    assigned = invoice("Assigned invoice", contact=contact, status="Sent")
    assigned.assigned_to.add(people["assignee"].profile)
    created = stamp_creator(Invoice, invoice("Created invoice"), people["creator"].user)
    invoice("Nobody's invoice")
    return {
        "people": people,
        "account": account,
        "assigned": assigned,
        "created": created,
    }


EXPECTED = {
    "admin": {"Assigned invoice", "Created invoice", "Nobody's invoice"},
    "superuser": {"Assigned invoice", "Created invoice", "Nobody's invoice"},
    "assignee": {"Assigned invoice"},
    "creator": {"Created invoice"},
    "unrelated": set(),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_list_rows_per_caller(world, who):
    response = world["people"][who].client().get(LIST_URL, {"limit": 50})
    assert response.status_code == 200
    assert {i["invoice_title"] for i in response.data["results"]} == EXPECTED[who]


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_export_rows_per_caller(world, who):
    response = world["people"][who].client().get(EXPORT_URL)
    assert response.status_code == 200
    assert _names(_rows(response)) == EXPECTED[who]


def test_other_org_never_exported(world, org_b_client):
    assert _rows(org_b_client.get(EXPORT_URL))[1:] == []


def test_columns_are_readable_and_carry_no_secret(world):
    Invoice.objects.filter(pk=world["assigned"].pk).update(
        total_amount=Decimal("99.50"), public_token="s3cret-token-value"
    )
    response = world["people"]["admin"].client().get(EXPORT_URL, {"status": "Sent"})
    rows = _rows(response)
    record = dict(zip(rows[0], rows[1]))
    assert record["Account"] == "Acme"
    assert record["Contact"] == "Ada Byron"
    assert record["Total"] == "99.50"
    assert record["Currency"] == "USD"
    assert record["Assigned to"] == "export-assignee@test.com"
    assert (
        "s3cret-token-value"
        not in b"".join(
            world["people"]["admin"].client().get(EXPORT_URL).streaming_content
        ).decode()
    )


def test_filters_honoured(world):
    admin = world["people"]["admin"].client()
    assert _names(_rows(admin.get(EXPORT_URL, {"status": "Sent"}))) == {
        "Assigned invoice"
    }
    assert _names(
        _rows(
            admin.get(
                EXPORT_URL,
                {"assigned_to": str(world["people"]["assignee"].profile.id)},
            )
        )
    ) == {"Assigned invoice"}
    assert _names(_rows(admin.get(EXPORT_URL, {"search": "Created"}))) == {
        "Created invoice"
    }


@pytest.mark.parametrize("param", ["issue_date_gte", "due_date_lte", "account"])
def test_malformed_filter_is_a_400(world, param):
    """These date filters took raw text and answered 500 on a bad value."""
    admin = world["people"]["admin"].client()
    assert admin.get(EXPORT_URL, {param: "banana"}).status_code == 400
    assert admin.get(LIST_URL, {param: "banana"}).status_code == 400


def test_query_count_does_not_grow_with_rows(
    world, org_a, django_assert_max_num_queries
):
    for i in range(25):
        inv = Invoice.objects.create(
            org=org_a, account=world["account"], invoice_title=f"Bulk {i}"
        )
        inv.assigned_to.add(world["people"]["assignee"].profile)
    with django_assert_max_num_queries(15):
        rows = _rows(world["people"]["admin"].client().get(EXPORT_URL))
    assert len(rows) == 1 + 28


def test_formula_cells_are_neutralised_and_file_named(world, org_a):
    Invoice.objects.create(
        org=org_a, account=world["account"], invoice_title="=2+5", client_name="@evil"
    )
    response = world["people"]["admin"].client().get(EXPORT_URL, {"search": "=2"})
    assert response["Content-Disposition"].startswith('attachment; filename="invoices-')
    rows = _rows(response)
    record = dict(zip(rows[0], rows[1]))
    assert (record["Title"], record["Client name"]) == ("'=2+5", "'@evil")
