"""System saves of an issued document cannot write back over a concurrent edit.

The portal's first view, public accept and decline, both sends, the overdue
and expiry sweeps, payment reminders, recurring generation and payment
recording all used to load a document, do their work, and full-save the copy
they loaded. Anything committed in between (a staff edit, a payment, a
cancel, an acceptance) was written back over, and a status check made on the
old copy could move a Paid or Accepted document back to an earlier state.

Each now writes only its own fields, and re-reads the row under
`select_for_update()` wherever it judges a status or bumps a counter.
Recurring generation claims each schedule under a row lock and advances it in
the transaction that creates the invoice, so overlapping runs bill once. SQLite
has no row locks, so the tests here make the concurrent change land in the
window the old code had (between the read and the write) and check it
survives; the PostgreSQL-only test at the end proves the lock is asked for.
"""

import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from invoices import api_views
from invoices.models import (
    Estimate,
    Invoice,
    Payment,
    RecurringInvoice,
    RecurringInvoiceLineItem,
)
from invoices.tasks import (
    check_expired_estimates,
    check_overdue_invoices,
    generate_recurring_invoices,
    send_estimate_to_client,
    send_invoice_to_client,
    send_payment_reminder,
)

EDITED = "Edited while the system save was in flight"
TODAY = datetime.date.today


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
        patch("invoices.tasks.send_invoice_to_client.delay"),
        patch("invoices.tasks.send_payment_reminder.delay"),
    ):
        yield


def _invoice(org, **fields):
    return Invoice.objects.create(
        org=org,
        invoice_title="Work",
        client_email="buyer@example.com",
        **fields,
    )


def _estimate(org, **fields):
    return Estimate.objects.create(
        org=org,
        title="Quote",
        client_email="buyer@example.com",
        expiry_date=TODAY() + datetime.timedelta(days=30),
        **fields,
    )


def _meanwhile(obj, **changes):
    """The concurrent commit: a queryset update, so nothing in memory moves."""
    type(obj).objects.filter(pk=obj.pk).update(**changes)


@contextmanager
def _after_first_read(model, **changes):
    """Commit `changes` to the first `model` row a `.first()` returns, right
    after it is read. Records whether each such read asked for a row lock."""
    real_first = QuerySet.first
    locked = []

    def first(qs):
        obj = real_first(qs)
        if obj is not None and qs.model is model:
            locked.append(bool(qs.query.select_for_update))
            if len(locked) == 1:
                _meanwhile(obj, **changes)
        return obj

    with patch.object(QuerySet, "first", first):
        yield locked


@contextmanager
def _on_first_save(model, action):
    """Run `action(instance)` once, when the first `model` row is saved."""
    fired = []

    def receiver(sender, instance, **kwargs):
        if not fired:
            fired.append(instance.pk)
            action(instance)

    post_save.connect(receiver, sender=model, dispatch_uid="clobber-test")
    try:
        yield fired
    finally:
        post_save.disconnect(sender=model, dispatch_uid="clobber-test")


def _send_meanwhile(obj, **changes):
    """Commit `changes` while the task's email is going out."""

    def send(message, *args, **kwargs):
        _meanwhile(obj, **changes)
        return 1

    return patch("invoices.tasks.EmailMessage.send", send)


# --------------------------------------------------------------------------
# Portal: first view, accept, decline
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestPortalFirstView:
    def test_invoice_first_view_marks_viewed(self, org_a):
        invoice = _invoice(org_a, status="Sent")

        response = APIClient().get(f"/api/public/invoice/{invoice.public_token}/")

        assert response.status_code == 200, response.content
        invoice.refresh_from_db()
        assert invoice.status == "Viewed"
        assert invoice.viewed_at is not None

    def test_invoice_first_view_keeps_an_edit_and_a_payment_made_meanwhile(self, org_a):
        invoice = _invoice(org_a, status="Sent")

        with _after_first_read(Invoice, notes=EDITED, status="Paid"):
            response = APIClient().get(f"/api/public/invoice/{invoice.public_token}/")

        assert response.status_code == 200, response.content
        assert response.json()["status"] == "Paid"
        invoice.refresh_from_db()
        assert invoice.notes == EDITED
        assert invoice.status == "Paid"
        assert invoice.viewed_at is not None

    def test_estimate_first_view_marks_viewed(self, org_a):
        estimate = _estimate(org_a, status="Sent")

        APIClient().get(f"/api/public/estimate/{estimate.public_token}/")

        estimate.refresh_from_db()
        assert estimate.status == "Viewed"
        assert estimate.viewed_at is not None

    def test_estimate_first_view_keeps_an_acceptance_made_meanwhile(self, org_a):
        estimate = _estimate(org_a, status="Sent")

        with _after_first_read(Estimate, notes=EDITED, status="Accepted"):
            APIClient().get(f"/api/public/estimate/{estimate.public_token}/")

        estimate.refresh_from_db()
        assert estimate.notes == EDITED
        assert estimate.status == "Accepted"
        assert estimate.viewed_at is not None


