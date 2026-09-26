"""
Object-level authorization tests for invoice-scoped API endpoints.

Regression coverage for GitHub issue #698: several invoice endpoints checked
only authentication + org membership, letting any same-org user mutate, read,
duplicate or send invoices they neither created nor were assigned to.

The attacker in every test below is ``user_client`` -- an authenticated,
non-admin, non-superuser profile in the *same* org as the target invoice, who
is not the creator and not in ``assigned_to``.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import Account
from common.models import Attachments
from invoices.models import Invoice, InvoiceLineItem, Payment

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_a(org_a):
    return Account.objects.create(name="Perm Test Account", org=org_a)


def _make_invoice(title, account, org, creator):
    """Build a 100.00 invoice owned by ``creator`` (a User).

        ``created_by`` must be set with a queryset update: BaseModel.save() resets it
        from the thread-local current user, which is None in fixtures.
    from django.utils import timezone
    """
    invoice = Invoice.objects.create(
        invoice_title=title,
        account=account,
        currency="USD",
        org=org,
    )
    InvoiceLineItem.objects.create(
        invoice=invoice,
        name="Widget",
        quantity=Decimal("2"),
        unit_price=Decimal("50.00"),
        org=org,
    )
    invoice.recalculate_totals()
    invoice.save()
    Invoice.objects.filter(id=invoice.id).update(created_by=creator)
    invoice.refresh_from_db()
    return invoice


@pytest.fixture
def foreign_invoice(account_a, org_a, admin_user):
    """An invoice owned by admin_user -- regular_user has no claim to it."""
    return _make_invoice("Victim Invoice", account_a, org_a, admin_user)


@pytest.fixture
def foreign_line_item(foreign_invoice):
    return foreign_invoice.line_items.first()


@pytest.fixture
def own_invoice(account_a, org_a, regular_user):
    """An invoice created by regular_user -- they are allowed to act on it."""
    return _make_invoice("Own Invoice", account_a, org_a, regular_user)


@pytest.fixture
def assigned_invoice(foreign_invoice, user_profile):
    """Created by admin_user but assigned to user_profile."""
    foreign_invoice.assigned_to.add(user_profile)
    return foreign_invoice


# ---------------------------------------------------------------------------
# Issue #698: the three reported endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportedEndpointsRejectNonOwner:
    """The endpoints named in issue #698 must enforce object-level access."""

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_send_denied_for_non_owner(self, mock_send, user_client, foreign_invoice):
        response = user_client.post(f"/api/invoices/{foreign_invoice.id}/send/")
        assert response.status_code == 404
        foreign_invoice.refresh_from_db()
        assert foreign_invoice.is_email_sent is False
        assert foreign_invoice.sent_at is None
        mock_send.assert_not_called()

    def test_mark_paid_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.post(
            f"/api/invoices/{foreign_invoice.id}/mark-paid/",
            {
                "amount": "999999.99",
                "payment_method": "CASH",
                "payment_date": "2026-06-13",
            },
            format="json",
        )
        assert response.status_code == 404
        assert Payment.objects.filter(invoice=foreign_invoice).count() == 0
        foreign_invoice.refresh_from_db()
        assert foreign_invoice.status != "Paid"

    def test_duplicate_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.post(f"/api/invoices/{foreign_invoice.id}/duplicate/")
        assert response.status_code == 404
        assert Invoice.objects.filter(invoice_title__startswith="Copy of").count() == 0


# ---------------------------------------------------------------------------
# Same class of bug on adjacent invoice-scoped endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLineItemEndpointsRejectNonOwner:
    def test_list_line_items_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.get(f"/api/invoices/{foreign_invoice.id}/line-items/")
        assert response.status_code == 404

    def test_create_line_item_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.post(
            f"/api/invoices/{foreign_invoice.id}/line-items/",
            {"name": "Injected", "quantity": "1", "unit_price": "10.00"},
            format="json",
        )
        assert response.status_code == 404
        assert not foreign_invoice.line_items.filter(name="Injected").exists()

    def test_update_line_item_denied_for_non_owner(
        self, user_client, foreign_invoice, foreign_line_item
    ):
        response = user_client.put(
            f"/api/invoices/{foreign_invoice.id}/line-items/{foreign_line_item.id}/",
            {"name": "Tampered"},
            format="json",
        )
        assert response.status_code == 404
        foreign_line_item.refresh_from_db()
        assert foreign_line_item.name == "Widget"

    def test_delete_line_item_denied_for_non_owner(
        self, user_client, foreign_invoice, foreign_line_item
    ):
        response = user_client.delete(
            f"/api/invoices/{foreign_invoice.id}/line-items/{foreign_line_item.id}/"
        )
        assert response.status_code == 404
        assert InvoiceLineItem.objects.filter(id=foreign_line_item.id).exists()


