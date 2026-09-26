import datetime
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import (
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceHistory,
    InvoiceLineItem,
    InvoiceTemplate,
    Payment,
    Product,
    RecurringInvoice,
    RecurringInvoiceLineItem,
)

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_for_invoice(org_a):
    return Account.objects.create(name="Invoice Test Account", org=org_a)


@pytest.fixture
def contact_for_invoice(org_a):
    return Contact.objects.create(
        first_name="Test",
        last_name="Contact",
        email="testcontact@example.com",
        org=org_a,
    )


@pytest.fixture
def account_org_b(org_b):
    return Account.objects.create(name="Org B Account", org=org_b)


@pytest.fixture
def contact_org_b(org_b):
    return Contact.objects.create(
        first_name="OrgB",
        last_name="Contact",
        email="orgb@example.com",
        org=org_b,
    )


@pytest.fixture
def invoice(account_for_invoice, org_a):
    return Invoice.objects.create(
        invoice_title="Test Invoice",
        account=account_for_invoice,
        currency="USD",
        org=org_a,
    )


@pytest.fixture
def invoice_org_b(account_org_b, org_b):
    return Invoice.objects.create(
        invoice_title="Org B Invoice",
        account=account_org_b,
        currency="USD",
        org=org_b,
    )


@pytest.fixture
def estimate_org_b(account_org_b, contact_org_b, org_b):
    return Estimate.objects.create(
        title="Org B Estimate",
        account=account_org_b,
        contact=contact_org_b,
        client_name="Org B Client",
        client_email="orgbclient@example.com",
        currency="USD",
        issue_date=timezone.localdate(),
        expiry_date=timezone.localdate() + datetime.timedelta(days=30),
        org=org_b,
    )


@pytest.fixture
def product(org_a):
    return Product.objects.create(
        name="Test Product",
        description="A test product",
        sku="TP-001",
        price=Decimal("99.99"),
        currency="USD",
        category="Software",
        is_active=True,
        org=org_a,
    )


@pytest.fixture
def product_inactive(org_a):
    return Product.objects.create(
        name="Inactive Product",
        sku="TP-002",
        price=Decimal("49.99"),
        is_active=False,
        org=org_a,
    )


@pytest.fixture
def template(org_a):
    return InvoiceTemplate.objects.create(
        name="Default Template",
        primary_color="#3B82F6",
        secondary_color="#1E40AF",
        default_notes="Thank you for your business",
        default_terms="Net 30",
        footer_text="Footer text",
        is_default=True,
        org=org_a,
    )


@pytest.fixture
def estimate(account_for_invoice, contact_for_invoice, org_a):
    return Estimate.objects.create(
        title="Test Estimate",
        account=account_for_invoice,
        contact=contact_for_invoice,
        client_name="Test Client",
        client_email="client@example.com",
        currency="USD",
        issue_date=timezone.localdate(),
        expiry_date=timezone.localdate() + datetime.timedelta(days=30),
        org=org_a,
    )


@pytest.fixture
def recurring_invoice(account_for_invoice, contact_for_invoice, org_a):
    return RecurringInvoice.objects.create(
        title="Monthly Hosting",
        account=account_for_invoice,
        contact=contact_for_invoice,
        client_name="Recurring Client",
        client_email="recurring@example.com",
        frequency="MONTHLY",
        start_date=timezone.localdate(),
        next_generation_date=timezone.localdate(),
        payment_terms="NET_30",
        currency="USD",
        is_active=True,
        org=org_a,
    )


@pytest.fixture
def invoice_with_balance(account_for_invoice, org_a):
    """An invoice carrying a 1000.00 outstanding balance.

    The plain ``invoice`` fixture has no line items, so its amount_due is 0 and
    any payment against it is now rejected as exceeding the balance.
    """
    inv = Invoice.objects.create(
        invoice_title="Invoice With Balance",
        account=account_for_invoice,
        currency="USD",
        org=org_a,
    )
    InvoiceLineItem.objects.create(
        invoice=inv,
        name="Consulting",
        quantity=Decimal("10"),
        unit_price=Decimal("100.00"),
        org=org_a,
    )
    inv.recalculate_totals()
    inv.save()
    return inv


@pytest.fixture
def line_item(invoice, org_a):
    return InvoiceLineItem.objects.create(
        invoice=invoice,
        name="Widget A",
        quantity=Decimal("5"),
        unit_price=Decimal("20.00"),
        org=org_a,
    )


@pytest.fixture
def payment(invoice, org_a):
    return Payment.objects.create(
        invoice=invoice,
        amount=Decimal("100.00"),
        payment_date=timezone.localdate(),
        payment_method="CASH",
        org=org_a,
    )