@pytest.mark.django_db
class TestPortalAcceptDecline:
    BODY = {"name": "Dana Buyer", "email": "dana@buyer.example"}

    def test_accept_reads_a_locked_row_and_keeps_an_edit(self, org_a):
        estimate = _estimate(org_a, status="Sent")

        with _after_first_read(Estimate, notes=EDITED) as locked:
            response = APIClient().post(
                f"/api/public/estimate/{estimate.public_token}/accept/",
                self.BODY,
                format="json",
            )

        assert response.status_code == 200, response.content
        assert locked == [True]
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"
        assert estimate.accepted_by_name == "Dana Buyer"
        assert estimate.accepted_at is not None
        assert estimate.notes == EDITED

    def test_accept_still_refuses_a_settled_estimate(self, org_a):
        estimate = _estimate(org_a, status="Declined")

        response = APIClient().post(
            f"/api/public/estimate/{estimate.public_token}/accept/",
            self.BODY,
            format="json",
        )

        assert response.status_code == 400
        estimate.refresh_from_db()
        assert estimate.status == "Declined"

    def test_decline_reads_a_locked_row_and_keeps_an_edit(self, org_a):
        estimate = _estimate(org_a, status="Viewed")

        with _after_first_read(Estimate, notes=EDITED) as locked:
            response = APIClient().post(
                f"/api/public/estimate/{estimate.public_token}/decline/"
            )

        assert response.status_code == 200, response.content
        assert locked == [True]
        estimate.refresh_from_db()
        assert estimate.status == "Declined"
        assert estimate.declined_at is not None
        assert estimate.notes == EDITED

    def test_decline_still_refuses_an_accepted_estimate(self, org_a):
        estimate = _estimate(org_a, status="Accepted")

        response = APIClient().post(
            f"/api/public/estimate/{estimate.public_token}/decline/"
        )

        assert response.status_code == 400
        estimate.refresh_from_db()
        assert estimate.status == "Accepted"


# --------------------------------------------------------------------------
# Celery: sends and reminders
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestSends:
    def test_invoice_send_keeps_an_edit_made_while_mailing(self, org_a):
        invoice = _invoice(org_a, status="Draft")

        with _send_meanwhile(invoice, notes=EDITED):
            send_invoice_to_client(str(invoice.id), str(org_a.id), include_pdf=False)

        invoice.refresh_from_db()
        assert invoice.notes == EDITED
        assert invoice.status == "Sent"
        assert invoice.is_email_sent is True
        assert invoice.sent_at is not None

    def test_invoice_send_does_not_move_a_paid_invoice_back_to_sent(self, org_a):
        invoice = _invoice(org_a, status="Draft")

        with _send_meanwhile(invoice, status="Paid"):
            send_invoice_to_client(str(invoice.id), str(org_a.id), include_pdf=False)

        invoice.refresh_from_db()
        assert invoice.status == "Paid"

    def test_estimate_send_keeps_an_edit_and_an_acceptance(self, org_a):
        estimate = _estimate(org_a, status="Draft")

        with _send_meanwhile(estimate, notes=EDITED, status="Accepted"):
            send_estimate_to_client(str(estimate.id), str(org_a.id), include_pdf=False)

        estimate.refresh_from_db()
        assert estimate.notes == EDITED
        assert estimate.status == "Accepted"
        assert estimate.sent_at is not None

    def test_estimate_send_marks_a_draft_sent(self, org_a):
        estimate = _estimate(org_a, status="Draft")

        send_estimate_to_client(str(estimate.id), str(org_a.id), include_pdf=False)

        estimate.refresh_from_db()
        assert estimate.status == "Sent"

    def test_reminder_counts_from_the_stored_count_and_keeps_an_edit(self, org_a):
        invoice = _invoice(org_a, status="Sent")

        with _send_meanwhile(invoice, notes=EDITED, reminder_count=5):
            send_payment_reminder(str(invoice.id), str(org_a.id))

        invoice.refresh_from_db()
        assert invoice.notes == EDITED
        assert invoice.reminder_count == 6
        assert invoice.last_reminder_sent is not None


