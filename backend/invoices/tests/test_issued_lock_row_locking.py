"""The issued-document check and the write it guards run on a locked row.

Every path that checks `issued_lock_message` or full-saves a document after
reading it (the invoice and estimate updates, the three invoice line-item
endpoints, both sends and estimate conversion) reads the row with
`select_for_update()` inside one transaction. Without it, a send landing
between the check and the save let a line change onto a Sent invoice, and
the edit's full save wrote the stale "Draft" back over "Sent".

A real race needs two connections to PostgreSQL. What is deterministic on
the SQLite test database is that each path asks for the lock; the query-level
test below proves the SQL on PostgreSQL.
"""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from invoices import api_views
from invoices.models import Estimate, EstimateLineItem, Invoice, InvoiceLineItem


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
        patch("invoices.api_views.send_invoice_to_client"),
        patch("invoices.tasks.send_estimate_to_client.delay"),
    ):
        yield


@pytest.fixture
def docs(org_a):
    account = Account.objects.create(name="Acme", org=org_a)
    invoice = Invoice.objects.create(
        org=org_a, account=account, invoice_title="Work", client_email="a@b.co"
    )
    line = InvoiceLineItem.objects.create(
        invoice=invoice, org=org_a, name="Design", quantity=1, unit_price=10
    )
    estimate = Estimate.objects.create(
        org=org_a, account=account, title="Quote", client_email="a@b.co"
    )
    EstimateLineItem.objects.create(
        estimate=estimate, org=org_a, name="Design", quantity=1, unit_price=10
    )
    return {"invoice": invoice, "line": line, "estimate": estimate}


INVOICE_CALLS = [
    ("put", "{invoice}/", {"notes": "x"}),
    ("post", "{invoice}/send/", {}),
    ("post", "{invoice}/line-items/", {"name": "B", "quantity": 1, "unit_price": 5}),
    ("put", "{invoice}/line-items/{line}/", {"quantity": 2}),
    ("delete", "{invoice}/line-items/{line}/", {}),
]
ESTIMATE_CALLS = [
    ("put", "estimates/{estimate}/", {"notes": "x"}),
    ("post", "estimates/{estimate}/send/", {}),
    ("post", "estimates/{estimate}/convert/", {}),
]


def _run(client, docs, verb, template, body):
    url = "/api/invoices/" + template.format(
        invoice=docs["invoice"].id, line=docs["line"].id, estimate=docs["estimate"].id
    )
    return getattr(client, verb)(url, body, format="json")


def _spy(name):
    """Wrap an `api_views` fetch helper, recording whether each call asked
    for a locked read."""
    real = getattr(api_views, name)
    locked = []

    def spy(request, pk, queryset=None):
        locked.append(queryset is not None and queryset.query.select_for_update)
        return real(request, pk, queryset)

    return patch.object(api_views, name, spy), locked


@pytest.mark.django_db
@pytest.mark.parametrize("verb, template, body", INVOICE_CALLS)
def test_invoice_writes_read_a_locked_row(admin_client, docs, verb, template, body):
    patcher, locked = _spy("get_invoice_or_error")
    with patcher:
        response = _run(admin_client, docs, verb, template, body)

    assert response.status_code in (200, 201), response.content
    assert locked == [True]


@pytest.mark.django_db
@pytest.mark.parametrize("verb, template, body", ESTIMATE_CALLS)
def test_estimate_writes_read_a_locked_row(admin_client, docs, verb, template, body):
    patcher, locked = _spy("get_estimate_or_error")
    with patcher:
        response = _run(admin_client, docs, verb, template, body)

    assert response.status_code in (200, 201), response.content
    assert locked == [True]


@pytest.mark.django_db
def test_the_invoice_read_is_for_update_on_postgres(admin_client, docs):
    if not connection.features.has_select_for_update:
        pytest.skip("SQLite has no row locks; this runs against PostgreSQL in CI")
    with CaptureQueriesContext(connection) as queries:
        admin_client.put(
            f"/api/invoices/{docs['invoice'].id}/", {"notes": "x"}, format="json"
        )

    assert any(
        "FOR UPDATE" in q["sql"] and 'FROM "invoice"' in q["sql"]
        for q in queries.captured_queries
    )
