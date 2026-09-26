"""Marking an invoice paid records one payment, however many clicks arrive.

`InvoiceMarkPaidView` read the invoice unlocked and outside a transaction, so
two requests at once both saw the full balance, both passed the amount-due
check, and each recorded a payment: amount_due went to -100. It now reads the
row under `select_for_update()` inside the request's transaction, as
`PaymentListView.post` does, so the second request waits, then sees nothing
owed and is refused.
"""

import threading
import time
from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection

from common.testing import _make_authenticated_client
from invoices import api_views
from invoices.models import Invoice, Payment
from invoices.serializer import PaymentCreateSerializer


def _owed(org, amount="100.00"):
    invoice = Invoice.objects.create(
        org=org, invoice_title="Work", client_email="a@b.co", status="Sent"
    )
    Invoice.objects.filter(pk=invoice.pk).update(
        total_amount=Decimal(amount), amount_due=Decimal(amount)
    )
    return invoice


def _mark_paid(client, invoice):
    return client.post(f"/api/invoices/{invoice.id}/mark-paid/", {}, format="json")


@pytest.mark.django_db
class TestMarkPaid:
    def test_reads_a_locked_row(self, admin_client, org_a):
        invoice = _owed(org_a)
        real = api_views.get_invoice_or_error
        locked = []

        def spy(request, pk, queryset=None):
            locked.append(queryset is not None and queryset.query.select_for_update)
            return real(request, pk, queryset)

        with mock.patch.object(api_views, "get_invoice_or_error", spy):
            response = _mark_paid(admin_client, invoice)

        assert response.status_code == 200, response.content
        assert locked == [True]

    def test_settles_the_balance_once(self, admin_client, org_a):
        invoice = _owed(org_a)

        first = _mark_paid(admin_client, invoice)
        second = _mark_paid(admin_client, invoice)

        assert first.status_code == 200, first.content
        assert second.status_code == 400
        invoice.refresh_from_db()
        assert invoice.status == "Paid"
        assert invoice.amount_due == Decimal("0.00")
        assert Payment.objects.filter(invoice=invoice).count() == 1

    def test_refuses_a_cancelled_invoice(self, admin_client, org_a):
        invoice = _owed(org_a)
        Invoice.objects.filter(pk=invoice.pk).update(status="Cancelled")

        response = _mark_paid(admin_client, invoice)

        assert response.status_code == 400
        assert not Payment.objects.exists()


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
def test_two_mark_paid_calls_at_once_record_one_payment(
    admin_user, org_a, admin_profile
):
    """Both requests start before either commits. The second waits on the
    invoice's row lock, then reads nothing owed and is refused. Without the
    lock both read 100.00 due and each recorded a payment."""
    if connection.vendor != "postgresql":
        pytest.skip("row locks need PostgreSQL")

    invoice = _owed(org_a)
    barrier = threading.Barrier(2)
    statuses, errors = [], []
    real_save = PaymentCreateSerializer.save

    def slow_save(self, **kwargs):
        time.sleep(0.5)
        return real_save(self, **kwargs)

    def worker():
        try:
            client = _make_authenticated_client(admin_user, org_a, admin_profile)
            barrier.wait()
            statuses.append(_mark_paid(client, invoice).status_code)
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with mock.patch.object(PaymentCreateSerializer, "save", slow_save):
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert sorted(statuses) == [200, 400]
    invoice.refresh_from_db()
    assert invoice.amount_due == Decimal("0.00")
    assert Payment.objects.filter(invoice=invoice).count() == 1