# --------------------------------------------------------------------------
# Celery: sweeps
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestOverdueSweep:
    def test_an_edit_or_payment_after_the_sweep_read_survives(self, org_a):
        past = TODAY() - datetime.timedelta(days=5)
        edited = _invoice(org_a, status="Sent", due_date=past)
        paid = _invoice(org_a, status="Viewed", due_date=past)
        extended = _invoice(org_a, status="Sent", due_date=past)
        first = _invoice(org_a, status="Sent", due_date=past)
        # The sweep reads newest first, so `first` is saved before the others.
        _meanwhile(first, created_at=timezone.now() + datetime.timedelta(hours=1))

        def meanwhile(instance):
            _meanwhile(edited, notes=EDITED)
            _meanwhile(paid, status="Paid")
            _meanwhile(extended, due_date=TODAY() + datetime.timedelta(days=5))

        with _on_first_save(Invoice, meanwhile) as fired:
            check_overdue_invoices()

        assert fired == [first.pk]
        for invoice in (edited, paid, extended, first):
            invoice.refresh_from_db()
        assert first.status == "Overdue"
        assert edited.status == "Overdue"
        assert edited.notes == EDITED
        assert paid.status == "Paid"
        assert extended.status == "Sent"


@pytest.mark.django_db
class TestExpirySweep:
    def test_an_edit_or_acceptance_after_the_sweep_read_survives(self, org_a):
        past = TODAY() - datetime.timedelta(days=1)
        edited = _estimate(org_a, status="Sent")
        accepted = _estimate(org_a, status="Viewed")
        extended = _estimate(org_a, status="Sent")
        first = _estimate(org_a, status="Sent")
        for estimate in (edited, accepted, extended, first):
            _meanwhile(estimate, expiry_date=past)
        # The sweep reads newest first, so `first` is saved before the others.
        _meanwhile(first, created_at=timezone.now() + datetime.timedelta(hours=1))

        def meanwhile(instance):
            _meanwhile(edited, notes=EDITED)
            _meanwhile(accepted, status="Accepted")
            _meanwhile(extended, expiry_date=TODAY() + datetime.timedelta(days=5))

        with _on_first_save(Estimate, meanwhile) as fired:
            check_expired_estimates()

        assert fired == [first.pk]
        for estimate in (edited, accepted, extended, first):
            estimate.refresh_from_db()
        assert first.status == "Expired"
        assert edited.status == "Expired"
        assert edited.notes == EDITED
        assert accepted.status == "Accepted"
        assert extended.status == "Sent"


# --------------------------------------------------------------------------
# Celery: recurring generation
# --------------------------------------------------------------------------


def _schedule(org, **fields):
    return RecurringInvoice.objects.create(
        org=org,
        title="Monthly",
        frequency="MONTHLY",
        next_generation_date=TODAY(),
        **fields,
    )


