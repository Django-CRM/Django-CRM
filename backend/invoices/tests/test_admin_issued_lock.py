"""The Django admin holds the issued-document lock too.

On a non-Draft invoice or estimate the amount fields are read-only, the line
inlines cannot add, change or delete, and the standalone line admins refuse
to change, delete or add a line on one. A Draft keeps every one of those.
"""

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from accounts.models import Account
from common.models import User
from invoices.admin import (
    ESTIMATE_AMOUNT_FIELDS,
    INVOICE_AMOUNT_FIELDS,
    EstimateLineItemInline,
    InvoiceLineItemInline,
)
from invoices.models import Estimate, EstimateLineItem, Invoice, InvoiceLineItem

LOCKED = "Its lines and amounts can only be changed while it is a Draft."


@pytest.fixture
def request_(db):
    request = RequestFactory().get("/admin/")
    request.user = User.objects.create_superuser(
        email="staff@test.com", password="pw-not-used"
    )
    return request


def _invoice(org, status):
    account = Account.objects.create(name=f"Acme {status}", org=org)
    invoice = Invoice.objects.create(org=org, account=account, invoice_title="Work")
    line = InvoiceLineItem.objects.create(
        invoice=invoice, org=org, name="Design", quantity=1, unit_price=100
    )
    Invoice.objects.filter(pk=invoice.pk).update(status=status)
    invoice.refresh_from_db()
    return invoice, line


def _estimate(org, status):
    account = Account.objects.create(name=f"Acme {status}", org=org)
    estimate = Estimate.objects.create(org=org, account=account, title="Quote")
    line = EstimateLineItem.objects.create(
        estimate=estimate, org=org, name="Design", quantity=1, unit_price=100
    )
    Estimate.objects.filter(pk=estimate.pk).update(status=status)
    estimate.refresh_from_db()
    return estimate, line


CASES = [
    (Invoice, _invoice, InvoiceLineItemInline, INVOICE_AMOUNT_FIELDS, "Paid"),
    (Invoice, _invoice, InvoiceLineItemInline, INVOICE_AMOUNT_FIELDS, "Sent"),
    (Estimate, _estimate, EstimateLineItemInline, ESTIMATE_AMOUNT_FIELDS, "Sent"),
    (Estimate, _estimate, EstimateLineItemInline, ESTIMATE_AMOUNT_FIELDS, "Accepted"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("model, make, inline, amounts, status", CASES)
def test_issued_document_is_locked_in_the_admin(
    request_, org_a, model, make, inline, amounts, status
):
    document, line = make(org_a, status)
    document_admin = site._registry[model]
    line_admin = site._registry[type(line)]
    inline_admin = inline(model, site)

    assert set(amounts) <= set(document_admin.get_readonly_fields(request_, document))
    assert "notes" not in document_admin.get_readonly_fields(request_, document)
    assert not inline_admin.has_add_permission(request_, document)
    assert not inline_admin.has_change_permission(request_, document)
    assert not inline_admin.has_delete_permission(request_, document)
    assert not line_admin.has_change_permission(request_, line)
    assert not line_admin.has_delete_permission(request_, line)
    assert "delete_selected" not in line_admin.get_actions(request_)


@pytest.mark.django_db
@pytest.mark.parametrize("model, make, inline, amounts, status", CASES)
def test_draft_document_stays_editable_in_the_admin(
    request_, org_a, model, make, inline, amounts, status
):
    document, line = make(org_a, "Draft")
    document_admin = site._registry[model]
    line_admin = site._registry[type(line)]
    inline_admin = inline(model, site)

    assert not set(amounts) & set(
        document_admin.get_readonly_fields(request_, document)
    )
    assert inline_admin.has_add_permission(request_, document)
    assert inline_admin.has_change_permission(request_, document)
    assert inline_admin.has_delete_permission(request_, document)
    assert line_admin.has_change_permission(request_, line)
    assert line_admin.has_delete_permission(request_, line)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "make, field", [(_invoice, "invoice"), (_estimate, "estimate")]
)
def test_standalone_line_admin_refuses_a_new_line_on_an_issued_document(
    request_, org_a, make, field
):
    issued, line = make(org_a, "Sent")
    draft, _ = make(org_a, "Draft")
    line_admin = site._registry[type(line)]
    Form = line_admin.get_form(request_, None)

    def submit(document):
        return Form(
            data={
                field: document.pk,
                "org": org_a.pk,
                "name": "Extra",
                "quantity": "1",
                "unit_price": "10",
                "discount_value": "0",
                "discount_amount": "0",
                "tax_rate": "0",
                "tax_amount": "0",
                "subtotal": "0",
                "total": "0",
                "order": "0",
            }
        )

    refused = submit(issued)
    assert not refused.is_valid()
    assert LOCKED in str(refused.errors)

    allowed = submit(draft)
    assert allowed.is_valid(), allowed.errors
