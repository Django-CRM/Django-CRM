"""A same-org invoice, estimate or schedule the caller cannot open answers
exactly like a missing id, on every verb (owner decision, 1.11.0).

It used to answer 403 "Permission denied", which told a member the record
exists. Each case below sends the same request twice, once at a record the
member cannot open and once at an id that does not exist, and requires the
two responses to be identical: status and whole body. The allowed side is
pinned too, so the check is shown to say yes as well as no.
"""

import uuid
from unittest.mock import patch

import pytest

from accounts.models import Account
from invoices.models import Estimate, Invoice, InvoiceLineItem, RecurringInvoice

INVOICE_CALLS = [
    ("get", "{id}/"),
    ("put", "{id}/"),
    ("post", "{id}/send/"),
    ("post", "{id}/mark-paid/"),
    ("post", "{id}/duplicate/"),
    ("post", "{id}/cancel/"),
    ("get", "{id}/pdf/"),
    ("get", "{id}/line-items/"),
    ("post", "{id}/line-items/"),
    ("put", "{id}/line-items/{child}/"),
    ("delete", "{id}/line-items/{child}/"),
    ("get", "{id}/payments/"),
    ("post", "{id}/payments/"),
    ("delete", "{id}/payments/{child}/"),
    ("post", "{id}/comments/"),
    ("post", "{id}/attachments/"),
]
ESTIMATE_CALLS = [
    ("get", "estimates/{id}/"),
    ("put", "estimates/{id}/"),
    ("delete", "estimates/{id}/"),
    ("post", "estimates/{id}/convert/"),
    ("post", "estimates/{id}/send/"),
    ("get", "estimates/{id}/pdf/"),
]
RECURRING_CALLS = [
    ("get", "recurring/{id}/"),
    ("put", "recurring/{id}/"),
    ("delete", "recurring/{id}/"),
    ("post", "recurring/{id}/toggle/"),
]


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
def documents(org_a, admin_user):
    """One of each, created by the admin and assigned to nobody, so the
    member behind `user_client` cannot open any of them."""
    account = Account.objects.create(name="Acme", org=org_a)
    invoice = Invoice.objects.create(org=org_a, account=account, invoice_title="Hidden")
    line = InvoiceLineItem.objects.create(
        invoice=invoice, org=org_a, name="Work", quantity=1, unit_price=10
    )
    estimate = Estimate.objects.create(org=org_a, account=account, title="Hidden")
    recurring = RecurringInvoice.objects.create(
        org=org_a,
        account=account,
        title="Hidden",
        start_date="2026-09-01",
        next_generation_date="2026-09-01",
    )
    # Set without save(), which stamps created_by from the request user.
    for obj in (invoice, estimate, recurring):
        type(obj).objects.filter(pk=obj.pk).update(created_by=admin_user)
    return {
        "invoice": invoice,
        "line": line,
        "estimate": estimate,
        "recurring": recurring,
    }


def _call(client, verb, template, record_id, child_id):
    path = "/api/invoices/" + template.format(id=record_id, child=child_id)
    return getattr(client, verb)(path, {}, format="json")


def _same_as_missing(client, verb, template, hidden_id, child_id):
    hidden = _call(client, verb, template, hidden_id, child_id)
    missing = _call(client, verb, template, uuid.uuid4(), child_id)
    assert hidden.status_code == missing.status_code == 404, template
    assert hidden.json() == missing.json(), template


@pytest.mark.django_db
@pytest.mark.parametrize("verb, template", INVOICE_CALLS)
def test_hidden_invoice_answers_like_a_missing_one(
    user_client, documents, verb, template
):
    _same_as_missing(
        user_client, verb, template, documents["invoice"].id, documents["line"].id
    )
    assert Invoice.objects.filter(invoice_title__startswith="Copy of").count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("verb, template", ESTIMATE_CALLS)
def test_hidden_estimate_answers_like_a_missing_one(
    user_client, documents, verb, template
):
    _same_as_missing(user_client, verb, template, documents["estimate"].id, None)
    assert Estimate.objects.filter(id=documents["estimate"].id).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("verb, template", RECURRING_CALLS)
def test_hidden_schedule_answers_like_a_missing_one(
    user_client, documents, verb, template
):
    _same_as_missing(user_client, verb, template, documents["recurring"].id, None)
    assert RecurringInvoice.objects.get(id=documents["recurring"].id).is_active


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key, template",
    [
        ("invoice", "{id}/"),
        ("estimate", "estimates/{id}/"),
        ("recurring", "recurring/{id}/"),
    ],
)
def test_the_owner_side_still_opens_them(
    admin_client, user_client, user_profile, documents, key, template
):
    record = documents[key]
    assert _call(admin_client, "get", template, record.id, None).status_code == 200

    record.assigned_to.add(user_profile)
    assert _call(user_client, "get", template, record.id, None).status_code == 200