@pytest.mark.django_db
class TestRecurringGeneration:
    def test_a_due_schedule_generates_once_and_advances(self, org_a):
        schedule = _schedule(org_a)
        RecurringInvoiceLineItem.objects.create(
            recurring_invoice=schedule,
            org=org_a,
            name="Hosting",
            quantity=1,
            unit_price=Decimal("50"),
        )

        generate_recurring_invoices()
        generate_recurring_invoices()

        schedule.refresh_from_db()
        assert Invoice.objects.count() == 1
        assert Invoice.objects.get().total_amount == Decimal("50")
        assert schedule.invoices_generated == 1
        assert schedule.next_generation_date > TODAY()

    def test_an_overlapping_run_that_listed_it_first_creates_nothing(self, org_a):
        """Run B lists the schedule as due, run A generates and advances it,
        then B reaches it. B must re-read it advanced and bill nothing."""
        schedule = _schedule(org_a)
        real_fetch = QuerySet._fetch_all
        overlapped = []

        def fetch_all(qs):
            real_fetch(qs)
            if qs.model is RecurringInvoice and not overlapped:
                overlapped.append(True)
                generate_recurring_invoices()

        with patch.object(QuerySet, "_fetch_all", fetch_all):
            generate_recurring_invoices()

        assert overlapped == [True]
        schedule.refresh_from_db()
        assert Invoice.objects.count() == 1
        assert schedule.invoices_generated == 1

    def test_an_end_date_extended_before_the_run_reaches_it_keeps_it_on(self, org_a):
        ending = _schedule(org_a, end_date=TODAY() - datetime.timedelta(days=1))
        running = _schedule(org_a)
        # Newest first, so `running` generates (and fires the hook) first,
        # before `ending` is claimed and re-read.
        _meanwhile(running, created_at=timezone.now() + datetime.timedelta(hours=1))

        with _on_first_save(
            Invoice,
            lambda inv: _meanwhile(
                ending, notes=EDITED, end_date=TODAY() + datetime.timedelta(days=90)
            ),
        ):
            generate_recurring_invoices()

        ending.refresh_from_db()
        assert ending.is_active is True
        assert ending.notes == EDITED

    def test_a_schedule_past_its_end_date_is_still_switched_off(self, org_a):
        ending = _schedule(org_a, end_date=TODAY() - datetime.timedelta(days=1))

        generate_recurring_invoices()

        ending.refresh_from_db()
        assert ending.is_active is False
        assert not Invoice.objects.exists()


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------


def _owed(org, amount="100.00", **fields):
    invoice = _invoice(org, status="Sent", **fields)
    _meanwhile(invoice, total_amount=Decimal(amount), amount_due=Decimal(amount))
    return Invoice.objects.get(pk=invoice.pk)


def _pay(invoice, amount):
    return Payment.objects.create(
        invoice=invoice,
        amount=Decimal(amount),
        payment_date=TODAY(),
        payment_method="CASH",
        org=invoice.org,
    )


@pytest.mark.django_db
class TestPaymentRecomputesFromTheStoredInvoice:
    def test_a_payment_on_a_stale_copy_does_not_undo_a_cancel(self, org_a):
        stale = _owed(org_a)
        _meanwhile(stale, status="Cancelled", notes=EDITED)

        _pay(stale, "40.00")

        invoice = Invoice.objects.get(pk=stale.pk)
        assert invoice.status == "Cancelled"
        assert invoice.notes == EDITED
        assert invoice.amount_paid == Decimal("40.00")
        assert invoice.amount_due == Decimal("60.00")

    def test_a_payment_on_a_stale_copy_keeps_an_edit_and_settles(self, org_a):
        stale = _owed(org_a)
        _meanwhile(stale, notes=EDITED)

        _pay(stale, "100.00")

        invoice = Invoice.objects.get(pk=stale.pk)
        assert invoice.status == "Paid"
        assert invoice.paid_at is not None
        assert invoice.amount_due == Decimal("0.00")
        assert invoice.notes == EDITED

    def test_recording_via_the_api_returns_and_stamps_the_new_totals(
        self, admin_client, admin_user, org_a
    ):
        invoice = _owed(org_a)
        before = invoice.updated_at

        response = admin_client.post(
            f"/api/invoices/{invoice.id}/payments/",
            {
                "amount": "30.00",
                "payment_date": str(TODAY()),
                "payment_method": "CASH",
            },
            format="json",
        )

        assert response.status_code == 201, response.content
        assert Decimal(response.json()["invoice"]["amount_paid"]) == Decimal("30")
        invoice.refresh_from_db()
        assert invoice.status == "Partially_Paid"
        assert invoice.updated_by_id == admin_user.id
        assert invoice.updated_at > before


# --------------------------------------------------------------------------
# Staff writes that race the system ones
# --------------------------------------------------------------------------


def _spy(name):
    """Wrap an `api_views` fetch helper, recording whether each call asked
    for a locked read (as in test_issued_lock_row_locking)."""
    real = getattr(api_views, name)
    locked = []

    def spy(request, pk, queryset=None):
        locked.append(queryset is not None and queryset.query.select_for_update)
        return real(request, pk, queryset)

    return patch.object(api_views, name, spy), locked