@pytest.mark.django_db
class TestPaymentEndpointsRejectNonOwner:
    def test_list_payments_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.get(f"/api/invoices/{foreign_invoice.id}/payments/")
        assert response.status_code == 404

    def test_create_payment_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.post(
            f"/api/invoices/{foreign_invoice.id}/payments/",
            {
                "amount": "100.00",
                "payment_date": "2026-06-13",
                "payment_method": "CASH",
            },
            format="json",
        )
        assert response.status_code == 404
        assert Payment.objects.filter(invoice=foreign_invoice).count() == 0


@pytest.mark.django_db
class TestCommentAndAttachmentRejectNonOwner:
    def test_create_comment_denied_for_non_owner(self, user_client, foreign_invoice):
        response = user_client.post(
            f"/api/invoices/{foreign_invoice.id}/comments/",
            {"comment": "injected"},
            format="json",
        )
        assert response.status_code == 404

    def test_create_attachment_denied_for_non_owner(self, user_client, foreign_invoice):
        upload = SimpleUploadedFile("evil.txt", b"payload", content_type="text/plain")
        response = user_client.post(
            f"/api/invoices/{foreign_invoice.id}/attachments/",
            {"file": upload},
            format="multipart",
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestAttachmentRoundTrip:
    """Upload then delete must work for the same non-admin user."""

    def test_uploader_can_delete_own_attachment(self, user_client, own_invoice):
        upload = SimpleUploadedFile("notes.txt", b"payload", content_type="text/plain")
        created = user_client.post(
            f"/api/invoices/{own_invoice.id}/attachments/",
            {"file": upload},
            format="multipart",
        )
        assert created.status_code == 201
        attachment_id = created.json()["attachment"]["id"]

        deleted = user_client.delete(f"/api/invoices/attachments/{attachment_id}/")
        assert deleted.status_code == 200
        assert not Attachments.objects.filter(id=attachment_id).exists()

    def test_non_uploader_cannot_delete_attachment(
        self, user_client, admin_client, own_invoice
    ):
        upload = SimpleUploadedFile("admin.txt", b"payload", content_type="text/plain")
        created = admin_client.post(
            f"/api/invoices/{own_invoice.id}/attachments/",
            {"file": upload},
            format="multipart",
        )
        assert created.status_code == 201
        attachment_id = created.json()["attachment"]["id"]

        response = user_client.delete(f"/api/invoices/attachments/{attachment_id}/")
        assert response.status_code == 403
        assert Attachments.objects.filter(id=attachment_id).exists()


# ---------------------------------------------------------------------------
# Positive controls -- the fix must not lock out legitimate users
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuthorizedUsersStillAllowed:
    def test_creator_can_mark_paid(self, user_client, own_invoice):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {
                "amount": "100.00",
                "payment_method": "CASH",
                "payment_date": "2026-06-13",
            },
            format="json",
        )
        assert response.status_code == 200
        assert Payment.objects.filter(invoice=own_invoice).count() == 1

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_assignee_can_send(self, mock_send, user_client, assigned_invoice):
        response = user_client.post(f"/api/invoices/{assigned_invoice.id}/send/")
        assert response.status_code == 200
        assigned_invoice.refresh_from_db()
        assert assigned_invoice.status == "Sent"

    def test_admin_can_duplicate_any_invoice(self, admin_client, own_invoice):
        response = admin_client.post(f"/api/invoices/{own_invoice.id}/duplicate/")
        assert response.status_code == 201

    def test_creator_can_list_line_items(self, user_client, own_invoice):
        response = user_client.get(f"/api/invoices/{own_invoice.id}/line-items/")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_creator_can_comment(self, user_client, own_invoice):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/comments/",
            {"comment": "looks good"},
            format="json",
        )
        assert response.status_code == 201


