"""An issued invoice or estimate keeps its lines and amounts (owner decision,
1.11.0).

Once a document leaves Draft, every write path refuses a change to its lines,
discounts, tax, shipping or currency with a 400: the document update (PUT and
its nested ``line_items``) and the three invoice line-item endpoints. Status
changes, payments, notes and dates stay editable. One rule,
`invoices.serializer.issued_lock_message`, backs every path.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from accounts.models import Account
from contacts.models import Contact
from invoices.models import (
    ESTIMATE_STATUS,
    INVOICE_STATUS,
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceLineItem,
    Payment,
)

LOCKED = "Its lines and amounts can only be changed while it is a Draft."

ISSUED_INVOICE = [code for code, _ in INVOICE_STATUS if code != "Draft"]
ISSUED_ESTIMATE = [code for code, _ in ESTIMATE_STATUS if code != "Draft"]

# One changed value for every locked field. `line_items` replaces the lines.
NEW_LINES = [{"name": "Other", "quantity": "1", "unit_price": "10.00"}]
INVOICE_CHANGES = {
    "line_items": NEW_LINES,
    "discount_type": "PERCENTAGE",
    "discount_value": "5",
    "tax_rate": "7.5",
    "shipping_amount": "4",
    "currency": "EUR",
}
ESTIMATE_CHANGES = {
    key: value for key, value in INVOICE_CHANGES.items() if key != "shipping_amount"
}


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
def account(org_a, admin_user):
    return Account.objects.create(name="Buyer Co", org=org_a, created_by=admin_user)


@pytest.fixture
def contact(org_a, admin_user):
    return Contact.objects.create(
        first_name="Ada", last_name="Buyer", org=org_a, created_by=admin_user
    )


def _invoice(org, account, status):
    invoice = Invoice.objects.create(
        invoice_title="Work",
        account=account,
        client_email="buyer@example.com",
        currency="USD",
        org=org,
    )
    InvoiceLineItem.objects.create(
        invoice=invoice, org=org, name="Design", quantity=2, unit_price=100
    )
    invoice.recalculate_totals()
    invoice.save()
    Invoice.objects.filter(id=invoice.id).update(status=status)
    invoice.refresh_from_db()
    return invoice


def _estimate(org, account, status):
    estimate = Estimate.objects.create(
        title="Quote",
        account=account,
        client_email="buyer@example.com",
        currency="USD",
        org=org,
    )
    EstimateLineItem.objects.create(
        estimate=estimate, org=org, name="Design", quantity=2, unit_price=100
    )
    estimate.recalculate_totals()
    estimate.save()
    Estimate.objects.filter(id=estimate.id).update(status=status)
    estimate.refresh_from_db()
    return estimate


def _snapshot(document):
    document.refresh_from_db()
    return (
        [
            (line.name, line.quantity, line.unit_price)
            for line in document.line_items.order_by("order", "name")
        ],
        document.discount_type,
        document.discount_value,
        document.tax_rate,
        getattr(document, "shipping_amount", None),
        document.currency,
        document.subtotal,
        document.total_amount,
    )


def _invoice_url(invoice):
    return f"/api/invoices/{invoice.id}/"


def _estimate_url(estimate):
    return f"/api/invoices/estimates/{estimate.id}/"


@pytest.mark.django_db
class TestInvoiceUpdate:
    @pytest.mark.parametrize("status", ISSUED_INVOICE)
    @pytest.mark.parametrize("field", INVOICE_CHANGES)
    def test_issued_invoice_refuses_a_change(
        self, admin_client, org_a, account, status, field
    ):
        invoice = _invoice(org_a, account, status)
        before = _snapshot(invoice)

        response = admin_client.put(
            _invoice_url(invoice), {field: INVOICE_CHANGES[field]}, format="json"
        )

        assert response.status_code == 400, response.content
        assert LOCKED in str(response.json()["errors"][field])
        assert _snapshot(invoice) == before

    @pytest.mark.parametrize("field", INVOICE_CHANGES)
    def test_draft_invoice_takes_the_change(self, admin_client, org_a, account, field):
        invoice = _invoice(org_a, account, "Draft")
        before = _snapshot(invoice)

        response = admin_client.put(
            _invoice_url(invoice), {field: INVOICE_CHANGES[field]}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _snapshot(invoice) != before

    def test_issued_invoice_accepts_its_stored_values_back(
        self, admin_client, org_a, account
    ):
        """Resending what is stored changes nothing, so it is not refused and
        the issued totals are not recomputed."""
        invoice = _invoice(org_a, account, "Sent")
        Invoice.objects.filter(id=invoice.id).update(total_amount=Decimal("999.00"))
        before = _snapshot(invoice)

        response = admin_client.put(
            _invoice_url(invoice),
            {
                "currency": "USD",
                "tax_rate": "0.00",
                "shipping_amount": "0",
                "discount_type": "",
                "discount_value": "0",
                "notes": "Resent with the form",
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        assert _snapshot(invoice) == before
        assert invoice.notes == "Resent with the form"

    @pytest.mark.parametrize("status", ISSUED_INVOICE)
    def test_issued_invoice_takes_notes_and_due_date(
        self, admin_client, org_a, account, status
    ):
        invoice = _invoice(org_a, account, status)
        due = datetime.date(2030, 1, 31)

        response = admin_client.put(
            _invoice_url(invoice),
            {"notes": "Paid by wire", "due_date": str(due), "po_number": "PO-7"},
            format="json",
        )

        assert response.status_code == 200, response.content
        invoice.refresh_from_db()
        assert (invoice.notes, invoice.due_date, invoice.po_number) == (
            "Paid by wire",
            due,
            "PO-7",
        )


@pytest.mark.django_db
class TestInvoiceLineItemEndpoints:
    def _add(self, client, invoice):
        return client.post(
            f"/api/invoices/{invoice.id}/line-items/",
            {"name": "Extra", "quantity": "1", "unit_price": "50"},
            format="json",
        )

    def _edit(self, client, invoice):
        line = invoice.line_items.get()
        return client.put(
            f"/api/invoices/{invoice.id}/line-items/{line.id}/",
            {"quantity": "3"},
            format="json",
        )

    def _reorder(self, client, invoice):
        line = invoice.line_items.get()
        return client.put(
            f"/api/invoices/{invoice.id}/line-items/{line.id}/",
            {"order": 5},
            format="json",
        )

    def _delete(self, client, invoice):
        line = invoice.line_items.get()
        return client.delete(f"/api/invoices/{invoice.id}/line-items/{line.id}/")

    WRITES = ["_add", "_edit", "_reorder", "_delete"]

    @pytest.mark.parametrize("status", ISSUED_INVOICE)
    @pytest.mark.parametrize("write", WRITES)
    def test_issued_invoice_refuses_line_writes(
        self, admin_client, org_a, account, status, write
    ):
        invoice = _invoice(org_a, account, status)
        before = _snapshot(invoice)
        order = invoice.line_items.get().order

        response = getattr(self, write)(admin_client, invoice)

        assert response.status_code == 400, response.content
        assert LOCKED in response.json()["message"]
        assert _snapshot(invoice) == before
        assert invoice.line_items.get().order == order

    @pytest.mark.parametrize("write", WRITES)
    def test_draft_invoice_takes_line_writes(self, admin_client, org_a, account, write):
        invoice = _invoice(org_a, account, "Draft")

        response = getattr(self, write)(admin_client, invoice)

        assert response.status_code in (200, 201), response.content

    def test_draft_line_write_recomputes_the_totals(self, admin_client, org_a, account):
        invoice = _invoice(org_a, account, "Draft")

        self._add(admin_client, invoice)

        invoice.refresh_from_db()
        assert invoice.total_amount == Decimal("250.00")

    def test_hidden_issued_invoice_answers_as_before_not_with_its_status(
        self, user_client, org_a, account
    ):
        """The lock is checked after the access check, so a member who cannot
        open the invoice is not told that it has been issued."""
        invoice = _invoice(org_a, account, "Paid")

        response = self._add(user_client, invoice)

        assert response.status_code == 404
        assert LOCKED not in str(response.content)
        assert invoice.line_items.count() == 1


@pytest.mark.django_db
class TestIssuedInvoiceStillMoves:
    def test_payment_and_payment_delete(self, admin_client, org_a, account):
        invoice = _invoice(org_a, account, "Sent")

        paid = admin_client.post(
            f"/api/invoices/{invoice.id}/payments/",
            {
                "amount": "50.00",
                "payment_date": "2026-09-01",
                "payment_method": "CASH",
            },
            format="json",
        )
        assert paid.status_code == 201, paid.content
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("50.00")
        assert invoice.total_amount == Decimal("200.00")

        payment = Payment.objects.get(invoice=invoice)
        removed = admin_client.delete(
            f"/api/invoices/{invoice.id}/payments/{payment.id}/"
        )
        assert removed.status_code == 200, removed.content

    def test_mark_paid_and_cancel(self, admin_client, org_a, account):
        sent = _invoice(org_a, account, "Sent")
        response = admin_client.post(f"/api/invoices/{sent.id}/mark-paid/")
        assert response.status_code == 200, response.content
        sent.refresh_from_db()
        assert sent.status == "Paid"

        overdue = _invoice(org_a, account, "Overdue")
        response = admin_client.post(f"/api/invoices/{overdue.id}/cancel/")
        assert response.status_code == 200, response.content
        overdue.refresh_from_db()
        assert overdue.status == "Cancelled"

    def test_resend(self, admin_client, org_a, account):
        invoice = _invoice(org_a, account, "Viewed")

        response = admin_client.post(f"/api/invoices/{invoice.id}/send/")

        assert response.status_code == 200, response.content


@pytest.mark.django_db
class TestEstimateUpdate:
    @pytest.mark.parametrize("status", ISSUED_ESTIMATE)
    @pytest.mark.parametrize("field", ESTIMATE_CHANGES)
    def test_issued_estimate_refuses_a_change(
        self, admin_client, org_a, account, status, field
    ):
        estimate = _estimate(org_a, account, status)
        before = _snapshot(estimate)

        response = admin_client.put(
            _estimate_url(estimate), {field: ESTIMATE_CHANGES[field]}, format="json"
        )

        assert response.status_code == 400, response.content
        assert LOCKED in str(response.json()["errors"][field])
        assert _snapshot(estimate) == before

    @pytest.mark.parametrize("field", ESTIMATE_CHANGES)
    def test_draft_estimate_takes_the_change(self, admin_client, org_a, account, field):
        estimate = _estimate(org_a, account, "Draft")
        before = _snapshot(estimate)

        response = admin_client.put(
            _estimate_url(estimate), {field: ESTIMATE_CHANGES[field]}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _snapshot(estimate) != before

    @pytest.mark.parametrize("status", ISSUED_ESTIMATE)
    def test_issued_estimate_takes_notes_and_expiry(
        self, admin_client, org_a, account, status
    ):
        estimate = _estimate(org_a, account, status)
        expiry = datetime.date(2030, 1, 31)

        response = admin_client.put(
            _estimate_url(estimate),
            {"notes": "Valid for a month", "expiry_date": str(expiry)},
            format="json",
        )

        assert response.status_code == 200, response.content
        estimate.refresh_from_db()
        assert (estimate.notes, estimate.expiry_date) == ("Valid for a month", expiry)

    def test_issued_estimate_still_converts(self, admin_client, org_a, account):
        estimate = _estimate(org_a, account, "Sent")

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")

        assert response.status_code == 201, response.content
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"
        assert estimate.converted_to_invoice.total_amount == Decimal("200.00")