@pytest.mark.django_db
class TestStaffWritesReadALockedRow:
    def test_cancel(self, admin_client, admin_user, org_a):
        invoice = _owed(org_a)
        patcher, locked = _spy("get_invoice_or_error")

        with patcher:
            response = admin_client.post(f"/api/invoices/{invoice.id}/cancel/")

        assert response.status_code == 200, response.content
        assert locked == [True]
        invoice.refresh_from_db()
        assert invoice.status == "Cancelled"
        assert invoice.updated_by_id == admin_user.id

    def test_cancel_still_refuses_a_paid_invoice(self, admin_client, org_a):
        invoice = _owed(org_a)
        _meanwhile(invoice, status="Paid")

        response = admin_client.post(f"/api/invoices/{invoice.id}/cancel/")

        assert response.status_code == 400
        invoice.refresh_from_db()
        assert invoice.status == "Paid"

    def test_payment_create(self, admin_client, org_a):
        invoice = _owed(org_a)
        patcher, locked = _spy("get_invoice_or_error")

        with patcher:
            response = admin_client.post(
                f"/api/invoices/{invoice.id}/payments/",
                {
                    "amount": "10.00",
                    "payment_date": str(TODAY()),
                    "payment_method": "CASH",
                },
                format="json",
            )

        assert response.status_code == 201, response.content
        assert locked == [True]

    def test_payment_create_still_refuses_a_cancelled_invoice(
        self, admin_client, org_a
    ):
        invoice = _owed(org_a)
        _meanwhile(invoice, status="Cancelled")

        response = admin_client.post(
            f"/api/invoices/{invoice.id}/payments/",
            {"amount": "10.00", "payment_date": str(TODAY()), "payment_method": "CASH"},
            format="json",
        )

        assert response.status_code == 400
        assert not Payment.objects.exists()

    def test_recurring_update(self, admin_client, org_a):
        schedule = _schedule(org_a)
        patcher, locked = _spy("get_recurring_or_error")

        with patcher:
            response = admin_client.put(
                f"/api/invoices/recurring/{schedule.id}/",
                {"title": "Renamed"},
                format="json",
            )

        assert response.status_code == 200, response.content
        assert locked == [True]

    def test_recurring_toggle_reads_a_locked_row_and_writes_only_the_flag(
        self, admin_client, org_a
    ):
        schedule = _schedule(org_a)
        moved = TODAY() + datetime.timedelta(days=31)
        real = api_views.get_recurring_or_error
        locked = []

        # A generation run committing after the locked read cannot happen on
        # PostgreSQL; here it stands in for fields the toggle does not own.
        def read_then_generate(request, pk, queryset=None):
            locked.append(queryset is not None and queryset.query.select_for_update)
            result = real(request, pk, queryset)
            _meanwhile(schedule, next_generation_date=moved, invoices_generated=1)
            return result

        with patch.object(api_views, "get_recurring_or_error", read_then_generate):
            response = admin_client.post(
                f"/api/invoices/recurring/{schedule.id}/toggle/"
            )

        assert response.status_code == 200, response.content
        assert locked == [True]
        schedule.refresh_from_db()
        assert schedule.is_active is False
        assert schedule.next_generation_date == moved
        assert schedule.invoices_generated == 1


# --------------------------------------------------------------------------
# PostgreSQL: the lock is really asked for
# --------------------------------------------------------------------------


@pytest.mark.postgres_only
@pytest.mark.django_db
def test_system_saves_lock_the_row_on_postgres(org_a):
    if not connection.features.has_select_for_update:
        pytest.skip("SQLite has no row locks; this runs against PostgreSQL in CI")
    estimate = _estimate(org_a, status="Sent")
    invoice = _owed(org_a)
    _schedule(org_a)

    with CaptureQueriesContext(connection) as queries:
        APIClient().post(
            f"/api/public/estimate/{estimate.public_token}/accept/",
            {"name": "Dana Buyer", "email": "dana@buyer.example"},
            format="json",
        )
        _pay(invoice, "10.00")
        generate_recurring_invoices()

    locked = [q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]]
    assert any('FROM "estimate"' in sql for sql in locked)
    assert any('FROM "invoice"' in sql for sql in locked)
    # An overlapping run skips a schedule another run holds, not waits on it.
    assert any(
        'FROM "recurring_invoice"' in sql and "SKIP LOCKED" in sql for sql in locked
    )