# ---------------------------------------------------------------------------
# Cross-org isolation must still report 404, not 403
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossOrgStillNotFound:
    """A different org must not learn that the invoice exists."""

    def test_other_org_gets_404_not_403(self, org_b_client, foreign_invoice):
        response = org_b_client.post(f"/api/invoices/{foreign_invoice.id}/mark-paid/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Payment amount validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPaymentAmountValidation:
    """Amount must be positive and must not exceed the outstanding balance."""

    def test_mark_paid_rejects_amount_above_amount_due(self, user_client, own_invoice):
        assert own_invoice.amount_due == Decimal("100.00")
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {
                "amount": "999999.99",
                "payment_method": "CASH",
                "payment_date": "2026-06-13",
            },
            format="json",
        )
        assert response.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 0
        own_invoice.refresh_from_db()
        assert own_invoice.status != "Paid"

    def test_mark_paid_rejects_zero(self, user_client, own_invoice):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {"amount": "0", "payment_method": "CASH", "payment_date": "2026-06-13"},
            format="json",
        )
        assert response.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 0

    def test_mark_paid_rejects_negative(self, user_client, own_invoice):
        """A negative payment would walk amount_paid back down via the SUM."""
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {
                "amount": "-50.00",
                "payment_method": "CASH",
                "payment_date": "2026-06-13",
            },
            format="json",
        )
        assert response.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 0

    def test_mark_paid_accepts_exact_amount_due(self, user_client, own_invoice):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {
                "amount": "100.00",
                "payment_method": "CASH",
                "payment_date": "2026-06-13",
            },
            format="json",
        )
        assert response.status_code == 200
        own_invoice.refresh_from_db()
        assert own_invoice.status == "Paid"

    def test_partial_payments_cannot_exceed_remaining_balance(
        self, user_client, own_invoice
    ):
        """Second payment is capped by the balance left after the first."""
        first = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {"amount": "60.00", "payment_method": "CASH", "payment_date": "2026-06-13"},
            format="json",
        )
        assert first.status_code == 200

        second = user_client.post(
            f"/api/invoices/{own_invoice.id}/mark-paid/",
            {"amount": "60.00", "payment_method": "CASH", "payment_date": "2026-06-14"},
            format="json",
        )
        assert second.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 1

    def test_payments_endpoint_rejects_amount_above_amount_due(
        self, user_client, own_invoice
    ):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/payments/",
            {
                "amount": "999999.99",
                "payment_date": "2026-06-13",
                "payment_method": "CASH",
            },
            format="json",
        )
        assert response.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 0

    def test_payments_endpoint_rejects_negative(self, user_client, own_invoice):
        response = user_client.post(
            f"/api/invoices/{own_invoice.id}/payments/",
            {
                "amount": "-50.00",
                "payment_date": "2026-06-13",
                "payment_method": "CASH",
            },
            format="json",
        )
        assert response.status_code == 400
        assert Payment.objects.filter(invoice=own_invoice).count() == 0


# ---------------------------------------------------------------------------
# Send lifecycle validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendLifecycle:
    """Send must be restricted to invoices in a sendable state."""

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_cannot_send_cancelled_invoice(self, mock_send, user_client, own_invoice):
        own_invoice.status = "Cancelled"
        own_invoice.save(update_fields=["status"])

        response = user_client.post(f"/api/invoices/{own_invoice.id}/send/")
        assert response.status_code == 400
        mock_send.assert_not_called()
        own_invoice.refresh_from_db()
        assert own_invoice.is_email_sent is False

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_cannot_send_paid_invoice(self, mock_send, user_client, own_invoice):
        Payment.objects.create(
            invoice=own_invoice,
            amount=Decimal("100.00"),
            payment_date=timezone.localdate(),
            payment_method="CASH",
            org=own_invoice.org,
        )
        own_invoice.refresh_from_db()
        assert own_invoice.status == "Paid"

        response = user_client.post(f"/api/invoices/{own_invoice.id}/send/")
        assert response.status_code == 400
        mock_send.assert_not_called()

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_can_send_draft_invoice(self, mock_send, user_client, own_invoice):
        assert own_invoice.status == "Draft"
        response = user_client.post(f"/api/invoices/{own_invoice.id}/send/")
        assert response.status_code == 200
        own_invoice.refresh_from_db()
        assert own_invoice.status == "Sent"
        mock_send.assert_called_once()

    @patch("invoices.api_views.send_invoice_to_client.delay")
    def test_can_resend_already_sent_invoice(self, mock_send, user_client, own_invoice):
        """Re-sending an unpaid, uncancelled invoice is a legitimate reminder."""
        own_invoice.status = "Sent"
        own_invoice.save(update_fields=["status"])

        response = user_client.post(f"/api/invoices/{own_invoice.id}/send/")
        assert response.status_code == 200
        mock_send.assert_called_once()
