"""Invoice and estimate numbers are sequences per org, unique per org.

`generate_invoice_number` and `generate_estimate_number` count the day's
numbers in one org, and under RLS they can only ever see that org's rows. The
columns used to be unique across every org, so the second org to invoice on a
given day minted `INV-<date>-0001` again and its save raised IntegrityError.
Uniqueness is now `(org, number)`, which is what the generator can actually
guarantee.
"""

import datetime
import threading
import time

import pytest
from django.db import IntegrityError, connection, transaction

from common.tasks import set_rls_context
from conftest import rls_org
from invoices.models import Estimate, Invoice


def _today(prefix):
    return f"{prefix}-{datetime.datetime.now():%Y%m%d}-"


def _invoice(org, **fields):
    return Invoice.objects.create(
        invoice_title="Numbered", client_email="c@example.com", org=org, **fields
    )


def _estimate(org, **fields):
    return Estimate.objects.create(title="Numbered", org=org, **fields)


def test_second_invoice_in_an_org_takes_the_next_number(org_a):
    first = _invoice(org_a)
    second = _invoice(org_a)
    assert first.invoice_number == _today("INV") + "0001"
    assert second.invoice_number == _today("INV") + "0002"


def test_orgs_number_independently(org_a, org_b):
    """Runs on every backend: the generator scopes by org itself, not via RLS."""
    with rls_org(org_b):
        _invoice(org_b)
        _invoice(org_b)
        _estimate(org_b)
    invoice = _invoice(org_a)
    estimate = _estimate(org_a)
    assert invoice.invoice_number == _today("INV") + "0001"
    assert estimate.estimate_number == _today("EST") + "0001"


def test_same_invoice_number_in_another_org_is_allowed(org_a, org_b):
    _invoice(org_a, invoice_number="INV-SHARED-1")
    with rls_org(org_b):
        other = _invoice(org_b, invoice_number="INV-SHARED-1")
    assert other.pk


def test_same_estimate_number_in_another_org_is_allowed(org_a, org_b):
    _estimate(org_a, estimate_number="EST-SHARED-1")
    with rls_org(org_b):
        other = _estimate(org_b, estimate_number="EST-SHARED-1")
    assert other.pk


def test_same_invoice_number_in_one_org_is_rejected(org_a):
    _invoice(org_a, invoice_number="INV-DUP-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        _invoice(org_a, invoice_number="INV-DUP-1")


def test_same_estimate_number_in_one_org_is_rejected(org_a):
    _estimate(org_a, estimate_number="EST-DUP-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        _estimate(org_a, estimate_number="EST-DUP-1")


@pytest.mark.postgres_only
def test_two_orgs_each_create_their_first_invoice_and_estimate_same_day(org_a, org_b):
    """The production failure, under a role RLS binds.

    Each org's generator sees only its own rows, so both orgs mint number 0001.
    With a globally unique column the second save raised IntegrityError.
    """
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")

    created = {}
    for org in (org_a, org_b):
        with rls_org(org):
            created[org.id] = (_invoice(org), _estimate(org))

    for invoice, estimate in created.values():
        assert invoice.invoice_number == _today("INV") + "0001"
        assert estimate.estimate_number == _today("EST") + "0001"


@pytest.mark.postgres_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "model,create,field",
    [(Invoice, _invoice, "invoice_number"), (Estimate, _estimate, "estimate_number")],
)
def test_concurrent_creates_in_one_org_get_distinct_numbers(
    org_a, model, create, field
):
    """Two creates at the same moment in one org both succeed, numbered 1 and 2.

    `recalculate_totals` runs between choosing the number and inserting the
    row; slowing it down holds that window open so both threads choose before
    either inserts, unless number allocation is serialized per org.
    """
    if connection.vendor != "postgresql":
        pytest.skip("advisory locks are PostgreSQL-only")

    original = model.recalculate_totals
    barrier = threading.Barrier(2)
    numbers, errors = [], []

    def slow_recalculate(self):
        time.sleep(0.5)
        return original(self)

    def worker():
        try:
            set_rls_context(org_a.id)
            barrier.wait()
            numbers.append(getattr(create(org_a), field))
        except Exception as exc:  # recorded and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    model.recalculate_totals = slow_recalculate
    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        model.recalculate_totals = original

    assert errors == []
    prefix = _today("INV" if model is Invoice else "EST")
    assert sorted(numbers) == [prefix + "0001", prefix + "0002"]