# ---------------------------------------------------------------------------
# Invoice List / Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceListView:
    """Tests for GET /api/invoices/ and POST /api/invoices/."""

    def test_list_invoices(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice(
        self,
        mock_history,
        admin_client,
        account_for_invoice,
        contact_for_invoice,
    ):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "New Invoice",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Invoice created successfully"
        assert "invoice" in data

    def test_create_invoice_unauthenticated(self, unauthenticated_client):
        """The request is refused, and refused as a response, not an exception.

        DRF catches ``PermissionDenied`` in ``handle_exception`` and renders it,
        so a test client never sees it raised. Wrapping the call in
        ``pytest.raises`` therefore failed with DID NOT RAISE no matter how the
        endpoint behaved, which meant this test could never have caught the
        access opening up.
        """
        response = unauthenticated_client.post(
            "/api/invoices/",
            {"invoice_title": "Unauthenticated Invoice"},
            format="json",
        )
        assert response.status_code == 403

    def test_org_isolation(self, org_b_client, invoice):
        """org_b_client should not see invoices belonging to org_a."""
        response = org_b_client.get("/api/invoices/")
        assert response.status_code == 200
        data = response.json()
        ids = [r["id"] for r in data["results"]]
        assert str(invoice.id) not in ids


# ---------------------------------------------------------------------------
# Invoice Detail / Update / Delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceDetailView:
    """Tests for GET/PUT/DELETE /api/invoices/<pk>/."""

    def test_get_detail(self, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/{invoice.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "invoice" in data
        assert data["invoice"]["id"] == str(invoice.id)

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_update_invoice(self, mock_history, admin_client, invoice):
        response = admin_client.put(
            f"/api/invoices/{invoice.id}/",
            {"invoice_title": "Updated Title"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Invoice updated successfully"

    def test_delete_invoice_not_supported(self, admin_client, invoice):
        """InvoiceDetailView does not implement DELETE; expect 405."""
        response = admin_client.delete(f"/api/invoices/{invoice.id}/")
        assert response.status_code == 405

    def test_cross_org(self, org_b_client, invoice):
        """org_b_client must not access an invoice belonging to org_a."""
        response = org_b_client.get(f"/api/invoices/{invoice.id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Invoice Actions (send, mark-paid, duplicate, cancel)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceActions:
    """Tests for invoice action endpoints."""

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_send_invoice(self, mock_send, admin_client, invoice):
        response = admin_client.post(f"/api/invoices/{invoice.id}/send/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Invoice sent successfully"
        invoice.refresh_from_db()
        assert invoice.status == "Sent"

    def test_mark_paid(self, admin_client, invoice_with_balance):
        response = admin_client.post(
            f"/api/invoices/{invoice_with_balance.id}/mark-paid/",
            {
                "amount": "500.00",
                "payment_method": "BANK_TRANSFER",
                "payment_date": "2026-01-15",
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Payment recorded successfully"
        assert "invoice" in data

    def test_duplicate_invoice(self, admin_client, invoice):
        response = admin_client.post(f"/api/invoices/{invoice.id}/duplicate/")
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Invoice duplicated successfully"
        assert "invoice" in data
        assert data["invoice"]["invoice_title"].startswith("Copy of")

    def test_cancel_invoice(self, admin_client, invoice):
        response = admin_client.post(f"/api/invoices/{invoice.id}/cancel/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Invoice cancelled successfully"
        invoice.refresh_from_db()
        assert invoice.status == "Cancelled"

    def test_cancel_already_cancelled(self, admin_client, invoice):
        invoice.status = "Cancelled"
        invoice.save()
        response = admin_client.post(f"/api/invoices/{invoice.id}/cancel/")
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    def test_cancel_paid_invoice(self, admin_client, invoice):
        invoice.status = "Paid"
        invoice.save()
        response = admin_client.post(f"/api/invoices/{invoice.id}/cancel/")
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True


# ---------------------------------------------------------------------------
# Invoice Line Items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceLineItems:
    """Tests for /api/invoices/<invoice_id>/line-items/."""

    def test_create_line_item(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/line-items/",
            {
                "name": "Consulting Hours",
                "quantity": "10.00",
                "unit_price": "150.00",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Line item added"
        assert "line_item" in data

    def test_list_line_items(self, admin_client, invoice):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Widget A",
            quantity=5,
            unit_price=20,
            org=invoice.org,
        )
        response = admin_client.get(f"/api/invoices/{invoice.id}/line-items/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1


# ---------------------------------------------------------------------------
# Invoice Payments
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoicePayments:
    """Tests for /api/invoices/<invoice_id>/payments/."""

    def test_create_payment(self, admin_client, invoice_with_balance):
        response = admin_client.post(
            f"/api/invoices/{invoice_with_balance.id}/payments/",
            {
                "amount": "250.00",
                "payment_date": "2026-02-01",
                "payment_method": "CREDIT_CARD",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Payment recorded"
        assert "payment" in data

    def test_list_payments(self, admin_client, invoice):
        Payment.objects.create(
            invoice=invoice,
            amount=100,
            payment_date="2026-01-10",
            payment_method="CASH",
            org=invoice.org,
        )
        response = admin_client.get(f"/api/invoices/{invoice.id}/payments/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1


@pytest.mark.django_db
class TestCancelledInvoiceTakesNoPayment:
    """A cancelled invoice is not owed, so no payment may be recorded on it.

    Before this rule both payment endpoints checked only the amount. A
    cancelled invoice keeps a non-zero ``amount_due``, so the amount passed,
    and ``Payment.save()`` -> ``update_invoice_payment()`` then wrote ``status``
    with no condition on it. The result was not just an accepted payment: the
    invoice moved out of Cancelled to Paid or Partially_Paid, undoing the
    cancellation. ``send`` and ``cancel`` both refuse that transition, so this
    was the one way round them.

    Both endpoints are covered because both reach the same serializer, and
    fixing only ``mark-paid`` would have left ``payments`` wide open.
    """

    @pytest.fixture
    def cancelled_invoice(self, invoice_with_balance):
        invoice_with_balance.status = "Cancelled"
        invoice_with_balance.save(update_fields=["status"])
        return invoice_with_balance

    def test_mark_paid_refuses_a_cancelled_invoice(
        self, admin_client, cancelled_invoice
    ):
        response = admin_client.post(
            f"/api/invoices/{cancelled_invoice.id}/mark-paid/",
            {"amount": "500.00", "payment_method": "CASH"},
            format="json",
        )

        assert response.status_code == 400
        assert "cancelled invoice" in str(response.json()["errors"])

    def test_payments_endpoint_refuses_a_cancelled_invoice(
        self, admin_client, cancelled_invoice
    ):
        response = admin_client.post(
            f"/api/invoices/{cancelled_invoice.id}/payments/",
            {
                "amount": "500.00",
                "payment_date": "2026-02-01",
                "payment_method": "CASH",
            },
            format="json",
        )

        assert response.status_code == 400
        assert "cancelled invoice" in str(response.json()["errors"])

    def test_a_refused_payment_leaves_the_invoice_cancelled(
        self, admin_client, cancelled_invoice
    ):
        admin_client.post(
            f"/api/invoices/{cancelled_invoice.id}/mark-paid/",
            {"amount": "500.00", "payment_method": "CASH"},
            format="json",
        )

        cancelled_invoice.refresh_from_db()
        # The point of the rule. Without it this read "Partially_Paid".
        assert cancelled_invoice.status == "Cancelled"
        assert cancelled_invoice.amount_paid == 0
        assert Payment.objects.filter(invoice=cancelled_invoice).count() == 0

    def test_the_same_payment_is_accepted_before_cancellation(
        self, admin_client, invoice_with_balance
    ):
        """The other branch: the rule has to be able to answer both ways.

        Identical request against the identical invoice, differing only in
        status, so a check that always refused would fail here.
        """
        response = admin_client.post(
            f"/api/invoices/{invoice_with_balance.id}/mark-paid/",
            {"amount": "500.00", "payment_method": "CASH"},
            format="json",
        )

        assert response.status_code == 200
        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.status == "Partially_Paid"

    def test_cancelling_after_a_payment_still_works(
        self, admin_client, invoice_with_balance
    ):
        """A part-paid invoice can still be cancelled.

        The new rule bites on the payment, not on the cancel, so the ordinary
        sequence (take a deposit, then cancel the job) is untouched.
        """
        admin_client.post(
            f"/api/invoices/{invoice_with_balance.id}/mark-paid/",
            {"amount": "500.00", "payment_method": "CASH"},
            format="json",
        )
        response = admin_client.post(f"/api/invoices/{invoice_with_balance.id}/cancel/")

        assert response.status_code == 200
        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.status == "Cancelled"
        # The money that did change hands is still recorded.
        assert invoice_with_balance.amount_paid == Decimal("500.00")


@pytest.mark.django_db
class TestPaymentTotalsDoNotResurrectACancelledInvoice:
    """The model-level half, for callers that never touch the serializer.

    ``update_invoice_payment`` runs from ``Payment.save()`` and
    ``Payment.delete()``, so a management command, a data fix or a deleted
    payment can reach it directly. It has to keep the totals right without
    moving a cancelled invoice's status.
    """

    def test_a_payment_created_directly_does_not_uncancel(self, invoice_with_balance):
        invoice_with_balance.status = "Cancelled"
        invoice_with_balance.save(update_fields=["status"])

        Payment.objects.create(
            invoice=invoice_with_balance,
            amount=Decimal("1000.00"),
            payment_date="2026-02-01",
            payment_method="CASH",
            org=invoice_with_balance.org,
        )

        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.status == "Cancelled"
        # Totals are still maintained: the guard skips the status, not the sums.
        assert invoice_with_balance.amount_paid == Decimal("1000.00")
        assert invoice_with_balance.amount_due == Decimal("0.00")

    def test_a_live_invoice_still_flips_to_paid(self, invoice_with_balance):
        """The other branch, so the guard is not simply always taken."""
        Payment.objects.create(
            invoice=invoice_with_balance,
            amount=Decimal("1000.00"),
            payment_date="2026-02-01",
            payment_method="CASH",
            org=invoice_with_balance.org,
        )

        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.status == "Paid"
        assert invoice_with_balance.paid_at is not None

    def test_deleting_a_payment_does_not_uncancel(self, invoice_with_balance):
        payment = Payment.objects.create(
            invoice=invoice_with_balance,
            amount=Decimal("400.00"),
            payment_date="2026-02-01",
            payment_method="CASH",
            org=invoice_with_balance.org,
        )
        invoice_with_balance.refresh_from_db()
        invoice_with_balance.status = "Cancelled"
        invoice_with_balance.save(update_fields=["status"])

        payment.delete()

        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.status == "Cancelled"
        assert invoice_with_balance.amount_paid == 0


# ---------------------------------------------------------------------------
# Invoice List Filters & Sorting
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceListFilters:
    """Tests for invoice list filtering, searching, and sorting."""

    def test_filter_by_status(self, admin_client, invoice):
        invoice.status = "Sent"
        invoice.save()
        response = admin_client.get("/api/invoices/?status=Sent")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        for r in data["results"]:
            assert r["status"] == "Sent"

    def test_filter_by_status_no_results(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/?status=Paid")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_search_by_title(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/?search=Test+Invoice")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_search_by_invoice_number(self, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/?search={invoice.invoice_number}")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_filter_by_account(self, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/?account={invoice.account_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_filter_by_date_range(self, admin_client, invoice):
        today = timezone.localdate().isoformat()
        response = admin_client.get(
            f"/api/invoices/?issue_date_gte={today}&issue_date_lte={today}"
        )
        assert response.status_code == 200

    def test_sort_by_created_at(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/?sort=created_at")
        assert response.status_code == 200

    def test_sort_by_due_date(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/?sort=-due_date")
        assert response.status_code == 200

    def test_sort_by_status(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/?sort=status")
        assert response.status_code == 200

    def test_invalid_sort_field_ignored(self, admin_client, invoice):
        """Invalid sort fields should be ignored (falls back to default)."""
        response = admin_client.get("/api/invoices/?sort=invalid_field")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Invoice Create - validation edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceCreateValidation:
    """Tests for invoice creation validation paths."""

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice_missing_account(
        self, mock_history, admin_client, contact_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "No Account Invoice",
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice_missing_contact(
        self, mock_history, admin_client, account_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "No Contact Invoice",
                "account_id": str(account_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice_invalid_account(
        self, mock_history, admin_client, contact_for_invoice
    ):
        fake_id = str(uuid.uuid4())
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Bad Account",
                "account_id": fake_id,
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 400

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice_cross_org_account(
        self, mock_history, admin_client, account_org_b, contact_for_invoice
    ):
        """Account from another org should fail validation."""
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Cross-org",
                "account_id": str(account_org_b.id),
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 400

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_create_invoice_with_line_items(
        self,
        mock_history,
        admin_client,
        account_for_invoice,
        contact_for_invoice,
    ):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Invoice With Items",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
                "line_items": [
                    {
                        "name": "Item 1",
                        "quantity": "2",
                        "unit_price": "100.00",
                    },
                    {
                        "name": "Item 2",
                        "quantity": "1",
                        "unit_price": "50.00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        inv_id = data["invoice"]["id"]
        assert InvoiceLineItem.objects.filter(invoice_id=inv_id).count() == 2

    @patch("invoices.api_views.create_invoice_history.delay")
    @patch("invoices.api_views.send_email.delay")
    def test_create_invoice_triggers_email_for_assigned_users(
        self,
        mock_email,
        mock_history,
        admin_client,
        account_for_invoice,
        contact_for_invoice,
        admin_profile,
    ):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Assigned Invoice",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 201
        # History is always called
        assert mock_history.called


@pytest.mark.django_db
class TestInvoiceCreateAuthorization:
    """The from-scratch invoice builder posts to InvoiceCreateSerializer. Three
    member-reachable holes are closed here and proven both ways: status is
    lifecycle-only (a new invoice is always Draft, never a fake "Paid"), and the
    template + line-item product FKs are org-scoped (no cross-tenant IDOR).
    account/contact/opportunity were already scoped and stay covered elsewhere.
    """

    def _payload(self, account, contact, **extra):
        payload = {
            "invoice_title": "Builder invoice",
            "account_id": str(account.id),
            "contact_id": str(contact.id),
            "currency": "USD",
        }
        payload.update(extra)
        return payload

    def test_status_is_forced_to_draft_on_create(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        # A client POSTing status="Paid" must NOT mint a paid invoice.
        response = admin_client.post(
            "/api/invoices/",
            self._payload(account_for_invoice, contact_for_invoice, status="Paid"),
            format="json",
        )
        assert response.status_code == 201
        invoice = Invoice.objects.get(id=response.json()["invoice"]["id"])
        assert invoice.status == "Draft"
        assert invoice.amount_paid == 0
        assert not invoice.payments.exists()

    def test_cross_org_template_is_rejected(
        self, admin_client, account_for_invoice, contact_for_invoice, org_b
    ):
        other = InvoiceTemplate.objects.create(name="Org B template", org=org_b)
        response = admin_client.post(
            "/api/invoices/",
            self._payload(
                account_for_invoice, contact_for_invoice, template_id=str(other.id)
            ),
            format="json",
        )
        assert response.status_code == 400
        assert "template_id" in response.json().get("errors", {})

    def test_own_org_template_is_accepted(
        self, admin_client, account_for_invoice, contact_for_invoice, template
    ):
        response = admin_client.post(
            "/api/invoices/",
            self._payload(
                account_for_invoice, contact_for_invoice, template_id=str(template.id)
            ),
            format="json",
        )
        assert response.status_code == 201
        invoice = Invoice.objects.get(id=response.json()["invoice"]["id"])
        assert invoice.template_id == template.id

    def test_cross_org_line_item_product_is_rejected(
        self, admin_client, account_for_invoice, contact_for_invoice, org_b
    ):
        other_product = Product.objects.create(
            name="Org B product", price=Decimal("5.00"), org=org_b
        )
        response = admin_client.post(
            "/api/invoices/",
            self._payload(
                account_for_invoice,
                contact_for_invoice,
                line_items=[
                    {
                        "name": "Item",
                        "quantity": 1,
                        "unit_price": "5.00",
                        "product": str(other_product.id),
                    }
                ],
            ),
            format="json",
        )
        assert response.status_code == 400
        assert "line_items" in response.json().get("errors", {})

    def test_own_org_line_item_product_is_accepted(
        self, admin_client, account_for_invoice, contact_for_invoice, product
    ):
        response = admin_client.post(
            "/api/invoices/",
            self._payload(
                account_for_invoice,
                contact_for_invoice,
                line_items=[
                    {
                        "name": "Item",
                        "quantity": 1,
                        "unit_price": "5.00",
                        "product": str(product.id),
                    }
                ],
            ),
            format="json",
        )
        assert response.status_code == 201


# ---------------------------------------------------------------------------
# Invoice Detail - permission edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceDetailPermissions:
    """Tests for invoice detail view permission checks."""

    def test_regular_user_cannot_see_unassigned_invoice(self, user_client, invoice):
        """Non-admin user who is neither creator nor assigned cannot see the invoice."""
        response = user_client.get(f"/api/invoices/{invoice.id}/")
        assert response.status_code == 403

    def test_get_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/{fake_id}/")
        assert response.status_code == 404

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_update_nonexistent_invoice(self, mock_history, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/{fake_id}/",
            {"invoice_title": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_regular_user_cannot_update_unassigned_invoice(
        self, mock_history, user_client, invoice
    ):
        response = user_client.put(
            f"/api/invoices/{invoice.id}/",
            {"invoice_title": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_detail_includes_attachments_comments_history(self, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/{invoice.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "attachments" in data
        assert "comments" in data
        assert "history" in data

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_update_invoice_with_line_items_replacement(
        self, mock_history, admin_client, invoice, line_item
    ):
        """PUT with line_items replaces all existing line items."""
        response = admin_client.put(
            f"/api/invoices/{invoice.id}/",
            {
                "line_items": [
                    {
                        "name": "Replacement Item",
                        "quantity": "1",
                        "unit_price": "999.00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == 200
        assert InvoiceLineItem.objects.filter(invoice=invoice).count() == 1
        assert InvoiceLineItem.objects.filter(
            invoice=invoice, name="Replacement Item"
        ).exists()


# ---------------------------------------------------------------------------
# Invoice Actions - additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceActionsEdgeCases:
    """Additional edge case tests for invoice action endpoints."""

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_send_already_sent_invoice(self, mock_send, admin_client, invoice):
        """Sending an already-sent invoice updates sent_at but keeps status."""
        invoice.status = "Sent"
        invoice.save()
        response = admin_client.post(f"/api/invoices/{invoice.id}/send/")
        assert response.status_code == 200
        invoice.refresh_from_db()
        assert invoice.status == "Sent"
        assert invoice.is_email_sent is True

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_send_nonexistent_invoice(self, mock_send, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/{fake_id}/send/")
        assert response.status_code == 404

    def test_mark_paid_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(
            f"/api/invoices/{fake_id}/mark-paid/",
            {
                "amount": "100.00",
                "payment_method": "CASH",
                "payment_date": "2026-01-01",
            },
            format="json",
        )
        assert response.status_code == 404

    def test_duplicate_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/{fake_id}/duplicate/")
        assert response.status_code == 404

    def test_cancel_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/{fake_id}/cancel/")
        assert response.status_code == 404

    def test_cancel_invoice_permission_denied_for_regular_user(
        self, user_client, invoice
    ):
        response = user_client.post(f"/api/invoices/{invoice.id}/cancel/")
        assert response.status_code == 403

    def test_duplicate_invoice_with_line_items(self, admin_client, invoice, line_item):
        """Duplicate should copy line items."""
        response = admin_client.post(f"/api/invoices/{invoice.id}/duplicate/")
        assert response.status_code == 201
        new_id = response.json()["invoice"]["id"]
        assert InvoiceLineItem.objects.filter(invoice_id=new_id).count() == 1

    def test_mark_paid_with_defaults(self, admin_client, invoice_with_balance):
        """Mark paid with no explicit amount settles the full amount_due."""
        response = admin_client.post(
            f"/api/invoices/{invoice_with_balance.id}/mark-paid/",
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        invoice_with_balance.refresh_from_db()
        assert invoice_with_balance.amount_paid == Decimal("1000.00")
        assert invoice_with_balance.status == "Paid"


# ---------------------------------------------------------------------------
# Invoice PDF
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoicePDF:
    """Tests for /api/invoices/<pk>/pdf/."""

    @patch("invoices.api_views.generate_invoice_pdf")
    @patch("invoices.api_views.generate_invoice_filename")
    def test_pdf_success(self, mock_filename, mock_pdf, admin_client, invoice):
        mock_pdf.return_value = b"%PDF-1.4 test content"
        mock_filename.return_value = "invoice.pdf"
        response = admin_client.get(f"/api/invoices/{invoice.id}/pdf/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert 'filename="invoice.pdf"' in response["Content-Disposition"]

    @patch(
        "invoices.api_views.generate_invoice_pdf",
        side_effect=ImportError("weasyprint not installed"),
    )
    def test_pdf_import_error(self, mock_pdf, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/{invoice.id}/pdf/")
        assert response.status_code == 503

    @patch(
        "invoices.api_views.generate_invoice_pdf",
        side_effect=RuntimeError("unexpected"),
    )
    def test_pdf_generic_error(self, mock_pdf, admin_client, invoice):
        response = admin_client.get(f"/api/invoices/{invoice.id}/pdf/")
        assert response.status_code == 500

    def test_pdf_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/{fake_id}/pdf/")
        assert response.status_code == 404

    @patch("invoices.api_views.generate_invoice_pdf")
    @patch("invoices.api_views.generate_invoice_filename")
    def test_pdf_permission_denied_for_regular_user(
        self, mock_filename, mock_pdf, user_client, invoice
    ):
        response = user_client.get(f"/api/invoices/{invoice.id}/pdf/")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Line Item Detail (update, delete)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLineItemDetail:
    """Tests for PUT/DELETE /api/invoices/<invoice_id>/line-items/<pk>/."""

    def test_update_line_item(self, admin_client, invoice, line_item):
        response = admin_client.put(
            f"/api/invoices/{invoice.id}/line-items/{line_item.id}/",
            {"name": "Updated Widget", "quantity": "10", "unit_price": "25.00"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Line item updated"
        line_item.refresh_from_db()
        assert line_item.name == "Updated Widget"

    def test_update_line_item_partial(self, admin_client, invoice, line_item):
        response = admin_client.put(
            f"/api/invoices/{invoice.id}/line-items/{line_item.id}/",
            {"quantity": "20"},
            format="json",
        )
        assert response.status_code == 200

    def test_delete_line_item(self, admin_client, invoice, line_item):
        response = admin_client.delete(
            f"/api/invoices/{invoice.id}/line-items/{line_item.id}/"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Line item deleted"
        assert not InvoiceLineItem.objects.filter(id=line_item.id).exists()

    def test_update_nonexistent_line_item(self, admin_client, invoice):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/{invoice.id}/line-items/{fake_id}/",
            {"name": "Ghost"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_nonexistent_line_item(self, admin_client, invoice):
        fake_id = uuid.uuid4()
        response = admin_client.delete(
            f"/api/invoices/{invoice.id}/line-items/{fake_id}/"
        )
        assert response.status_code == 404

    def test_create_line_item_invalid_data(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/line-items/",
            {"quantity": "not_a_number"},
            format="json",
        )
        assert response.status_code == 400

    def test_line_item_on_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/{fake_id}/line-items/")
        assert response.status_code == 404

    def test_create_line_item_on_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(
            f"/api/invoices/{fake_id}/line-items/",
            {"name": "Item", "quantity": "1", "unit_price": "10.00"},
            format="json",
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Payment Detail (delete)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPaymentDetail:
    """Tests for DELETE /api/invoices/<invoice_id>/payments/<pk>/."""

    def test_delete_payment(self, admin_client, invoice, payment):
        response = admin_client.delete(
            f"/api/invoices/{invoice.id}/payments/{payment.id}/"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Payment deleted"
        assert not Payment.objects.filter(id=payment.id).exists()

    def test_delete_payment_nonexistent(self, admin_client, invoice):
        fake_id = uuid.uuid4()
        response = admin_client.delete(
            f"/api/invoices/{invoice.id}/payments/{fake_id}/"
        )
        assert response.status_code == 404

    def test_delete_payment_permission_denied(self, user_client, invoice, payment):
        response = user_client.delete(
            f"/api/invoices/{invoice.id}/payments/{payment.id}/"
        )
        assert response.status_code == 403

    def test_create_payment_invalid_data(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/payments/",
            {"amount": "not_a_number"},
            format="json",
        )
        assert response.status_code == 400

    def test_create_payment_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(
            f"/api/invoices/{fake_id}/payments/",
            {
                "amount": "100.00",
                "payment_date": "2026-01-01",
                "payment_method": "CASH",
            },
            format="json",
        )
        assert response.status_code == 404

    def test_list_payments_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/{fake_id}/payments/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Products CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProductListView:
    """Tests for GET/POST /api/invoices/products/."""

    def test_list_products(self, admin_client, product):
        response = admin_client.get("/api/invoices/products/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    def test_list_products_filter_active(self, admin_client, product, product_inactive):
        response = admin_client.get("/api/invoices/products/?is_active=true")
        assert response.status_code == 200
        data = response.json()
        for r in data["results"]:
            assert r["is_active"] is True

    def test_list_products_filter_inactive(
        self, admin_client, product, product_inactive
    ):
        response = admin_client.get("/api/invoices/products/?is_active=false")
        assert response.status_code == 200
        data = response.json()
        for r in data["results"]:
            assert r["is_active"] is False

    def test_list_products_search(self, admin_client, product):
        response = admin_client.get("/api/invoices/products/?search=Test")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_list_products_search_by_sku(self, admin_client, product):
        response = admin_client.get("/api/invoices/products/?search=TP-001")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_list_products_filter_category(self, admin_client, product):
        response = admin_client.get("/api/invoices/products/?category=Software")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_create_product(self, admin_client):
        response = admin_client.post(
            "/api/invoices/products/",
            {
                "name": "New Product",
                "description": "A new product",
                "sku": "NP-001",
                "price": "199.99",
                "category": "Hardware",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["product"]["name"] == "New Product"

    def test_create_product_invalid(self, admin_client):
        """Missing required name field."""
        response = admin_client.post(
            "/api/invoices/products/",
            {"description": "No name"},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestProductDetailView:
    """Tests for GET/PUT/DELETE /api/invoices/products/<pk>/."""

    def test_get_product(self, admin_client, product):
        response = admin_client.get(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Product"

    def test_get_nonexistent_product(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/products/{fake_id}/")
        assert response.status_code == 404

    def test_update_product(self, admin_client, product):
        response = admin_client.put(
            f"/api/invoices/products/{product.id}/",
            {"name": "Updated Product", "price": "149.99"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["product"]["name"] == "Updated Product"

    def test_update_nonexistent_product(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/products/{fake_id}/",
            {"name": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_product(self, admin_client, product):
        response = admin_client.delete(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert not Product.objects.filter(id=product.id).exists()

    def test_delete_nonexistent_product(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/products/{fake_id}/")
        assert response.status_code == 404

    def test_org_isolation(self, org_b_client, product):
        """org_b_client should not see products from org_a."""
        response = org_b_client.get(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestProductWriteAuthorization:
    """The product catalogue is org-wide config: its list prices flow onto every
    rep's invoices and estimates. Reading it is open to any member (they build
    line items from it), but creating/editing/deleting is admin-only. See
    ProductListView.post and ProductDetailView._require_admin. Both directions
    are proven here: a non-admin is refused, an admin is allowed.
    """

    def test_non_admin_can_list_products(self, user_client, product):
        response = user_client.get("/api/invoices/products/")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_non_admin_can_read_product(self, user_client, product):
        response = user_client.get(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 200
        assert response.json()["name"] == "Test Product"

    def test_non_admin_cannot_create_product(self, user_client):
        response = user_client.post(
            "/api/invoices/products/",
            {"name": "Sneaky", "price": "10.00"},
            format="json",
        )
        assert response.status_code == 403
        assert not Product.objects.filter(name="Sneaky").exists()

    def test_non_admin_cannot_update_product(self, user_client, product):
        response = user_client.put(
            f"/api/invoices/products/{product.id}/",
            {"name": "Repriced", "price": "1.00"},
            format="json",
        )
        assert response.status_code == 403
        product.refresh_from_db()
        assert product.name == "Test Product"
        assert product.price == Decimal("99.99")

    def test_non_admin_cannot_delete_product(self, user_client, product):
        response = user_client.delete(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 403
        assert Product.objects.filter(id=product.id).exists()

    def test_admin_can_create_update_delete(self, admin_client):
        created = admin_client.post(
            "/api/invoices/products/",
            {"name": "Adminmade", "price": "5.00"},
            format="json",
        )
        assert created.status_code == 201
        pid = created.json()["product"]["id"]

        updated = admin_client.put(
            f"/api/invoices/products/{pid}/",
            {"price": "6.00"},
            format="json",
        )
        assert updated.status_code == 200

        deleted = admin_client.delete(f"/api/invoices/products/{pid}/")
        assert deleted.status_code == 200
        assert not Product.objects.filter(id=pid).exists()


@pytest.mark.django_db
class TestProductUsedOn:
    """`used_on` reports the number of DISTINCT invoices a product is a line item
    on, annotated across the list, computed on the single detail. Two line items
    on one invoice count once; it is why a retired product stays worth listing.
    """

    def test_used_on_zero_when_never_invoiced(self, admin_client, product):
        response = admin_client.get(f"/api/invoices/products/{product.id}/")
        assert response.status_code == 200
        assert response.json()["used_on"] == 0

    def test_used_on_counts_distinct_invoices(
        self, admin_client, product, account_for_invoice, org_a
    ):
        inv1 = Invoice.objects.create(
            invoice_title="Inv1", account=account_for_invoice, currency="USD", org=org_a
        )
        # Two line items on the SAME invoice must not double-count.
        InvoiceLineItem.objects.create(
            invoice=inv1, product=product, name="a", org=org_a
        )
        InvoiceLineItem.objects.create(
            invoice=inv1, product=product, name="b", org=org_a
        )
        inv2 = Invoice.objects.create(
            invoice_title="Inv2", account=account_for_invoice, currency="USD", org=org_a
        )
        InvoiceLineItem.objects.create(
            invoice=inv2, product=product, name="c", org=org_a
        )

        # Detail path: the serializer's fallback query.
        detail = admin_client.get(f"/api/invoices/products/{product.id}/")
        assert detail.json()["used_on"] == 2

        # List path: the annotation.
        listed = admin_client.get("/api/invoices/products/")
        row = next(r for r in listed.json()["results"] if r["id"] == str(product.id))
        assert row["used_on"] == 2


# ---------------------------------------------------------------------------
# Estimates CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateListView:
    """Tests for GET/POST /api/invoices/estimates/."""

    def test_list_estimates(self, admin_client, estimate):
        response = admin_client.get("/api/invoices/estimates/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    def test_list_estimates_filter_status(self, admin_client, estimate):
        response = admin_client.get("/api/invoices/estimates/?status=Draft")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_list_estimates_filter_account(self, admin_client, estimate):
        response = admin_client.get(
            f"/api/invoices/estimates/?account={estimate.account_id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_list_estimates_search(self, admin_client, estimate):
        response = admin_client.get("/api/invoices/estimates/?search=Test")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

    def test_create_estimate(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/estimates/",
            {
                "title": "New Estimate",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "client_name": "New Client",
                "client_email": "new@example.com",
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["estimate"]["title"] == "New Estimate"

    def test_create_estimate_with_line_items(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/estimates/",
            {
                "title": "Estimate With Items",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "client_name": "New Client",
                "client_email": "new@example.com",
                "currency": "USD",
                "tax_rate": "10.00",
                "line_items": [
                    {
                        "name": "Implementation",
                        "quantity": "2.00",
                        "unit_price": "100.00",
                        "order": 0,
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        created = Estimate.objects.get(id=data["estimate"]["id"])
        assert created.line_items.count() == 1
        assert created.subtotal == Decimal("200.00")
        assert created.total_amount == Decimal("220.00")

    def test_create_estimate_invalid_account(self, admin_client, contact_for_invoice):
        fake_id = str(uuid.uuid4())
        response = admin_client.post(
            "/api/invoices/estimates/",
            {
                "title": "Bad Estimate",
                "account_id": fake_id,
                "contact_id": str(contact_for_invoice.id),
                "client_name": "Client",
                "client_email": "c@example.com",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_create_estimate_missing_fields(self, admin_client):
        response = admin_client.post(
            "/api/invoices/estimates/",
            {"title": "Incomplete"},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestEstimateListRowShape:
    """The list row must carry enough to run the worklist.

    The estimates page separates "accepted but not yet billed" from "already
    billed"; that decision is ``status == 'Accepted' and not
    converted_to_invoice``. If the list serializer omits the conversion
    reference the page cannot tell the two apart and offers to raise a *second*
    invoice for an estimate that already has one -- a duplicate. These pin the
    two fields the row needs.
    """

    def _row(self, client, estimate_id):
        response = client.get("/api/invoices/estimates/")
        assert response.status_code == 200
        rows = response.data["results"]
        return next(r for r in rows if r["id"] == str(estimate_id))

    def test_unconverted_estimate_has_null_conversion(self, admin_client, estimate):
        row = self._row(admin_client, estimate.id)
        # Present (so the client can rely on it) and null (nothing billed yet).
        assert "converted_to_invoice" in row
        assert row["converted_to_invoice"] is None

    def test_converted_estimate_exposes_its_invoice(self, admin_client, estimate):
        convert = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")
        assert convert.status_code == 201
        row = self._row(admin_client, estimate.id)
        assert row["converted_to_invoice"] is not None
        assert row["converted_to_invoice"]["id"]
        assert row["converted_to_invoice"]["invoice_number"]

    def test_opportunity_absent_serializes_as_null(self, admin_client, estimate):
        # The base estimate has no opportunity; the field must still be present
        # and null rather than missing, so the page can read e.opportunity.
        row = self._row(admin_client, estimate.id)
        assert "opportunity" in row
        assert row["opportunity"] is None

    def test_opportunity_present_serializes_id_and_name(
        self, admin_client, estimate, org_a, account_for_invoice
    ):
        from opportunity.models import Opportunity

        opp = Opportunity.objects.create(
            name="Renewal, 40 seats",
            account=account_for_invoice,
            org=org_a,
        )
        estimate.opportunity = opp
        estimate.save(update_fields=["opportunity"])
        row = self._row(admin_client, estimate.id)
        assert row["opportunity"] == {"id": str(opp.id), "name": "Renewal, 40 seats"}


@pytest.mark.django_db
class TestEstimateDetailView:
    """Tests for GET/PUT/DELETE /api/invoices/estimates/<pk>/."""

    def test_get_estimate(self, admin_client, estimate):
        response = admin_client.get(f"/api/invoices/estimates/{estimate.id}/")
        assert response.status_code == 200
        data = response.json()
        assert "estimate" in data
        assert data["estimate"]["title"] == "Test Estimate"

    def test_get_nonexistent_estimate(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/estimates/{fake_id}/")
        assert response.status_code == 404

    def test_update_estimate(self, admin_client, estimate):
        response = admin_client.put(
            f"/api/invoices/estimates/{estimate.id}/",
            {"title": "Updated Estimate"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["estimate"]["title"] == "Updated Estimate"

    def test_update_nonexistent_estimate(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/estimates/{fake_id}/",
            {"title": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_estimate(self, admin_client, estimate):
        response = admin_client.delete(f"/api/invoices/estimates/{estimate.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert not Estimate.objects.filter(id=estimate.id).exists()

    def test_delete_nonexistent_estimate(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/estimates/{fake_id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Estimate Actions (convert, send, pdf)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateActions:
    """Tests for estimate action endpoints."""

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_convert_estimate_to_invoice(self, mock_history, admin_client, estimate):
        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Estimate converted to invoice"
        assert "invoice" in data
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"
        assert estimate.converted_to_invoice is not None

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_convert_already_converted_estimate(
        self, mock_history, admin_client, estimate, invoice
    ):
        estimate.converted_to_invoice = invoice
        estimate.save()
        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True

    def test_convert_nonexistent_estimate(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/estimates/{fake_id}/convert/")
        assert response.status_code == 404

    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_send_estimate(self, mock_send, admin_client, estimate):
        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        estimate.refresh_from_db()
        assert estimate.status == "Sent"
        assert estimate.sent_at is not None

    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_send_already_sent_estimate(self, mock_send, admin_client, estimate):
        estimate.status = "Sent"
        estimate.save()
        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")
        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.status == "Sent"

    def test_send_nonexistent_estimate(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/estimates/{fake_id}/send/")
        assert response.status_code == 404

    # ------------------------------------------------------------------
    # A settled or lapsed quote is not sendable.
    #
    # `EstimateSendView` had no status check at all, while `InvoiceSendView`
    # refused Paid and Cancelled with a comment explaining why. Re-sending
    # mailed the client a quote they had already declined and overwrote
    # `sent_at`, losing the record of when it first went out. The accept
    # endpoint was already guarded, so this never let a lapsed quote be
    # accepted; it was the send half that was missing.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("state", ["Accepted", "Declined", "Expired"])
    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_a_settled_estimate_cannot_be_sent(
        self, mock_send, admin_client, estimate, state
    ):
        estimate.status = state
        estimate.save()

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")

        assert response.status_code == 400
        assert state.lower() in response.json()["message"]
        # No mail, and the timestamp of the real send is untouched.
        mock_send.assert_not_called()
        estimate.refresh_from_db()
        assert estimate.sent_at is None

    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_a_lapsed_estimate_cannot_be_sent_before_the_task_relabels_it(
        self, mock_send, admin_client, estimate
    ):
        """The gap between the expiry date passing and the daily task running.

        `check_expired_estimates` flips Sent to Expired once a day, so an
        estimate reads as "Sent" while `is_expired` is already true. A guard
        that only tested the status would send a lapsed quote on any day the
        task had not run yet.
        """
        estimate.status = "Sent"
        estimate.expiry_date = timezone.localdate() - datetime.timedelta(days=1)
        estimate.save()

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")

        assert response.status_code == 400
        assert "expired on" in response.json()["message"]
        mock_send.assert_not_called()

    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_an_estimate_expiring_today_can_still_be_sent(
        self, mock_send, admin_client, estimate
    ):
        """The boundary, and the other branch of the expiry check.

        `is_expired` is `today > expiry_date`, so the last day is inclusive.
        A check written with `>=` would refuse a quote on the very day it is
        still valid, which is the day it most needs sending.
        """
        estimate.status = "Sent"
        estimate.expiry_date = timezone.localdate()
        estimate.save()

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")

        assert response.status_code == 200
        mock_send.assert_called_once()

    @pytest.mark.parametrize("state", ["Draft", "Sent", "Viewed"])
    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_a_live_estimate_still_sends(
        self, mock_send, admin_client, estimate, state
    ):
        """The other branch of the status check, so it is not always-refuse."""
        estimate.status = state
        estimate.save()

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")

        assert response.status_code == 200
        assert response.json()["error"] is False
        mock_send.assert_called_once()
        estimate.refresh_from_db()
        # Draft is promoted; Sent and Viewed keep the status they had.
        assert estimate.status == ("Sent" if state == "Draft" else state)

    @patch("invoices.api_views.generate_estimate_pdf")
    @patch("invoices.api_views.generate_estimate_filename")
    def test_estimate_pdf_success(
        self, mock_filename, mock_pdf, admin_client, estimate
    ):
        mock_pdf.return_value = b"%PDF-1.4 estimate"
        mock_filename.return_value = "estimate.pdf"
        response = admin_client.get(f"/api/invoices/estimates/{estimate.id}/pdf/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_estimate_pdf_nonexistent(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/estimates/{fake_id}/pdf/")
        assert response.status_code == 404

    @patch("invoices.api_views.generate_estimate_pdf")
    @patch("invoices.api_views.generate_estimate_filename")
    def test_estimate_pdf_permission_denied(
        self, mock_filename, mock_pdf, user_client, estimate
    ):
        response = user_client.get(f"/api/invoices/estimates/{estimate.id}/pdf/")
        assert response.status_code == 403

    @patch(
        "invoices.api_views.generate_estimate_pdf",
        side_effect=ImportError("no weasyprint"),
    )
    def test_estimate_pdf_import_error(self, mock_pdf, admin_client, estimate):
        response = admin_client.get(f"/api/invoices/estimates/{estimate.id}/pdf/")
        assert response.status_code == 503

    @patch(
        "invoices.api_views.generate_estimate_pdf",
        side_effect=RuntimeError("unexpected"),
    )
    def test_estimate_pdf_generic_error(self, mock_pdf, admin_client, estimate):
        response = admin_client.get(f"/api/invoices/estimates/{estimate.id}/pdf/")
        assert response.status_code == 500

    @patch("invoices.api_views.create_invoice_history.delay")
    def test_convert_estimate_with_line_items(
        self, mock_history, admin_client, estimate
    ):
        """Converting estimate with line items copies them to the new invoice."""
        EstimateLineItem.objects.create(
            estimate=estimate,
            name="Service A",
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            org=estimate.org,
        )
        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")
        assert response.status_code == 201
        inv_id = response.json()["invoice"]["id"]
        assert InvoiceLineItem.objects.filter(invoice_id=inv_id).count() == 1


@pytest.mark.django_db
class TestEstimateAuthorization:
    """Object-level authorization on the estimate endpoints.

    The estimate views used to carry only an org filter -- no object check at
    all on detail/update/delete/convert/send -- so any member could act on any
    estimate in the org, while the *list* crashed for non-admins. These pin the
    fix and every one of them fails against the pre-fix code:

    * the list case 500s (``Q(created_by=<Profile>)`` raises ``ValueError``);
    * the "member is blocked" cases returned 200 and mutated the row;
    * the creator-PDF case returned 403 (``request.profile == created_by`` is a
      Profile-vs-User comparison, always False).
    """

    # -- E1: the list no longer 500s for a non-admin --------------------------

    def test_member_can_list_estimates(self, user_client, estimate):
        response = user_client.get("/api/invoices/estimates/")
        assert response.status_code == 200

    # -- E2/E3: a member who is neither creator nor assignee is blocked -------

    def test_member_not_owner_cannot_get(self, user_client, estimate):
        response = user_client.get(f"/api/invoices/estimates/{estimate.id}/")
        assert response.status_code == 403

    def test_member_not_owner_cannot_update(self, user_client, estimate):
        response = user_client.put(
            f"/api/invoices/estimates/{estimate.id}/",
            {"title": "Hijacked"},
            format="json",
        )
        assert response.status_code == 403
        estimate.refresh_from_db()
        assert estimate.title == "Test Estimate"

    def test_member_not_owner_cannot_delete(self, user_client, estimate):
        response = user_client.delete(f"/api/invoices/estimates/{estimate.id}/")
        assert response.status_code == 403
        assert Estimate.objects.filter(id=estimate.id).exists()

    def test_member_not_owner_cannot_convert(self, user_client, estimate):
        response = user_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")
        assert response.status_code == 403
        estimate.refresh_from_db()
        assert estimate.converted_to_invoice is None

    @patch("invoices.tasks.send_estimate_to_client.delay")
    def test_member_not_owner_cannot_send(self, mock_send, user_client, estimate):
        # send_estimate_to_client is imported inside the view, so patch it at
        # its source. The 403 short-circuits before the import is even reached.
        response = user_client.post(f"/api/invoices/estimates/{estimate.id}/send/")
        assert response.status_code == 403
        mock_send.assert_not_called()

    # -- the positive direction: assignee and creator do have access ----------

    def test_assignee_can_get_and_update(self, user_client, user_profile, estimate):
        estimate.assigned_to.set([user_profile])
        assert (
            user_client.get(f"/api/invoices/estimates/{estimate.id}/").status_code
            == 200
        )
        response = user_client.put(
            f"/api/invoices/estimates/{estimate.id}/",
            {"title": "Updated by assignee"},
            format="json",
        )
        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.title == "Updated by assignee"

    def test_creator_can_get(self, user_client, regular_user, estimate):
        # .update() rather than assignment: created_by is stamped by
        # BaseModel.save() from the request thread, which the fixture had none.
        Estimate.objects.filter(id=estimate.id).update(created_by=regular_user)
        response = user_client.get(f"/api/invoices/estimates/{estimate.id}/")
        assert response.status_code == 200

    @patch("invoices.api_views.generate_estimate_filename", return_value="e.pdf")
    @patch("invoices.api_views.generate_estimate_pdf", return_value=b"%PDF-1.4")
    def test_creator_can_get_pdf(
        self, mock_pdf, mock_name, user_client, regular_user, estimate
    ):
        # E4: the creator was denied their own estimate's PDF because the check
        # compared a Profile to a User. They are neither admin nor assignee here.
        Estimate.objects.filter(id=estimate.id).update(created_by=regular_user)
        response = user_client.get(f"/api/invoices/estimates/{estimate.id}/pdf/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    # -- admin keeps blanket access, and other orgs get 404 not 403 -----------

    def test_admin_can_access_any_estimate(self, admin_client, estimate):
        assert (
            admin_client.get(f"/api/invoices/estimates/{estimate.id}/").status_code
            == 200
        )

    def test_other_org_estimate_is_404_not_403(self, user_client, estimate_org_b):
        # A member of org A must not even learn that org B's estimate exists.
        response = user_client.get(f"/api/invoices/estimates/{estimate_org_b.id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Recurring Invoices CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecurringInvoiceListView:
    """Tests for GET/POST /api/invoices/recurring/."""

    def test_list_recurring(self, admin_client, recurring_invoice):
        response = admin_client.get("/api/invoices/recurring/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    def test_list_recurring_filter_active(self, admin_client, recurring_invoice):
        response = admin_client.get("/api/invoices/recurring/?is_active=true")
        assert response.status_code == 200
        data = response.json()
        for r in data["results"]:
            assert r["is_active"] is True

    def test_list_recurring_filter_inactive(self, admin_client, recurring_invoice):
        recurring_invoice.is_active = False
        recurring_invoice.save()
        response = admin_client.get("/api/invoices/recurring/?is_active=false")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        for r in data["results"]:
            assert r["is_active"] is False

    def test_create_recurring(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "New Recurring",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "client_name": "Client",
                "client_email": "client@example.com",
                "frequency": "WEEKLY",
                "start_date": "2026-03-01",
                "next_generation_date": "2026-03-01",
                "currency": "USD",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["recurring_invoice"]["title"] == "New Recurring"

    # ------------------------------------------------------------------
    # A CUSTOM cadence needs its interval.
    #
    # `calculate_next_date` tests `frequency == "CUSTOM" and self.custom_days`
    # and otherwise falls through to a monthly step. So a CUSTOM schedule
    # saved without an interval was accepted and then billed monthly forever,
    # while the list kept describing it as "Custom". The client asked for one
    # thing and the server quietly did another, which is why this is a 400 and
    # not a default.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("interval", [None, 0])
    def test_a_custom_cadence_without_an_interval_is_refused(
        self, admin_client, account_for_invoice, contact_for_invoice, interval
    ):
        payload = {
            "title": "Custom cadence",
            "account_id": str(account_for_invoice.id),
            "contact_id": str(contact_for_invoice.id),
            "frequency": "CUSTOM",
            "start_date": "2026-03-01",
            "next_generation_date": "2026-03-01",
            "currency": "USD",
        }
        if interval is not None:
            # Zero is falsy in `calculate_next_date`, so it behaved exactly
            # like a missing interval and has to be refused the same way.
            payload["custom_days"] = interval

        response = admin_client.post("/api/invoices/recurring/", payload, format="json")

        assert response.status_code == 400
        assert "custom_days" in response.json()["errors"]
        assert RecurringInvoice.objects.filter(title="Custom cadence").count() == 0

    def test_a_custom_cadence_with_an_interval_is_accepted(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        """The other branch, so the rule is not simply always-refuse."""
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "Every ten days",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "frequency": "CUSTOM",
                "custom_days": 10,
                "start_date": "2026-03-01",
                "next_generation_date": "2026-03-01",
                "currency": "USD",
            },
            format="json",
        )

        assert response.status_code == 201
        created = RecurringInvoice.objects.get(title="Every ten days")
        assert created.custom_days == 10
        # The interval is honoured rather than silently falling back to a month.
        assert created.calculate_next_date() == datetime.date(2026, 3, 11)

    def test_a_non_custom_cadence_needs_no_interval(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        """The rule must not leak onto the other six frequencies."""
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "Quarterly",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "frequency": "QUARTERLY",
                "start_date": "2026-03-01",
                "next_generation_date": "2026-03-01",
                "currency": "USD",
            },
            format="json",
        )

        assert response.status_code == 201

    def test_switching_an_existing_schedule_to_custom_needs_an_interval(
        self, admin_client, recurring_invoice
    ):
        """The PUT path, which reads `frequency` from the request.

        Sending only `frequency` here is the natural way to change a cadence,
        and the guard has to fire on it rather than only on create.
        """
        response = admin_client.put(
            f"/api/invoices/recurring/{recurring_invoice.id}/",
            {"frequency": "CUSTOM"},
            format="json",
        )

        assert response.status_code == 400
        assert "custom_days" in response.json()["errors"]
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.frequency != "CUSTOM"

    def test_editing_a_custom_schedule_without_resending_frequency_is_fine(
        self, admin_client, recurring_invoice
    ):
        """A partial update must read the STORED frequency, not an absent one.

        Without the `partial` branch this passes for the wrong reason: absent
        `frequency` reads as not-CUSTOM and the rule never fires. With a stored
        CUSTOM schedule that already has an interval, the edit has to succeed.
        """
        recurring_invoice.frequency = "CUSTOM"
        recurring_invoice.custom_days = 10
        recurring_invoice.save()

        response = admin_client.put(
            f"/api/invoices/recurring/{recurring_invoice.id}/",
            {"title": "Renamed"},
            format="json",
        )

        assert response.status_code == 200
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.title == "Renamed"
        assert recurring_invoice.custom_days == 10

    def test_create_recurring_with_line_items(
        self, admin_client, account_for_invoice, contact_for_invoice
    ):
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "Recurring With Items",
                "account_id": str(account_for_invoice.id),
                "contact_id": str(contact_for_invoice.id),
                "client_name": "Client",
                "client_email": "client@example.com",
                "frequency": "WEEKLY",
                "start_date": "2026-03-01",
                "next_generation_date": "2026-03-01",
                "currency": "USD",
                "tax_rate": "10.00",
                "line_items": [
                    {
                        "name": "Hosting",
                        "quantity": "2.00",
                        "unit_price": "50.00",
                        "order": 0,
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        created = RecurringInvoice.objects.get(id=data["recurring_invoice"]["id"])
        assert created.line_items.count() == 1
        assert created.subtotal == Decimal("100.00")
        assert created.total_amount == Decimal("110.00")

    def test_create_recurring_invalid_account(self, admin_client, contact_for_invoice):
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "Bad Recurring",
                "account_id": str(uuid.uuid4()),
                "contact_id": str(contact_for_invoice.id),
                "client_name": "Client",
                "client_email": "c@e.com",
                "frequency": "MONTHLY",
                "start_date": "2026-03-01",
                "next_generation_date": "2026-03-01",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_create_recurring_missing_fields(self, admin_client):
        response = admin_client.post(
            "/api/invoices/recurring/",
            {"title": "Incomplete"},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestRecurringInvoiceDetailView:
    """Tests for GET/PUT/DELETE /api/invoices/recurring/<pk>/."""

    def test_get_recurring(self, admin_client, recurring_invoice):
        response = admin_client.get(f"/api/invoices/recurring/{recurring_invoice.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["recurring_invoice"]["title"] == "Monthly Hosting"

    def test_get_nonexistent_recurring(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/recurring/{fake_id}/")
        assert response.status_code == 404

    def test_update_recurring(self, admin_client, recurring_invoice):
        response = admin_client.put(
            f"/api/invoices/recurring/{recurring_invoice.id}/",
            {"title": "Updated Recurring"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["recurring_invoice"]["title"] == "Updated Recurring"

    def test_update_nonexistent_recurring(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/recurring/{fake_id}/",
            {"title": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_recurring(self, admin_client, recurring_invoice):
        response = admin_client.delete(
            f"/api/invoices/recurring/{recurring_invoice.id}/"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert not RecurringInvoice.objects.filter(id=recurring_invoice.id).exists()

    def test_delete_nonexistent_recurring(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/recurring/{fake_id}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestRecurringInvoicePauseToggle:
    """Tests for POST /api/invoices/recurring/<pk>/toggle/."""

    def test_pause_recurring(self, admin_client, recurring_invoice):
        assert recurring_invoice.is_active is True
        response = admin_client.post(
            f"/api/invoices/recurring/{recurring_invoice.id}/toggle/"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert "paused" in data["message"]
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.is_active is False

    def test_resume_recurring(self, admin_client, recurring_invoice):
        recurring_invoice.is_active = False
        recurring_invoice.save()
        response = admin_client.post(
            f"/api/invoices/recurring/{recurring_invoice.id}/toggle/"
        )
        assert response.status_code == 200
        data = response.json()
        assert "resumed" in data["message"]
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.is_active is True

    def test_toggle_nonexistent(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/recurring/{fake_id}/toggle/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestRecurringInvoiceAuthorization:
    """Object-level authorization on the recurring-invoice endpoints.

    Every recurring view used to filter on ``org`` only -- no object check on
    detail/update/delete/toggle, and no non-admin scoping on the list -- so any
    member could read, edit, delete or pause/resume *every* schedule in the org.
    A schedule is an owned billing record (``AssignableMixin`` + ``created_by``),
    the same shape as Invoice and Estimate, so it now follows the same rule:
    admins and superusers see all; everyone else only what they created or are
    assigned to. The ``recurring_invoice`` fixture has no creator and no
    assignee, so ``user_client`` is neither -- every block below is a 403.
    """

    def test_member_can_list_recurring(self, user_client, recurring_invoice):
        # Never a 500 (the list carries no bad Profile-vs-User comparison).
        assert user_client.get("/api/invoices/recurring/").status_code == 200

    def test_member_list_excludes_unowned(self, user_client, recurring_invoice):
        response = user_client.get("/api/invoices/recurring/")
        ids = [r["id"] for r in response.json()["results"]]
        assert str(recurring_invoice.id) not in ids

    def test_admin_list_includes_it(self, admin_client, recurring_invoice):
        response = admin_client.get("/api/invoices/recurring/")
        ids = [r["id"] for r in response.json()["results"]]
        assert str(recurring_invoice.id) in ids

    def test_member_not_owner_cannot_get(self, user_client, recurring_invoice):
        response = user_client.get(f"/api/invoices/recurring/{recurring_invoice.id}/")
        assert response.status_code == 403

    def test_member_not_owner_cannot_update(self, user_client, recurring_invoice):
        response = user_client.put(
            f"/api/invoices/recurring/{recurring_invoice.id}/",
            {"title": "Hijacked"},
            format="json",
        )
        assert response.status_code == 403
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.title == "Monthly Hosting"

    def test_member_not_owner_cannot_delete(self, user_client, recurring_invoice):
        response = user_client.delete(
            f"/api/invoices/recurring/{recurring_invoice.id}/"
        )
        assert response.status_code == 403
        assert RecurringInvoice.objects.filter(id=recurring_invoice.id).exists()

    def test_member_not_owner_cannot_toggle(self, user_client, recurring_invoice):
        was_active = recurring_invoice.is_active
        response = user_client.post(
            f"/api/invoices/recurring/{recurring_invoice.id}/toggle/"
        )
        assert response.status_code == 403
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.is_active is was_active

    def test_assignee_can_get_and_toggle(
        self, user_client, user_profile, recurring_invoice
    ):
        recurring_invoice.assigned_to.set([user_profile])
        assert (
            user_client.get(
                f"/api/invoices/recurring/{recurring_invoice.id}/"
            ).status_code
            == 200
        )
        response = user_client.post(
            f"/api/invoices/recurring/{recurring_invoice.id}/toggle/"
        )
        assert response.status_code == 200
        recurring_invoice.refresh_from_db()
        assert recurring_invoice.is_active is False


@pytest.mark.django_db
class TestRecurringInvoiceListRowShape:
    """The row must carry what the schedules worklist renders."""

    def test_row_includes_terms_cadence_and_contact(
        self, admin_client, recurring_invoice
    ):
        recurring_invoice.frequency = "CUSTOM"
        recurring_invoice.custom_days = 14
        recurring_invoice.save(update_fields=["frequency", "custom_days"])
        response = admin_client.get("/api/invoices/recurring/")
        row = next(
            r
            for r in response.json()["results"]
            if r["id"] == str(recurring_invoice.id)
        )
        assert row["payment_terms"] == "NET_30"
        assert row["custom_days"] == 14
        # contact_name comes from the Contact FK, present on the fixture.
        assert row["contact_name"]


# ---------------------------------------------------------------------------
# Invoice Templates CRUD
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceTemplateListView:
    """Tests for GET/POST /api/invoices/templates/."""

    def test_list_templates(self, admin_client, template):
        response = admin_client.get("/api/invoices/templates/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    def test_create_template(self, admin_client):
        response = admin_client.post(
            "/api/invoices/templates/",
            {
                "name": "New Template",
                "primary_color": "#FF0000",
                "secondary_color": "#00FF00",
                "default_notes": "Thanks!",
                "default_terms": "Net 15",
                "is_default": False,
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["template"]["name"] == "New Template"

    def test_create_template_missing_name(self, admin_client):
        response = admin_client.post(
            "/api/invoices/templates/",
            {"primary_color": "#FF0000"},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestInvoiceTemplateDetailView:
    """Tests for GET/PUT/DELETE /api/invoices/templates/<pk>/."""

    def test_get_template(self, admin_client, template):
        response = admin_client.get(f"/api/invoices/templates/{template.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Default Template"

    def test_get_nonexistent_template(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.get(f"/api/invoices/templates/{fake_id}/")
        assert response.status_code == 404

    def test_update_template(self, admin_client, template):
        response = admin_client.put(
            f"/api/invoices/templates/{template.id}/",
            {"name": "Updated Template"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert data["template"]["name"] == "Updated Template"

    def test_update_nonexistent_template(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/templates/{fake_id}/",
            {"name": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_template(self, admin_client, template):
        response = admin_client.delete(f"/api/invoices/templates/{template.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False
        assert not InvoiceTemplate.objects.filter(id=template.id).exists()

    def test_delete_nonexistent_template(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/templates/{fake_id}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestInvoiceTemplateColourValidation:
    """``primary_color`` / ``secondary_color`` have to parse as CSS colours.

    ``pdf.py`` substitutes ``primary_color`` into the stylesheet by string
    replacement, so a value a renderer cannot parse does not raise, it prints
    every invoice in the org with a broken accent. The model carries only
    ``max_length=7``, so before this the three clients (two web forms and the
    phone) were the only thing refusing ``purple``.
    """

    def test_a_six_digit_hex_is_accepted(self, admin_client):
        response = admin_client.post(
            "/api/invoices/templates/",
            {"name": "Hex", "primary_color": "#a1B2c3", "secondary_color": "#000000"},
            format="json",
        )
        assert response.status_code == 201, response.content
        assert InvoiceTemplate.objects.get(name="Hex").primary_color == "#a1B2c3"

    def test_a_colour_name_is_refused(self, admin_client):
        response = admin_client.post(
            "/api/invoices/templates/",
            {"name": "Named", "primary_color": "purple"},
            format="json",
        )
        assert response.status_code == 400
        assert "primary_color" in response.json()["errors"]
        assert not InvoiceTemplate.objects.filter(name="Named").exists()

    def test_a_missing_hash_is_refused(self, admin_client):
        response = admin_client.post(
            "/api/invoices/templates/",
            {"name": "Bare", "primary_color": "3B82F6"},
            format="json",
        )
        assert response.status_code == 400
        assert not InvoiceTemplate.objects.filter(name="Bare").exists()

    def test_the_secondary_colour_is_checked_too(self, admin_client):
        """Two fields, two validators; one of them was easy to forget."""
        response = admin_client.post(
            "/api/invoices/templates/",
            {"name": "Second", "primary_color": "#3B82F6", "secondary_color": "}a{b:c"},
            format="json",
        )
        assert response.status_code == 400
        assert "secondary_color" in response.json()["errors"]

    def test_an_update_is_checked_as_well_as_a_create(self, admin_client, template):
        """The edit form writes these fields, so PUT is the likelier caller."""
        response = admin_client.put(
            f"/api/invoices/templates/{template.id}/",
            {"primary_color": "red"},
            format="json",
        )
        assert response.status_code == 400
        template.refresh_from_db()
        assert template.primary_color == "#3B82F6"

    def test_omitting_a_colour_leaves_it_alone(self, admin_client, template):
        """PUT is partial. Editing only the name must not demand the colours."""
        response = admin_client.put(
            f"/api/invoices/templates/{template.id}/",
            {"name": "Renamed"},
            format="json",
        )
        assert response.status_code == 200, response.content
        template.refresh_from_db()
        assert template.primary_color == "#3B82F6"


@pytest.mark.django_db
class TestInvoiceTemplateWriteAuthorization:
    """Invoice templates are org-wide shared config: how every invoice reaches a
    customer. Any member reads the catalogue, but creating/editing/deleting a
    template, or flipping the org default, is admin-only (see
    _forbid_non_admin_template). Both directions proven: a non-admin is
    refused, an admin is allowed.
    """

    def test_non_admin_can_list_templates(self, user_client, template):
        response = user_client.get("/api/invoices/templates/")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_non_admin_can_read_template(self, user_client, template):
        response = user_client.get(f"/api/invoices/templates/{template.id}/")
        assert response.status_code == 200
        assert response.json()["name"] == "Default Template"

    def test_non_admin_cannot_create_template(self, user_client):
        response = user_client.post(
            "/api/invoices/templates/",
            {"name": "Sneaky", "primary_color": "#FF0000"},
            format="json",
        )
        assert response.status_code == 403
        assert not InvoiceTemplate.objects.filter(name="Sneaky").exists()

    def test_non_admin_cannot_update_template(self, user_client, template):
        response = user_client.put(
            f"/api/invoices/templates/{template.id}/",
            {"name": "Hijacked"},
            format="json",
        )
        assert response.status_code == 403
        template.refresh_from_db()
        assert template.name == "Default Template"

    def test_non_admin_cannot_flip_default(self, user_client, org_a, template):
        # The default drives which template every PDF uses; a member must not be
        # able to swap it. `template` is already the default, so try to demote it.
        other = InvoiceTemplate.objects.create(
            name="Other", is_default=False, org=org_a
        )
        response = user_client.put(
            f"/api/invoices/templates/{other.id}/",
            {"is_default": True},
            format="json",
        )
        assert response.status_code == 403
        other.refresh_from_db()
        assert other.is_default is False

    def test_non_admin_cannot_delete_template(self, user_client, template):
        response = user_client.delete(f"/api/invoices/templates/{template.id}/")
        assert response.status_code == 403
        assert InvoiceTemplate.objects.filter(id=template.id).exists()

    def test_admin_can_create_update_delete(self, admin_client):
        created = admin_client.post(
            "/api/invoices/templates/",
            {"name": "Adminmade", "primary_color": "#123456"},
            format="json",
        )
        assert created.status_code == 201
        tid = created.json()["template"]["id"]

        updated = admin_client.put(
            f"/api/invoices/templates/{tid}/",
            {"name": "Adminrenamed"},
            format="json",
        )
        assert updated.status_code == 200
        assert updated.json()["template"]["name"] == "Adminrenamed"

        deleted = admin_client.delete(f"/api/invoices/templates/{tid}/")
        assert deleted.status_code == 200
        assert not InvoiceTemplate.objects.filter(id=tid).exists()


@pytest.mark.django_db
class TestInvoiceTemplateSafeShape:
    """The list/detail responses must never carry the raw template_html /
    template_css (org-authored markup that WeasyPrint renders into a PDF, a
    stored-XSS vector the moment it reaches a browser DOM). They surface flags
    and a byte count instead, plus usage/authorship the page needs.
    """

    def test_list_never_exposes_markup(self, admin_client, org_a):
        InvoiceTemplate.objects.create(
            name="Custom",
            template_html="<div>{{ invoice_number }}</div>",
            template_css="body { color: red; }",
            org=org_a,
        )
        response = admin_client.get("/api/invoices/templates/")
        assert response.status_code == 200
        row = next(r for r in response.json()["results"] if r["name"] == "Custom")
        assert "template_html" not in row
        assert "template_css" not in row
        assert row["has_custom_html"] is True
        assert row["has_custom_css"] is True
        assert row["custom_html_bytes"] == len(
            "<div>{{ invoice_number }}</div>".encode("utf-8")
        )

    def test_detail_never_exposes_markup(self, admin_client, org_a):
        tpl = InvoiceTemplate.objects.create(
            name="Custom2",
            template_html="<b>secret</b>",
            template_css="b{}",
            org=org_a,
        )
        response = admin_client.get(f"/api/invoices/templates/{tpl.id}/")
        assert response.status_code == 200
        body = response.json()
        assert "template_html" not in body
        assert "template_css" not in body
        assert body["has_custom_html"] is True

    def test_used_on_invoices_counts(self, admin_client, org_a, account_for_invoice):
        tpl = InvoiceTemplate.objects.create(name="Counted", org=org_a)
        for _ in range(2):
            Invoice.objects.create(
                invoice_title="X",
                account=account_for_invoice,
                currency="USD",
                org=org_a,
                template=tpl,
            )
        response = admin_client.get("/api/invoices/templates/")
        row = next(r for r in response.json()["results"] if r["name"] == "Counted")
        assert row["used_on_invoices"] == 2


@pytest.mark.django_db
class TestSafePdfUrlFetcher:
    """The invoice/estimate PDF render fetcher. Templates carry org-authored
    HTML/CSS rendered server-side with base_url=BASE_DIR, so an <img>/url() in
    that markup is an SSRF + local-file-read lever. The fetcher denies by
    default and permits only data: URIs, files inside MEDIA_ROOT (the org logo
    in dev), and HTTPS to the configured S3 host (the logo in prod). Both
    directions proven per branch.
    """

    def _fetcher(self):
        from invoices.pdf import safe_pdf_url_fetcher

        return safe_pdf_url_fetcher

    def test_blocks_etc_passwd(self):
        with pytest.raises(ValueError):
            self._fetcher()("file:///etc/passwd")

    def test_blocks_app_source(self, settings):
        # The exact LFI the sink enabled: base_url=BASE_DIR let markup read
        # settings.py / .env / keys. All outside MEDIA_ROOT.
        with pytest.raises(ValueError):
            self._fetcher()(f"file://{settings.BASE_DIR}/crm/settings.py")

    def test_blocks_cloud_metadata_ssrf(self):
        with pytest.raises(ValueError):
            self._fetcher()("http://169.254.169.254/latest/meta-data/")

    def test_blocks_localhost_ssrf(self):
        with pytest.raises(ValueError):
            self._fetcher()("http://127.0.0.1:8000/api/internal/")

    def test_blocks_arbitrary_external_host(self):
        with pytest.raises(ValueError):
            self._fetcher()("https://evil.example.com/x.png")

    def test_blocks_other_schemes(self):
        for url in ("ftp://host/f", "gopher://host/1"):
            with pytest.raises(ValueError):
                self._fetcher()(url)

    def test_allows_data_uri(self):
        with patch("invoices.pdf.default_url_fetcher", lambda u: {"delegated": u}):
            result = self._fetcher()("data:text/plain,hello")
        assert result == {"delegated": "data:text/plain,hello"}

    def test_allows_media_file(self, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        (tmp_path / "org_logos").mkdir()
        (tmp_path / "org_logos" / "logo.png").write_bytes(b"PNG")
        with patch("invoices.pdf.default_url_fetcher", lambda u: {"delegated": u}):
            result = self._fetcher()("file:///media/org_logos/logo.png")
        assert result["delegated"].endswith("org_logos/logo.png")

    def test_blocks_media_traversal(self, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        (tmp_path.parent / "secret.txt").write_bytes(b"nope")
        with pytest.raises(ValueError):
            self._fetcher()("file:///media/../secret.txt")

    def test_allows_s3_host_in_prod(self, settings):
        settings.AWS_S3_CUSTOM_DOMAIN = "mybucket.s3.amazonaws.com"
        with patch("invoices.pdf.default_url_fetcher", lambda u: {"delegated": u}):
            result = self._fetcher()(
                "https://mybucket.s3.amazonaws.com/media/org_logos/logo.png"
            )
        assert result["delegated"].startswith("https://mybucket.s3.amazonaws.com/")

    def test_blocks_s3_lookalike_host(self, settings):
        settings.AWS_S3_CUSTOM_DOMAIN = "mybucket.s3.amazonaws.com"
        with pytest.raises(ValueError):
            self._fetcher()("https://mybucket.s3.amazonaws.com.evil.com/x")

    def test_blocks_non_logo_media_cross_tenant(self, settings, tmp_path):
        # Files under MEDIA_ROOT that are not logos (another org's attachments or
        # documents. MEDIA_ROOT is a flat, cross-tenant tree) must be blocked.
        # Only the logo subtrees are allowed.
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        att = tmp_path / "attachments" / "2026" / "07"
        att.mkdir(parents=True)
        (att / "victim-contract.png").write_bytes(b"PNG")
        with pytest.raises(ValueError):
            self._fetcher()("file:///media/attachments/2026/07/victim-contract.png")


@pytest.mark.django_db
class TestPdfRenderWiring:
    """The safe fetcher must reach BOTH the HTML and the CSS render. WeasyPrint
    resolves @import / @font-face / @color-profile through the CSS object's own
    fetcher at PARSE time, so a CSS() left on the default fetcher reopens the
    whole SSRF/LFI via template_css. This pins the fetcher onto both constructors
    so that regression can't come back silently.
    """

    def test_invoice_render_wires_safe_fetcher_into_html_and_css(
        self, org_a, account_for_invoice
    ):
        from invoices import pdf

        tpl = InvoiceTemplate.objects.create(
            name="Malicious",
            template_html="<div>{{ invoice_number }}</div>",
            template_css='@import url("http://169.254.169.254/latest/meta-data/");',
            org=org_a,
        )
        inv = Invoice.objects.create(
            invoice_title="X",
            account=account_for_invoice,
            currency="USD",
            org=org_a,
            template=tpl,
        )
        captured = {}

        class FakeHTML:
            def __init__(self, **kwargs):
                captured["html"] = kwargs

            def write_pdf(self, target, **kwargs):
                target.write(b"%PDF-1.7")

        class FakeCSS:
            def __init__(self, **kwargs):
                captured["css"] = kwargs

        with patch("invoices.pdf.HTML", FakeHTML), patch("invoices.pdf.CSS", FakeCSS):
            pdf.generate_invoice_pdf(inv)

        assert captured["html"].get("url_fetcher") is pdf.safe_pdf_url_fetcher
        assert captured["css"].get("url_fetcher") is pdf.safe_pdf_url_fetcher


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceComments:
    """Tests for invoice comment endpoints."""

    def test_create_comment(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": "This is a test comment"},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["error"] is False
        assert data["message"] == "Comment added"
        assert "comment" in data

    def test_create_comment_empty(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": ""},
            format="json",
        )
        assert response.status_code == 400

    def test_create_comment_missing_text(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {},
            format="json",
        )
        assert response.status_code == 400

    def test_create_comment_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(
            f"/api/invoices/{fake_id}/comments/",
            {"comment": "Ghost"},
            format="json",
        )
        assert response.status_code == 404

    def test_update_comment(self, admin_client, invoice):
        # Create first
        resp = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": "Original"},
            format="json",
        )
        comment_id = resp.json()["comment"]["id"]
        # Update
        response = admin_client.put(
            f"/api/invoices/comments/{comment_id}/",
            {"comment": "Updated comment"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False

    def test_delete_comment(self, admin_client, invoice):
        resp = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": "To delete"},
            format="json",
        )
        comment_id = resp.json()["comment"]["id"]
        response = admin_client.delete(f"/api/invoices/comments/{comment_id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is False

    def test_update_nonexistent_comment(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.put(
            f"/api/invoices/comments/{fake_id}/",
            {"comment": "Nope"},
            format="json",
        )
        assert response.status_code == 404

    def test_delete_nonexistent_comment(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/comments/{fake_id}/")
        assert response.status_code == 404

    def test_regular_user_cannot_edit_others_comment(
        self, admin_client, user_client, invoice
    ):
        # Admin creates comment
        resp = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": "Admin's comment"},
            format="json",
        )
        comment_id = resp.json()["comment"]["id"]
        # Regular user tries to edit
        response = user_client.put(
            f"/api/invoices/comments/{comment_id}/",
            {"comment": "Hacked"},
            format="json",
        )
        assert response.status_code == 403

    def test_regular_user_cannot_delete_others_comment(
        self, admin_client, user_client, invoice
    ):
        resp = admin_client.post(
            f"/api/invoices/{invoice.id}/comments/",
            {"comment": "Admin's comment"},
            format="json",
        )
        comment_id = resp.json()["comment"]["id"]
        response = user_client.delete(f"/api/invoices/comments/{comment_id}/")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceAttachments:
    """Tests for invoice attachment endpoints."""

    def test_upload_attachment_no_file(self, admin_client, invoice):
        response = admin_client.post(
            f"/api/invoices/{invoice.id}/attachments/",
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] is True
        assert data["message"] == "File required"

    def test_upload_attachment_nonexistent_invoice(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.post(f"/api/invoices/{fake_id}/attachments/")
        assert response.status_code == 404

    def test_delete_attachment_nonexistent(self, admin_client):
        fake_id = uuid.uuid4()
        response = admin_client.delete(f"/api/invoices/attachments/{fake_id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Reports / Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceDashboard:
    """Tests for GET /api/invoices/reports/dashboard/."""

    def test_dashboard(self, admin_client, invoice):
        response = admin_client.get("/api/invoices/reports/dashboard/")
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "total_invoiced" in data["summary"]
        assert "total_paid" in data["summary"]
        assert "total_due" in data["summary"]
        assert "status_counts" in data
        assert "overdue" in data
        assert "recent_activity" in data
        assert "estimates" in data

    def test_dashboard_with_data(self, admin_client, invoice, estimate):
        """Dashboard should reflect existing invoices and estimates."""
        response = admin_client.get("/api/invoices/reports/dashboard/")
        assert response.status_code == 200
        data = response.json()
        assert data["recent_activity"]["invoices_created_30d"] >= 1


@pytest.mark.django_db
class TestRevenueReport:
    """Tests for GET /api/invoices/reports/revenue/."""

    def test_revenue_report_default(self, admin_client):
        response = admin_client.get("/api/invoices/reports/revenue/")
        assert response.status_code == 200
        data = response.json()
        assert "start_date" in data
        assert "end_date" in data
        assert "group_by" in data
        assert data["group_by"] == "month"
        assert "data" in data
        assert "total" in data

    def test_revenue_report_by_day(self, admin_client):
        response = admin_client.get(
            "/api/invoices/reports/revenue/?group_by=day"
            "&start_date=2026-01-01&end_date=2026-12-31"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["group_by"] == "day"

    def test_revenue_report_by_week(self, admin_client):
        response = admin_client.get("/api/invoices/reports/revenue/?group_by=week")
        assert response.status_code == 200
        data = response.json()
        assert data["group_by"] == "week"

    def test_revenue_report_by_year(self, admin_client):
        response = admin_client.get("/api/invoices/reports/revenue/?group_by=year")
        assert response.status_code == 200
        data = response.json()
        assert data["group_by"] == "year"

    def test_revenue_report_with_dates(self, admin_client):
        response = admin_client.get(
            "/api/invoices/reports/revenue/?start_date=2025-01-01&end_date=2026-12-31"
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestAgingReport:
    """Tests for GET /api/invoices/reports/aging/."""

    def test_aging_report_empty(self, admin_client):
        response = admin_client.get("/api/invoices/reports/aging/")
        assert response.status_code == 200
        data = response.json()
        assert "current" in data
        assert "1_30_days" in data
        assert "31_60_days" in data
        assert "61_90_days" in data
        assert "over_90_days" in data
        assert "total" in data

    def _make_unpaid_invoice(self, title, status, due_date, account, org):
        """Helper: create an invoice with a line item so amount_due > 0."""
        inv = Invoice.objects.create(
            invoice_title=title,
            account=account,
            status=status,
            currency="USD",
            org=org,
        )
        InvoiceLineItem.objects.create(
            invoice=inv,
            name="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            org=org,
        )
        # Recalculate and then override due_date and status via update_fields
        inv.recalculate_totals()
        inv.due_date = due_date
        inv.status = status
        inv.save(
            update_fields=[
                "due_date",
                "status",
                "subtotal",
                "total_amount",
                "amount_due",
            ]
        )
        return inv

    def test_aging_report_with_overdue_invoices(
        self, admin_client, account_for_invoice, org_a
    ):
        """Create invoices with various due dates and check aging buckets."""
        today = timezone.localdate()
        self._make_unpaid_invoice(
            "Current Invoice",
            "Sent",
            today + datetime.timedelta(days=5),
            account_for_invoice,
            org_a,
        )
        self._make_unpaid_invoice(
            "Overdue 15d",
            "Sent",
            today - datetime.timedelta(days=15),
            account_for_invoice,
            org_a,
        )
        self._make_unpaid_invoice(
            "Overdue 45d",
            "Sent",
            today - datetime.timedelta(days=45),
            account_for_invoice,
            org_a,
        )
        self._make_unpaid_invoice(
            "Overdue 75d",
            "Partially_Paid",
            today - datetime.timedelta(days=75),
            account_for_invoice,
            org_a,
        )
        self._make_unpaid_invoice(
            "Overdue 120d",
            "Overdue",
            today - datetime.timedelta(days=120),
            account_for_invoice,
            org_a,
        )
        response = admin_client.get("/api/invoices/reports/aging/")
        assert response.status_code == 200
        data = response.json()
        assert data["current"]["count"] >= 1
        assert data["1_30_days"]["count"] >= 1
        assert data["31_60_days"]["count"] >= 1
        assert data["61_90_days"]["count"] >= 1
        assert data["over_90_days"]["count"] >= 1

    def test_aging_report_with_no_due_date(
        self, admin_client, account_for_invoice, org_a
    ):
        """Invoice with no due date goes to current bucket."""
        self._make_unpaid_invoice(
            "No Due Date",
            "Sent",
            None,
            account_for_invoice,
            org_a,
        )
        response = admin_client.get("/api/invoices/reports/aging/")
        assert response.status_code == 200
        data = response.json()
        assert data["current"]["count"] >= 1


@pytest.mark.django_db
class TestReportsAreAdminOnly:
    """The three invoice reports are org-wide financials, admin-only.

    They roll up the whole org's invoiced/collected/overdue and its AR aging
    across every account. The list views scope a non-admin to their own records;
    a report that showed them the org's total revenue and receivables would leak
    exactly what that scoping protects. So a member (role ``USER``) is refused
    all three, and an admin is allowed.
    """

    REPORTS = (
        "/api/invoices/reports/dashboard/",
        "/api/invoices/reports/revenue/",
        "/api/invoices/reports/aging/",
    )

    def test_member_is_forbidden_every_report(self, user_client):
        for path in self.REPORTS:
            assert user_client.get(path).status_code == 403, path

    def test_admin_is_allowed_every_report(self, admin_client):
        for path in self.REPORTS:
            assert admin_client.get(path).status_code == 200, path


@pytest.mark.django_db
class TestReportPayloadShape:
    """The dashboard, revenue and aging rows carry what the v2 page renders."""

    def test_dashboard_has_avg_pay_and_invoice_count(self, admin_client, invoice):
        data = admin_client.get("/api/invoices/reports/dashboard/").json()
        assert "average_days_to_pay" in data
        assert isinstance(data["average_days_to_pay"], int)
        assert data["invoice_count"] >= 1

    def test_average_days_to_pay_is_computed(self, admin_client, invoice):
        # A paid invoice issued 10 days before it was paid -> ~10 day average.
        invoice.issue_date = timezone.localdate() - datetime.timedelta(days=10)
        invoice.status = "Paid"
        invoice.paid_at = timezone.now()
        invoice.amount_paid = invoice.total_amount
        invoice.save()
        data = admin_client.get("/api/invoices/reports/dashboard/").json()
        assert data["average_days_to_pay"] == 10

    def test_revenue_rows_carry_invoiced_and_paid(self, admin_client, invoice):
        invoice.issue_date = timezone.localdate()
        invoice.status = "Paid"
        invoice.paid_at = timezone.now()
        invoice.amount_paid = invoice.total_amount
        invoice.save()
        data = admin_client.get("/api/invoices/reports/revenue/?group_by=month").json()
        assert "invoiced" in data["total"]
        assert data["data"], "expected at least one period row"
        row = data["data"][0]
        assert "invoiced" in row and "revenue" in row

    def test_aging_has_by_account_and_overdue(
        self, admin_client, account_for_invoice, org_a
    ):
        # An overdue invoice with an account shows up in the by-account roll-up.
        # A line item is needed so recalculate_totals leaves amount_due > 0.
        inv = Invoice.objects.create(
            invoice_title="Late invoice",
            status="Sent",
            account=account_for_invoice,
            client_name="Late Co",
            org=org_a,
        )
        InvoiceLineItem.objects.create(
            invoice=inv,
            name="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("500.00"),
            org=org_a,
        )
        inv.recalculate_totals()
        inv.due_date = timezone.localdate() - datetime.timedelta(days=45)
        inv.save(update_fields=["due_date", "subtotal", "total_amount", "amount_due"])
        data = admin_client.get("/api/invoices/reports/aging/").json()
        assert "by_account" in data
        assert "overdue" in data
        assert data["overdue"]["count"] >= 1
        row = next(
            (r for r in data["by_account"] if r["id"] == str(account_for_invoice.id)),
            None,
        )
        assert row is not None
        assert row["count"] >= 1
        assert row["oldest_days"] >= 45


def _money_invoice(org, account, currency, total, **fields):
    """An invoice with fixed amounts, written past save()'s recalculation.

    ``Invoice.save`` rebuilds every total from line items, so the amounts are
    set with a queryset update instead. ``paid`` defaults to nothing paid.
    """
    paid = Decimal(fields.pop("paid", "0"))
    inv = Invoice.objects.create(
        invoice_title=f"{currency} {total}",
        account=account,
        currency=currency,
        status=fields.pop("status", "Sent"),
        org=org,
    )
    Invoice.objects.filter(pk=inv.pk).update(
        total_amount=Decimal(total),
        amount_paid=paid,
        amount_due=Decimal(total) - paid,
        **fields,
    )
    return inv


def _by_currency(rows, field):
    """``{currency: Decimal(field)}`` from a ``by_currency`` list."""
    return {r["currency"]: Decimal(r[field]) for r in rows}


@pytest.mark.django_db
class TestReportsNeverAddCurrencies:
    """Invoices carry their own currency and there is no exchange-rate data.

    Every money figure in the three reports is therefore grouped by currency.
    The plain figure each endpoint always had is kept for a single-currency org
    (the common case, so those clients see no change) and is ``None`` when the
    set spans several currencies, rather than a sum of unlike amounts.
    """

    DASH = "/api/invoices/reports/dashboard/"
    REVENUE = "/api/invoices/reports/revenue/?group_by=month"
    AGING = "/api/invoices/reports/aging/"

    def _paid_and_overdue(self, org, account, currency, paid, owed):
        today = timezone.localdate()
        _money_invoice(
            org,
            account,
            currency,
            paid,
            paid=paid,
            status="Paid",
            issue_date=today,
            paid_at=timezone.now(),
        )
        _money_invoice(
            org,
            account,
            currency,
            owed,
            issue_date=today,
            due_date=today - datetime.timedelta(days=10),
        )

    def test_single_currency_figures_are_unchanged(
        self, admin_client, account_for_invoice, org_a
    ):
        self._paid_and_overdue(org_a, account_for_invoice, "EUR", "100.00", "40.00")

        dash = admin_client.get(self.DASH).json()
        assert Decimal(dash["summary"]["total_invoiced"]) == Decimal("140")
        assert Decimal(dash["summary"]["total_paid"]) == Decimal("100")
        assert Decimal(dash["summary"]["total_due"]) == Decimal("40")
        assert Decimal(dash["overdue"]["amount"]) == Decimal("40")
        assert dash["overdue"]["count"] == 1
        assert Decimal(dash["recent_activity"]["revenue_30d"]) == Decimal("100")
        assert Decimal(dash["recent_activity"]["invoiced_30d"]) == Decimal("140")
        assert [r["currency"] for r in dash["summary"]["by_currency"]] == ["EUR"]

        rev = admin_client.get(self.REVENUE).json()
        assert Decimal(rev["total"]["invoiced"]) == Decimal("140")
        assert Decimal(rev["total"]["revenue"]) == Decimal("100")
        assert rev["total"]["count"] == 1
        (row,) = rev["data"]
        assert Decimal(row["invoiced"]) == Decimal("140")
        assert Decimal(row["revenue"]) == Decimal("100")

        aging = admin_client.get(self.AGING).json()
        assert Decimal(aging["1_30_days"]["amount"]) == Decimal("40")
        assert Decimal(aging["overdue"]["amount"]) == Decimal("40")
        assert Decimal(aging["total"]["amount"]) == Decimal("40")
        assert aging["1_30_days"]["invoices"][0]["currency"] == "EUR"
        (acct,) = aging["by_account"]
        assert acct["currency"] == "EUR"
        assert Decimal(acct["amount"]) == Decimal("40")

    def test_two_currencies_are_never_summed(
        self, admin_client, account_for_invoice, org_a
    ):
        self._paid_and_overdue(org_a, account_for_invoice, "USD", "100.00", "40.00")
        self._paid_and_overdue(org_a, account_for_invoice, "EUR", "7.00", "3.00")

        dash = admin_client.get(self.DASH).json()
        summary = dash["summary"]
        for field in ("total_invoiced", "total_paid", "total_due"):
            assert summary[field] is None, field
        assert _by_currency(summary["by_currency"], "total_invoiced") == {
            "EUR": Decimal("10"),
            "USD": Decimal("140"),
        }
        assert _by_currency(summary["by_currency"], "total_due") == {
            "EUR": Decimal("3"),
            "USD": Decimal("40"),
        }
        assert dash["overdue"]["amount"] is None
        assert dash["overdue"]["count"] == 2
        assert _by_currency(dash["overdue"]["by_currency"], "amount") == {
            "EUR": Decimal("3"),
            "USD": Decimal("40"),
        }
        recent = dash["recent_activity"]
        assert recent["revenue_30d"] is None and recent["invoiced_30d"] is None
        assert _by_currency(recent["by_currency"], "revenue_30d") == {
            "EUR": Decimal("7"),
            "USD": Decimal("100"),
        }

        rev = admin_client.get(self.REVENUE).json()
        assert rev["total"]["invoiced"] is None
        assert rev["total"]["revenue"] is None
        assert rev["total"]["count"] == 2
        assert _by_currency(rev["total"]["by_currency"], "invoiced") == {
            "EUR": Decimal("10"),
            "USD": Decimal("140"),
        }
        (row,) = rev["data"]
        assert row["invoiced"] is None and row["revenue"] is None
        assert _by_currency(row["by_currency"], "revenue") == {
            "EUR": Decimal("7"),
            "USD": Decimal("100"),
        }

        aging = admin_client.get(self.AGING).json()
        assert aging["overdue"]["amount"] is None
        assert aging["total"]["amount"] is None
        assert _by_currency(aging["total"]["by_currency"], "amount") == {
            "EUR": Decimal("3"),
            "USD": Decimal("40"),
        }
        # One account owing in two currencies is two rows, never one sum.
        assert _by_currency(aging["by_account"], "amount") == {
            "EUR": Decimal("3"),
            "USD": Decimal("40"),
        }

    def test_aging_buckets_are_split_by_currency(
        self, admin_client, account_for_invoice, org_a
    ):
        today = timezone.localdate()
        for currency, total, days in (
            ("USD", "100.00", 15),
            ("EUR", "40.00", 15),
            ("EUR", "70.00", 45),
            ("GBP", "5.00", -3),
        ):
            _money_invoice(
                org_a,
                account_for_invoice,
                currency,
                total,
                due_date=today - datetime.timedelta(days=days),
            )

        aging = admin_client.get(self.AGING).json()

        early = aging["1_30_days"]
        assert early["count"] == 2
        assert early["amount"] is None
        assert early["by_currency"] == [
            {
                "currency": "EUR",
                "count": 1,
                "amount": early["by_currency"][0]["amount"],
            },
            {
                "currency": "USD",
                "count": 1,
                "amount": early["by_currency"][1]["amount"],
            },
        ]
        assert _by_currency(early["by_currency"], "amount") == {
            "EUR": Decimal("40"),
            "USD": Decimal("100"),
        }
        # A bucket holding one currency keeps its plain figure.
        assert Decimal(aging["31_60_days"]["amount"]) == Decimal("70")
        assert Decimal(aging["current"]["amount"]) == Decimal("5")
        assert aging["current"]["by_currency"][0]["currency"] == "GBP"
        assert aging["over_90_days"]["by_currency"] == []
        assert Decimal(aging["over_90_days"]["amount"]) == Decimal("0")
        assert _by_currency(aging["overdue"]["by_currency"], "amount") == {
            "EUR": Decimal("110"),
            "USD": Decimal("100"),
        }
        assert _by_currency(aging["total"]["by_currency"], "amount") == {
            "EUR": Decimal("110"),
            "GBP": Decimal("5"),
            "USD": Decimal("100"),
        }
        assert aging["total"]["count"] == 4

    def test_dashboard_overdue_counts_invoices_marked_overdue(
        self, admin_client, account_for_invoice, org_a
    ):
        # The nightly task flips late invoices to "Overdue"; they are still owed.
        _money_invoice(
            org_a,
            account_for_invoice,
            "USD",
            "25.00",
            status="Overdue",
            due_date=timezone.localdate() - datetime.timedelta(days=40),
        )
        dash = admin_client.get(self.DASH).json()
        assert dash["overdue"]["count"] == 1
        assert Decimal(dash["overdue"]["amount"]) == Decimal("25")

    def test_other_orgs_invoices_stay_out(
        self, admin_client, account_for_invoice, account_org_b, org_a, org_b
    ):
        self._paid_and_overdue(org_a, account_for_invoice, "USD", "100.00", "40.00")
        self._paid_and_overdue(org_b, account_org_b, "EUR", "9.00", "9.00")

        dash = admin_client.get(self.DASH).json()
        assert [r["currency"] for r in dash["summary"]["by_currency"]] == ["USD"]
        aging = admin_client.get(self.AGING).json()
        assert [r["currency"] for r in aging["total"]["by_currency"]] == ["USD"]


@pytest.mark.django_db
class TestListTotalsNeverAddCurrencies:
    """The invoice list's header ``totals``, on the reports' convention.

    ``_totals`` summed ``amount_due`` and friends across every visible invoice
    whatever its currency, so an org billing in USD and EUR saw one figure
    adding dollars to euros. It now groups by currency: the plain figure stays
    for a single-currency org, is ``None`` across several, and ``by_currency``
    carries the split either way. Counts have no currency and stay totalled.
    """

    URL = "/api/invoices/"

    def _book(self, org, account, currency, overdue, draft):
        today = timezone.localdate()
        _money_invoice(
            org,
            account,
            currency,
            overdue,
            due_date=today - datetime.timedelta(days=5),
        )
        _money_invoice(org, account, currency, draft, status="Draft")

    def test_single_currency_totals_are_unchanged(
        self, admin_client, account_for_invoice, org_a
    ):
        self._book(org_a, account_for_invoice, "EUR", "40.00", "15.00")

        totals = admin_client.get(self.URL).json()["totals"]

        assert totals["count"] == 2
        assert totals["action_needed"] == 2
        assert Decimal(totals["outstanding"]) == Decimal("40")
        assert Decimal(totals["overdue"]) == Decimal("40")
        assert Decimal(totals["draft"]) == Decimal("15")
        assert Decimal(totals["due_this_month"]) >= Decimal("0")
        assert Decimal(totals["paid_this_quarter"]) == Decimal("0")
        assert [r["currency"] for r in totals["by_currency"]] == ["EUR"]
        assert Decimal(totals["by_currency"][0]["overdue"]) == Decimal("40")

    def test_two_currencies_are_never_summed(
        self, admin_client, account_for_invoice, org_a
    ):
        self._book(org_a, account_for_invoice, "USD", "40.00", "15.00")
        self._book(org_a, account_for_invoice, "EUR", "3.00", "2.00")

        totals = admin_client.get(self.URL).json()["totals"]

        for field in (
            "outstanding",
            "overdue",
            "due_this_month",
            "paid_this_quarter",
            "draft",
        ):
            assert totals[field] is None, field
        assert totals["count"] == 4
        assert totals["action_needed"] == 4
        assert _by_currency(totals["by_currency"], "overdue") == {
            "EUR": Decimal("3"),
            "USD": Decimal("40"),
        }
        assert _by_currency(totals["by_currency"], "draft") == {
            "EUR": Decimal("2"),
            "USD": Decimal("15"),
        }
        assert {r["currency"]: r["count"] for r in totals["by_currency"]} == {
            "EUR": 2,
            "USD": 2,
        }

    def test_no_invoices_is_zero_with_no_currencies(self, admin_client, org_a):
        totals = admin_client.get(self.URL).json()["totals"]

        assert totals["outstanding"] == "0"
        assert totals["count"] == 0
        assert totals["by_currency"] == []

    def test_a_member_totals_only_what_they_can_see(
        self,
        user_client,
        user_profile,
        account_for_invoice,
        org_a,
    ):
        """The currency split must not widen the member's scope."""
        mine = _money_invoice(
            org_a,
            account_for_invoice,
            "USD",
            "30.00",
            due_date=timezone.localdate() - datetime.timedelta(days=5),
        )
        mine.assigned_to.add(user_profile)
        _money_invoice(org_a, account_for_invoice, "EUR", "99.00")

        totals = user_client.get(self.URL).json()["totals"]

        assert totals["count"] == 1
        assert Decimal(totals["overdue"]) == Decimal("30")
        assert [r["currency"] for r in totals["by_currency"]] == ["USD"]


def _won_opportunity(org, account, contact, name="Big Deal", currency="EUR"):
    """A CLOSED_WON deal with one line item and one contact."""
    from opportunity.models import Opportunity, OpportunityLineItem

    opp = Opportunity.objects.create(
        name=name,
        org=org,
        account=account,
        stage="CLOSED_WON",
        amount=Decimal("300"),
        currency=currency,
        closed_on=timezone.localdate(),
    )
    opp.contacts.add(contact)
    OpportunityLineItem.objects.create(
        opportunity=opp,
        org=org,
        name="Widget",
        quantity=Decimal("3"),
        unit_price=Decimal("100"),
    )
    return opp


@pytest.mark.django_db
class TestInvoiceFromOpportunity:
    """``POST /api/invoices/from-opportunity/<id>/``.

    Every call used to 500: it passed ``title=`` (not an Invoice field) and
    ``status="DRAFT"`` (not a status), and minted ``INV-000001`` style numbers
    of its own instead of the model's per-org sequence.
    """

    def _url(self, opp):
        return f"/api/invoices/from-opportunity/{opp.id}/"

    @patch("invoices.api_views.create_invoice_history")
    def test_admin_creates_a_draft_invoice_from_a_won_deal(
        self,
        history,
        admin_client,
        admin_profile,
        account_for_invoice,
        contact_for_invoice,
        org_a,
    ):
        opp = _won_opportunity(org_a, account_for_invoice, contact_for_invoice)
        # An earlier invoice today, so the new number must continue the sequence.
        earlier = _money_invoice(org_a, account_for_invoice, "USD", "1.00")

        response = admin_client.post(self._url(opp))

        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get(opportunity=opp)
        assert invoice.org == org_a
        assert invoice.status == "Draft"
        assert invoice.invoice_title == "Invoice for Big Deal"
        assert invoice.currency == "EUR"
        assert invoice.account == account_for_invoice
        assert invoice.contact == contact_for_invoice
        prefix = earlier.invoice_number.rsplit("-", 1)[0]
        assert invoice.invoice_number == f"{prefix}-0002"
        assert invoice.total_amount == Decimal("300")
        assert invoice.amount_due == Decimal("300")
        (line,) = invoice.line_items.all()
        assert line.name == "Widget" and line.org == org_a
        assert response.json()["invoice"]["id"] == str(invoice.id)
        history.delay.assert_called_once_with(
            str(invoice.id), str(admin_profile.id), [], str(org_a.id)
        )

    def test_a_long_deal_name_fits_the_title(
        self, admin_client, account_for_invoice, contact_for_invoice, org_a
    ):
        opp = _won_opportunity(
            org_a, account_for_invoice, contact_for_invoice, name="x" * 255
        )

        response = admin_client.post(self._url(opp))

        assert response.status_code == 201, response.content
        assert len(Invoice.objects.get(opportunity=opp).invoice_title) == 100

    def test_another_orgs_opportunity_is_not_found(
        self, admin_client, account_org_b, contact_org_b, org_a, org_b
    ):
        theirs = _won_opportunity(org_b, account_org_b, contact_org_b)

        response = admin_client.post(self._url(theirs))

        assert response.status_code == 404
        assert not Invoice.objects.filter(opportunity=theirs).exists()

    def test_a_member_assigned_to_the_deal_may_invoice_it(
        self,
        user_client,
        user_profile,
        account_for_invoice,
        contact_for_invoice,
        org_a,
    ):
        opp = _won_opportunity(org_a, account_for_invoice, contact_for_invoice)
        opp.assigned_to.add(user_profile)
        # The invoice is billed only to a contact and an account the caller
        # may open.
        contact_for_invoice.assigned_to.add(user_profile)
        account_for_invoice.assigned_to.add(user_profile)

        response = user_client.post(self._url(opp))

        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get(opportunity=opp, org=org_a)
        # Stamped as theirs, so it stays in their own scoped invoice list.
        assert invoice.created_by == user_profile.user
        assert invoice.assigned_to.filter(pk=user_profile.pk).exists()

    def test_a_member_with_no_part_in_the_deal_is_refused(
        self, user_client, account_for_invoice, contact_for_invoice, org_a
    ):
        opp = _won_opportunity(org_a, account_for_invoice, contact_for_invoice)

        response = user_client.post(self._url(opp))

        # 404, not 403: a deal the member cannot open answers like a missing one.
        assert response.status_code == 404
        assert not Invoice.objects.filter(opportunity=opp).exists()

    def test_an_open_deal_is_refused(
        self, admin_client, account_for_invoice, contact_for_invoice, org_a
    ):
        opp = _won_opportunity(org_a, account_for_invoice, contact_for_invoice)
        type(opp).objects.filter(pk=opp.pk).update(stage="PROPOSAL")

        response = admin_client.post(self._url(opp))

        assert response.status_code == 400
        assert not Invoice.objects.filter(opportunity=opp).exists()


# ---------------------------------------------------------------------------
# Public portal: reachability through the real middleware stack
#
# The view-level tests below this class use APIRequestFactory and call the view
# directly, which skips MIDDLEWARE entirely. That is fine for testing what the
# view returns, but it means they passed for the whole time the portal was
# returning 403 to every customer: `RequireOrgContext` listed
# "/api/public/csat/" in EXEMPT_PATHS and neither "/api/public/invoice/" nor
# "/api/public/estimate/", and `_is_exempt` is a startswith check, so the
# sibling prefixes were never covered.
#
# These tests use the full Django test client on purpose. They are the ones
# that fail if the exemption is removed, narrowed, or reordered.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPublicPortalIsReachableAnonymously:
    """The customer-facing links must work with no auth and no org context."""

    def test_invoice_link_is_reachable(self, client, invoice):
        response = client.get(f"/api/public/invoice/{invoice.public_token}/")
        assert response.status_code == 200, (
            "Anonymous GET must not be rejected by RequireOrgContext; "
            "this is the emailed customer link."
        )
        assert response.json()["invoice_number"] == invoice.invoice_number

    def test_estimate_link_is_reachable(self, client, estimate):
        response = client.get(f"/api/public/estimate/{estimate.public_token}/")
        assert response.status_code == 200
        assert response.json()["estimate_number"] == estimate.estimate_number

    def test_invoice_pdf_subpath_is_reachable(self, client, invoice):
        """The exemption is a prefix, so /pdf/ under it must be covered too."""
        with patch("invoices.public_views.generate_invoice_pdf", return_value=b"%PDF-"):
            response = client.get(f"/api/public/invoice/{invoice.public_token}/pdf/")
        assert response.status_code == 200

    def test_unknown_token_is_404_not_403(self, client):
        """A bad token must read as 'no such document', never as 'log in'.

        403 here would be the old bug wearing a different hat: it tells the
        customer to sign in to an application they have no account on.
        """
        response = client.get("/api/public/invoice/not-a-real-token/")
        assert response.status_code == 404

    def test_disabled_link_is_404(self, client, invoice):
        invoice.public_link_enabled = False
        invoice.save()
        response = client.get(f"/api/public/invoice/{invoice.public_token}/")
        assert response.status_code == 404

    def test_exemption_does_not_leak_to_the_rest_of_the_api(self, client):
        """The other half of the check: it must still return False.

        A permission check that cannot return both answers is not a check. If
        someone ever 'simplifies' EXEMPT_PATHS to "/api/public/" or "/api/",
        this is what catches it.
        """
        response = client.get("/api/invoices/")
        assert response.status_code in (401, 403)

    def test_exempt_prefixes_are_specific(self):
        """Guard the shape of the list, not just its behaviour."""
        from common.middleware.rls_context import RequireOrgContext

        exempt = RequireOrgContext.EXEMPT_PATHS
        assert "/api/public/invoice/" in exempt
        assert "/api/public/estimate/" in exempt
        assert "/api/public/" not in exempt
        assert "/api/" not in exempt


# ---------------------------------------------------------------------------
# Public Invoice Views (tested via RequestFactory to bypass RequireOrgContext middleware)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPublicInvoiceView:
    """Tests for PublicInvoiceView (via RequestFactory to bypass middleware)."""

    def _get(self, token):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicInvoiceView

        factory = APIRequestFactory()
        request = factory.get(f"/api/public/invoice/{token}/")
        view = PublicInvoiceView.as_view()
        return view(request, token=token)

    def test_public_invoice_view(self, invoice):
        response = self._get(invoice.public_token)
        assert response.status_code == 200
        assert response.data["invoice_number"] == invoice.invoice_number
        assert response.data["invoice_title"] == invoice.invoice_title
        assert "line_items" in response.data
        assert "payments" in response.data
        assert "org" in response.data
        assert "template" in response.data

    def test_public_invoice_tracks_view(self, invoice):
        invoice.status = "Sent"
        invoice.save()
        self._get(invoice.public_token)
        invoice.refresh_from_db()
        assert invoice.viewed_at is not None
        assert invoice.status == "Viewed"

    def test_public_invoice_already_viewed(self, invoice):
        """Second view should not change viewed_at."""
        from django.utils import timezone

        first_time = timezone.now()
        invoice.viewed_at = first_time
        invoice.save()
        self._get(invoice.public_token)
        invoice.refresh_from_db()
        assert invoice.viewed_at == first_time

    def test_public_invoice_not_found(self):
        response = self._get("nonexistent_token")
        assert response.status_code == 404

    def test_public_invoice_disabled_link(self, invoice):
        invoice.public_link_enabled = False
        invoice.save()
        response = self._get(invoice.public_token)
        assert response.status_code == 404

    def test_public_invoice_with_template(self, invoice, template):
        invoice.template = template
        invoice.save()
        response = self._get(invoice.public_token)
        assert response.status_code == 200
        assert response.data["template"]["primary_color"] == "#3B82F6"

    def test_public_invoice_with_default_template(self, invoice, template):
        """When invoice has no template, use org default template."""
        response = self._get(invoice.public_token)
        assert response.status_code == 200
        assert response.data["template"]["primary_color"] == "#3B82F6"


@pytest.mark.django_db
class TestPublicInvoicePDFView:
    """Tests for PublicInvoicePDFView."""

    def _get(self, token):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicInvoicePDFView

        factory = APIRequestFactory()
        request = factory.get(f"/api/public/invoice/{token}/pdf/")
        view = PublicInvoicePDFView.as_view()
        return view(request, token=token)

    @patch("invoices.public_views.generate_invoice_pdf")
    @patch("invoices.public_views.generate_invoice_filename")
    def test_public_pdf_success(self, mock_filename, mock_pdf, invoice):
        mock_pdf.return_value = b"%PDF-1.4 test"
        mock_filename.return_value = "invoice.pdf"
        response = self._get(invoice.public_token)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_public_pdf_not_found(self):
        response = self._get("bad_token")
        assert response.status_code == 404

    @patch(
        "invoices.public_views.generate_invoice_pdf",
        side_effect=ImportError("no lib"),
    )
    def test_public_pdf_import_error(self, mock_pdf, invoice):
        response = self._get(invoice.public_token)
        assert response.status_code == 503

    @patch(
        "invoices.public_views.generate_invoice_pdf",
        side_effect=RuntimeError("fail"),
    )
    def test_public_pdf_generic_error(self, mock_pdf, invoice):
        response = self._get(invoice.public_token)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# Public Estimate Views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPublicEstimateView:
    """Tests for PublicEstimateView."""

    def _get(self, token):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicEstimateView

        factory = APIRequestFactory()
        request = factory.get(f"/api/public/estimate/{token}/")
        view = PublicEstimateView.as_view()
        return view(request, token=token)

    def test_public_estimate_view(self, estimate):
        response = self._get(estimate.public_token)
        assert response.status_code == 200
        assert response.data["title"] == "Test Estimate"
        assert "line_items" in response.data
        assert "template" in response.data
        assert "org" in response.data

    def test_public_estimate_tracks_view(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        self._get(estimate.public_token)
        estimate.refresh_from_db()
        assert estimate.viewed_at is not None
        assert estimate.status == "Viewed"

    def test_public_estimate_not_found(self):
        response = self._get("nonexistent")
        assert response.status_code == 404

    def test_public_estimate_disabled_link(self, estimate):
        estimate.public_link_enabled = False
        estimate.save()
        response = self._get(estimate.public_token)
        assert response.status_code == 404

    def test_public_estimate_with_default_template(self, estimate, template):
        response = self._get(estimate.public_token)
        assert response.status_code == 200
        assert response.data["template"]["primary_color"] == "#3B82F6"

    def test_public_estimate_no_template(self, estimate):
        """No default template gives fallback colors."""
        response = self._get(estimate.public_token)
        assert response.status_code == 200
        assert response.data["template"]["primary_color"] == "#3B82F6"
        assert response.data["template"]["secondary_color"] == "#1E40AF"


@pytest.mark.django_db
class TestPublicEstimatePDFView:
    """Tests for PublicEstimatePDFView."""

    def _get(self, token):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicEstimatePDFView

        factory = APIRequestFactory()
        request = factory.get(f"/api/public/estimate/{token}/pdf/")
        view = PublicEstimatePDFView.as_view()
        return view(request, token=token)

    @patch("invoices.public_views.generate_estimate_pdf")
    @patch("invoices.public_views.generate_estimate_filename")
    def test_public_estimate_pdf_success(self, mock_filename, mock_pdf, estimate):
        mock_pdf.return_value = b"%PDF-1.4 est"
        mock_filename.return_value = "estimate.pdf"
        response = self._get(estimate.public_token)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_public_estimate_pdf_not_found(self):
        response = self._get("bad_token")
        assert response.status_code == 404

    @patch(
        "invoices.public_views.generate_estimate_pdf",
        side_effect=ImportError("no lib"),
    )
    def test_public_estimate_pdf_import_error(self, mock_pdf, estimate):
        response = self._get(estimate.public_token)
        assert response.status_code == 503

    @patch(
        "invoices.public_views.generate_estimate_pdf",
        side_effect=RuntimeError("fail"),
    )
    def test_public_estimate_pdf_generic_error(self, mock_pdf, estimate):
        response = self._get(estimate.public_token)
        assert response.status_code == 500


@pytest.mark.django_db
class TestPublicEstimateAcceptDecline:
    """Tests for PublicEstimateAcceptView and PublicEstimateDeclineView."""

    def _post_accept(self, token, data=None, **meta):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicEstimateAcceptView

        # Accepting now requires the acceptor to identify themselves; default to
        # a valid name+email so the pre-existing happy-path tests keep passing,
        # and let callers override to exercise the validation.
        if data is None:
            data = {"name": "Dana Buyer", "email": "dana@buyer.example"}
        factory = APIRequestFactory()
        request = factory.post(
            f"/api/public/estimate/{token}/accept/", data, format="json", **meta
        )
        view = PublicEstimateAcceptView.as_view()
        return view(request, token=token)

    def _post_decline(self, token):
        from rest_framework.test import APIRequestFactory

        from invoices.public_views import PublicEstimateDeclineView

        factory = APIRequestFactory()
        request = factory.post(f"/api/public/estimate/{token}/decline/")
        view = PublicEstimateDeclineView.as_view()
        return view(request, token=token)

    def test_accept_estimate(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        response = self._post_accept(
            estimate.public_token,
            data={"name": "Dana Buyer", "email": "dana@buyer.example"},
            HTTP_USER_AGENT="Mozilla/5.0 (portal test)",
            REMOTE_ADDR="203.0.113.9",
        )
        assert response.status_code == 200
        assert response.data["error"] is False
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"
        assert estimate.accepted_at is not None
        # Acceptance is authorisation; the acceptor is now recorded.
        assert estimate.accepted_by_name == "Dana Buyer"
        assert estimate.accepted_by_email == "dana@buyer.example"
        assert estimate.accepted_ip == "203.0.113.9"
        assert "portal test" in estimate.accepted_user_agent

    def test_accept_viewed_estimate(self, estimate):
        estimate.status = "Viewed"
        estimate.save()
        response = self._post_accept(estimate.public_token)
        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"

    def test_accept_draft_estimate_fails(self, estimate):
        """Can only accept Sent or Viewed estimates."""
        assert estimate.status == "Draft"
        response = self._post_accept(estimate.public_token)
        assert response.status_code == 400

    def test_accept_nonexistent_estimate(self):
        response = self._post_accept("bad_token")
        assert response.status_code == 404

    def test_accept_expired_estimate_is_rejected(self, estimate):
        """An expired quote is not acceptable at its original price."""
        import datetime

        estimate.status = "Sent"
        estimate.expiry_date = timezone.localdate() - datetime.timedelta(days=1)
        estimate.save()
        response = self._post_accept(estimate.public_token)
        assert response.status_code == 400
        assert "expired" in response.data["message"].lower()
        estimate.refresh_from_db()
        assert estimate.status == "Sent"
        assert estimate.accepted_at is None

    def test_accept_on_expiry_day_still_allowed(self, estimate):
        """is_expired is strictly past the date. The last day still counts."""

        estimate.status = "Sent"
        estimate.expiry_date = timezone.localdate()
        estimate.save()
        response = self._post_accept(estimate.public_token)
        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"

    def test_accept_requires_name_and_email(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        # Missing name
        r1 = self._post_accept(estimate.public_token, data={"email": "x@y.example"})
        assert r1.status_code == 400
        # Missing email
        r2 = self._post_accept(estimate.public_token, data={"name": "Dana"})
        assert r2.status_code == 400
        estimate.refresh_from_db()
        assert estimate.status == "Sent"
        assert estimate.accepted_at is None

    def test_accept_rejects_invalid_email(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        response = self._post_accept(
            estimate.public_token, data={"name": "Dana", "email": "not-an-email"}
        )
        assert response.status_code == 400
        estimate.refresh_from_db()
        assert estimate.status == "Sent"

    def test_accept_ip_prefers_forwarded_for(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        response = self._post_accept(
            estimate.public_token,
            data={"name": "Dana", "email": "dana@buyer.example"},
            HTTP_X_FORWARDED_FOR="198.51.100.7, 10.0.0.1",
            REMOTE_ADDR="10.0.0.1",
        )
        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.accepted_ip == "198.51.100.7"

    def test_decline_estimate(self, estimate):
        estimate.status = "Sent"
        estimate.save()
        response = self._post_decline(estimate.public_token)
        assert response.status_code == 200
        assert response.data["error"] is False
        estimate.refresh_from_db()
        assert estimate.status == "Declined"
        assert estimate.declined_at is not None

    def test_decline_draft_estimate_fails(self, estimate):
        assert estimate.status == "Draft"
        response = self._post_decline(estimate.public_token)
        assert response.status_code == 400

    def test_decline_nonexistent_estimate(self):
        response = self._post_decline("bad_token")
        assert response.status_code == 404


@pytest.mark.django_db
class TestPortalTokenRegistration:
    """Creating a token-bearing record registers the unscoped org lookup.

    The lookup is what lets the anonymous portal view resolve the org before
    RLS; if a save does not populate it, that record's link 404s in production.
    """

    def test_creating_estimate_registers_lookup(self, estimate):
        from common.portal_tokens import resolve_portal_org

        assert resolve_portal_org(estimate.public_token, "estimate") == str(
            estimate.org_id
        )

    def test_creating_invoice_registers_lookup(self, invoice):
        from common.portal_tokens import resolve_portal_org

        assert resolve_portal_org(invoice.public_token, "invoice") == str(
            invoice.org_id
        )

    def test_estimate_token_does_not_resolve_as_invoice(self, estimate):
        from common.portal_tokens import resolve_portal_org

        assert resolve_portal_org(estimate.public_token, "invoice") is None


# ---------------------------------------------------------------------------
# Model Methods and Properties
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceModel:
    """Tests for Invoice model methods and properties."""

    def test_str(self, invoice):
        assert str(invoice) == invoice.invoice_number

    def test_invoice_number_generated(self, invoice):
        assert invoice.invoice_number.startswith("INV-")

    def test_public_token_generated(self, invoice):
        assert invoice.public_token != ""
        assert len(invoice.public_token) > 10

    def test_public_url(self, invoice):
        assert invoice.public_url == f"/portal/invoice/{invoice.public_token}"

    def test_is_overdue_true(self, invoice):
        invoice.due_date = timezone.localdate() - datetime.timedelta(days=1)
        invoice.status = "Sent"
        assert invoice.is_overdue is True

    def test_is_overdue_false_paid(self, invoice):
        invoice.due_date = timezone.localdate() - datetime.timedelta(days=1)
        invoice.status = "Paid"
        assert invoice.is_overdue is False

    def test_is_overdue_false_future_date(self, invoice):
        invoice.due_date = timezone.localdate() + datetime.timedelta(days=10)
        invoice.status = "Sent"
        assert invoice.is_overdue is False

    def test_is_overdue_false_no_due_date(self, invoice):
        invoice.due_date = None
        invoice.status = "Sent"
        assert invoice.is_overdue is False

    def test_calculate_due_date_net_30(self, invoice):
        invoice.issue_date = datetime.date(2026, 1, 1)
        invoice.payment_terms = "NET_30"
        invoice.due_date = None
        result = invoice.calculate_due_date()
        assert result == datetime.date(2026, 1, 31)

    def test_calculate_due_date_net_15(self, invoice):
        invoice.issue_date = datetime.date(2026, 1, 1)
        invoice.payment_terms = "NET_15"
        result = invoice.calculate_due_date()
        assert result == datetime.date(2026, 1, 16)

    def test_calculate_due_date_due_on_receipt(self, invoice):
        invoice.issue_date = datetime.date(2026, 1, 1)
        invoice.payment_terms = "DUE_ON_RECEIPT"
        result = invoice.calculate_due_date()
        assert result == datetime.date(2026, 1, 1)

    def test_calculate_due_date_net_60(self, invoice):
        invoice.issue_date = datetime.date(2026, 1, 1)
        invoice.payment_terms = "NET_60"
        result = invoice.calculate_due_date()
        assert result == datetime.date(2026, 3, 2)

    def test_calculate_due_date_no_issue_date(self, invoice):
        invoice.issue_date = None
        result = invoice.calculate_due_date()
        assert result is None

    def test_recalculate_totals_percentage_discount(self, invoice, org_a):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("10"),
            unit_price=Decimal("100"),
            org=org_a,
        )
        invoice.discount_type = "PERCENTAGE"
        invoice.discount_value = Decimal("10")
        invoice.tax_rate = Decimal("5")
        invoice.recalculate_totals()
        # subtotal = 1000, discount = 100, taxable = 900, tax = 45
        assert invoice.subtotal == Decimal("1000.00")
        assert invoice.discount_amount == Decimal("100.00")
        assert invoice.tax_amount == Decimal("45.00")
        assert invoice.total_amount == Decimal("945.00")

    def test_recalculate_totals_fixed_discount(self, invoice, org_a):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("1"),
            unit_price=Decimal("500"),
            org=org_a,
        )
        invoice.discount_type = "FIXED"
        invoice.discount_value = Decimal("50")
        invoice.tax_rate = Decimal("10")
        invoice.recalculate_totals()
        assert invoice.subtotal == Decimal("500.00")
        assert invoice.discount_amount == Decimal("50.00")
        assert invoice.tax_amount == Decimal("45.00")
        assert invoice.total_amount == Decimal("495.00")

    def test_formatted_total_amount(self, invoice):
        invoice.currency = "EUR"
        invoice.total_amount = Decimal("1234.56")
        assert invoice.formatted_total_amount() == "EUR 1234.56"

    def test_formatted_amount_due(self, invoice):
        invoice.currency = "GBP"
        invoice.amount_due = Decimal("999.00")
        assert invoice.formatted_amount_due() == "GBP 999.00"

    def test_created_on_arrow(self, invoice):
        result = invoice.created_on_arrow
        assert "ago" in result


@pytest.mark.django_db
class TestInvoiceLineItemModel:
    """Tests for InvoiceLineItem model methods and properties."""

    def test_str(self, line_item):
        result = str(line_item)
        assert line_item.invoice.invoice_number in result
        assert "Widget A" in result

    def test_str_no_name(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            description="Just a description",
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            org=org_a,
        )
        assert "Just a description" in str(item)

    def test_str_no_name_no_description(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            org=org_a,
        )
        assert "Item" in str(item)

    def test_formatted_unit_price(self, line_item):
        result = line_item.formatted_unit_price
        assert "USD" in result

    def test_formatted_total(self, line_item):
        result = line_item.formatted_total
        assert "USD" in result

    def test_save_calculates_subtotal(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("3"),
            unit_price=Decimal("100"),
            org=org_a,
        )
        assert item.subtotal == Decimal("300.00")

    def test_save_percentage_discount(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("1"),
            unit_price=Decimal("200"),
            discount_type="PERCENTAGE",
            discount_value=Decimal("25"),
            org=org_a,
        )
        assert item.discount_amount == Decimal("50.00")
        assert item.total == Decimal("150.00")

    def test_save_fixed_discount(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("1"),
            unit_price=Decimal("200"),
            discount_type="FIXED",
            discount_value=Decimal("30"),
            org=org_a,
        )
        assert item.discount_amount == Decimal("30.00")
        assert item.total == Decimal("170.00")

    def test_save_with_tax(self, invoice, org_a):
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Item",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            tax_rate=Decimal("10"),
            org=org_a,
        )
        assert item.tax_amount == Decimal("10.00")
        assert item.total == Decimal("110.00")


@pytest.mark.django_db
class TestPaymentModel:
    """Tests for Payment model methods."""

    def test_str(self, payment):
        result = str(payment)
        assert payment.invoice.invoice_number in result

    def _make_invoice_with_total(self, invoice, org_a, total):
        """Add a line item to give the invoice a real total_amount."""
        InvoiceLineItem.objects.create(
            invoice=invoice,
            name="Service",
            quantity=Decimal("1"),
            unit_price=total,
            org=org_a,
        )
        invoice.recalculate_totals()
        invoice.save()
        invoice.refresh_from_db()

    def test_payment_updates_invoice(self, invoice, org_a):
        self._make_invoice_with_total(invoice, org_a, Decimal("1000.00"))
        Payment.objects.create(
            invoice=invoice,
            amount=Decimal("400.00"),
            payment_date=timezone.localdate(),
            payment_method="CASH",
            org=org_a,
        )
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("400.00")
        assert invoice.status == "Partially_Paid"

    def test_full_payment_marks_paid(self, invoice, org_a):
        self._make_invoice_with_total(invoice, org_a, Decimal("500.00"))
        Payment.objects.create(
            invoice=invoice,
            amount=Decimal("500.00"),
            payment_date=timezone.localdate(),
            payment_method="BANK_TRANSFER",
            org=org_a,
        )
        invoice.refresh_from_db()
        assert invoice.status == "Paid"
        assert invoice.paid_at is not None

    def test_delete_payment_recalculates(self, invoice, org_a):
        self._make_invoice_with_total(invoice, org_a, Decimal("500.00"))
        p = Payment.objects.create(
            invoice=invoice,
            amount=Decimal("500.00"),
            payment_date=timezone.localdate(),
            payment_method="CASH",
            org=org_a,
        )
        invoice.refresh_from_db()
        assert invoice.status == "Paid"
        p.delete()
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0")

    def test_payment_inherits_org_from_invoice(self, invoice):
        p = Payment.objects.create(
            invoice=invoice,
            amount=Decimal("50.00"),
            payment_date=timezone.localdate(),
            payment_method="CASH",
            org=invoice.org,
        )
        assert p.org_id == invoice.org_id


@pytest.mark.django_db
class TestEstimateModel:
    """Tests for Estimate model methods and properties."""

    def test_str(self, estimate):
        assert str(estimate) == estimate.estimate_number

    def test_estimate_number_generated(self, estimate):
        assert estimate.estimate_number.startswith("EST-")

    def test_public_token_generated(self, estimate):
        assert estimate.public_token != ""

    def test_public_url(self, estimate):
        assert estimate.public_url == f"/portal/estimate/{estimate.public_token}"

    def test_is_expired_true(self, estimate):
        estimate.expiry_date = timezone.localdate() - datetime.timedelta(days=1)
        estimate.status = "Sent"
        assert estimate.is_expired is True

    def test_is_expired_false_accepted(self, estimate):
        estimate.expiry_date = timezone.localdate() - datetime.timedelta(days=1)
        estimate.status = "Accepted"
        assert estimate.is_expired is False

    def test_is_expired_false_future(self, estimate):
        estimate.expiry_date = timezone.localdate() + datetime.timedelta(days=10)
        estimate.status = "Sent"
        assert estimate.is_expired is False

    def test_is_expired_false_no_expiry(self, estimate):
        estimate.expiry_date = None
        assert estimate.is_expired is False

    def test_recalculate_totals(self, estimate, org_a):
        EstimateLineItem.objects.create(
            estimate=estimate,
            name="Item",
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
            org=org_a,
        )
        estimate.discount_type = "PERCENTAGE"
        estimate.discount_value = Decimal("10")
        estimate.tax_rate = Decimal("5")
        estimate.recalculate_totals()
        assert estimate.subtotal == Decimal("200.00")
        assert estimate.discount_amount == Decimal("20.00")
        assert estimate.tax_amount == Decimal("9.00")
        assert estimate.total_amount == Decimal("189.00")


@pytest.mark.django_db
class TestEstimateLineItemModel:
    """Tests for EstimateLineItem model."""

    def test_str(self, estimate, org_a):
        item = EstimateLineItem.objects.create(
            estimate=estimate,
            name="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            org=org_a,
        )
        result = str(item)
        assert estimate.estimate_number in result
        assert "Service" in result

    def test_save_calculates_totals(self, estimate, org_a):
        item = EstimateLineItem.objects.create(
            estimate=estimate,
            name="Widget",
            quantity=Decimal("5"),
            unit_price=Decimal("10"),
            discount_type="PERCENTAGE",
            discount_value=Decimal("20"),
            tax_rate=Decimal("10"),
            org=org_a,
        )
        # subtotal = 50, discount = 10, taxable = 40, tax = 4
        assert item.subtotal == Decimal("50.00")
        assert item.discount_amount == Decimal("10.00")
        assert item.tax_amount == Decimal("4.00")
        assert item.total == Decimal("44.00")

    def test_save_fixed_discount(self, estimate, org_a):
        item = EstimateLineItem.objects.create(
            estimate=estimate,
            name="Widget",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            discount_type="FIXED",
            discount_value=Decimal("15"),
            org=org_a,
        )
        assert item.discount_amount == Decimal("15.00")
        assert item.total == Decimal("85.00")


@pytest.mark.django_db
class TestRecurringInvoiceModel:
    """Tests for RecurringInvoice model methods."""

    def test_str(self, recurring_invoice):
        result = str(recurring_invoice)
        assert "Monthly Hosting" in result
        assert "MONTHLY" in result

    def test_calculate_next_date_weekly(self, recurring_invoice):
        recurring_invoice.frequency = "WEEKLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 1, 8)

    def test_calculate_next_date_biweekly(self, recurring_invoice):
        recurring_invoice.frequency = "BIWEEKLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 1, 15)

    def test_calculate_next_date_monthly(self, recurring_invoice):
        recurring_invoice.frequency = "MONTHLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 15)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 2, 15)

    def test_calculate_next_date_quarterly(self, recurring_invoice):
        recurring_invoice.frequency = "QUARTERLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 4, 1)

    def test_calculate_next_date_semi_annually(self, recurring_invoice):
        recurring_invoice.frequency = "SEMI_ANNUALLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 7, 1)

    def test_calculate_next_date_yearly(self, recurring_invoice):
        recurring_invoice.frequency = "YEARLY"
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2027, 1, 1)

    def test_calculate_next_date_custom(self, recurring_invoice):
        recurring_invoice.frequency = "CUSTOM"
        recurring_invoice.custom_days = 10
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 1, 11)

    def test_calculate_next_date_custom_no_days(self, recurring_invoice):
        """CUSTOM without custom_days falls back to monthly."""
        recurring_invoice.frequency = "CUSTOM"
        recurring_invoice.custom_days = None
        recurring_invoice.next_generation_date = datetime.date(2026, 1, 1)
        result = recurring_invoice.calculate_next_date()
        assert result == datetime.date(2026, 2, 1)


@pytest.mark.django_db
class TestRecurringInvoiceLineItemModel:
    """Tests for RecurringInvoiceLineItem model."""

    def test_str(self, recurring_invoice, org_a):
        item = RecurringInvoiceLineItem.objects.create(
            recurring_invoice=recurring_invoice,
            description="Monthly Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            org=org_a,
        )
        result = str(item)
        assert "Monthly Hosting" in result

    def test_inherits_org(self, recurring_invoice):
        item = RecurringInvoiceLineItem.objects.create(
            recurring_invoice=recurring_invoice,
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("50"),
            org=recurring_invoice.org,
        )
        assert item.org_id == recurring_invoice.org_id


@pytest.mark.django_db
class TestProductModel:
    """Tests for Product model."""

    def test_str(self, product):
        assert str(product) == "Test Product"


@pytest.mark.django_db
class TestInvoiceTemplateModel:
    """Tests for InvoiceTemplate model methods."""

    def test_str(self, template):
        assert str(template) == "Default Template"

    def test_default_template_uniqueness(self, org_a):
        """Setting a new template as default should unset the previous default."""
        t1 = InvoiceTemplate.objects.create(
            name="Template 1", is_default=True, org=org_a
        )
        t2 = InvoiceTemplate.objects.create(
            name="Template 2", is_default=True, org=org_a
        )
        t1.refresh_from_db()
        assert t1.is_default is False
        assert t2.is_default is True

    def test_non_default_save(self, org_a):
        t = InvoiceTemplate.objects.create(
            name="Non-Default", is_default=False, org=org_a
        )
        assert t.is_default is False


@pytest.mark.django_db
class TestInvoiceHistoryModel:
    """Tests for InvoiceHistory model."""

    def test_str(self, invoice, org_a):
        history = InvoiceHistory.objects.create(
            invoice=invoice,
            invoice_title=invoice.invoice_title,
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            org=org_a,
        )
        result = str(history)
        assert invoice.invoice_number in result

    def test_created_on_arrow(self, invoice, org_a):
        history = InvoiceHistory.objects.create(
            invoice=invoice,
            invoice_title=invoice.invoice_title,
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            org=org_a,
        )
        assert "ago" in history.created_on_arrow

    def test_inherits_org(self, invoice):
        history = InvoiceHistory.objects.create(
            invoice=invoice,
            invoice_title=invoice.invoice_title,
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            org=invoice.org,
        )
        assert history.org_id == invoice.org_id


# ---------------------------------------------------------------------------
# Regular User Visibility (non-admin queryset filtering)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegularUserVisibility:
    """Tests for non-admin user queryset restrictions."""

    def test_regular_user_sees_no_invoices_by_default(self, user_client, invoice):
        """Regular user should not see invoices they didn't create or aren't assigned to."""
        response = user_client.get("/api/invoices/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_regular_user_sees_assigned_invoice(
        self, user_client, user_profile, invoice
    ):
        invoice.assigned_to.add(user_profile)
        response = user_client.get("/api/invoices/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_admin_user_sees_all_estimates(self, admin_client, estimate):
        """Admin users should see all estimates regardless of assignment."""
        response = admin_client.get("/api/invoices/estimates/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
