"""The Celery beat tasks that sweep every org, run under a role RLS binds.

A worker runs no middleware, so a periodic task starts with an empty
`app.current_org`, and the isolation policy matches no rows when the context is
empty. Six of these tasks used to find their work with a query made before any
context was set, so under the non-superuser role the deployment docs mandate
they silently did nothing, every run. SQLite has no RLS and a superuser
bypasses it, so every other test of these tasks passed throughout.

Each test here seeds rows in two orgs, empties the context the way a worker
would see it, runs the task, and asserts three things:

* rows in both orgs were processed, which is what failed before the fix;
* every write happened with the RLS context set to that row's own org, so
  processing org A never touches org B's rows under A's context;
* the context is empty again afterwards, so the last org's id is not left on
  a pooled connection for the next task to inherit.

Marked `postgres_only`, and only meaningful under a non-superuser role (the CI
step "Run the RLS tests as a policy-bound role").
"""

import contextlib
from datetime import timedelta
from unittest import mock
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from cases.models import Case, EscalationPolicy, TimeEntry
from cases.tasks import auto_stop_stale_timers, scan_for_breached_cases
from common.models import Profile, User
from conftest import clear_rls_context, rls_org
from invoices.models import Estimate, Invoice, RecurringInvoice
from invoices.tasks import (
    check_expired_estimates,
    check_overdue_invoices,
    generate_recurring_invoices,
    process_payment_reminders,
)

pytestmark = [pytest.mark.postgres_only, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _postgres_only():
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")


def _current_org():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_org', true)")
        return cursor.fetchone()[0] or ""


@contextlib.contextmanager
def _record_save_context(model):
    """Record ``(row org, RLS context)`` for every ``model.save()`` call."""
    seen = []
    original = model.save

    def spy(self, *args, **kwargs):
        seen.append((str(self.org_id), _current_org()))
        return original(self, *args, **kwargs)

    with mock.patch.object(model, "save", spy):
        yield seen


def _run_as_worker(task, *args, **kwargs):
    """Run ``task`` the way a worker does: with no RLS context set."""
    clear_rls_context()
    assert _current_org() == ""
    result = task(*args, **kwargs)
    assert _current_org() == "", "task left an org's RLS context on the connection"
    return result


def _assert_each_write_in_own_org(seen, *orgs):
    assert {org for org, _ in seen} == {str(o.id) for o in orgs}
    for row_org, context in seen:
        assert context == row_org


def _profile(org, email):
    user = User.objects.create_user(email=email, password="x")
    return Profile.objects.create(user=user, org=org, role="ADMIN", is_active=True)


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


def _seed_breached_case(org, email):
    with rls_org(org):
        target = _profile(org, email)
        EscalationPolicy.objects.create(
            org=org,
            priority="Urgent",
            first_response_action="notify",
            resolution_action="notify",
            first_response_target=target,
            resolution_target=target,
            is_active=True,
        )
        case = Case.objects.create(
            name="Breached", status="New", priority="Urgent", org=org
        )
        Case.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=5)
        )
    return case


@mock.patch("cases.tasks.send_email_to_assigned_user")
def test_scan_for_breached_cases_escalates_in_every_org(mock_email, org_a, org_b):
    case_a = _seed_breached_case(org_a, "target-a@test.com")
    case_b = _seed_breached_case(org_b, "target-b@test.com")

    with _record_save_context(Case) as seen:
        assert _run_as_worker(scan_for_breached_cases) == 2

    _assert_each_write_in_own_org(seen, org_a, org_b)
    for org, case in ((org_a, case_a), (org_b, case_b)):
        with rls_org(org):
            case.refresh_from_db()
            assert case.escalation_count == 1


def _seed_stale_timer(org, email):
    with rls_org(org):
        profile = _profile(org, email)
        case = Case.objects.create(name="Timed", status="New", org=org)
        return TimeEntry.objects.create(
            org=org,
            case=case,
            profile=profile,
            started_at=timezone.now() - timedelta(hours=15),
        )


def test_auto_stop_stale_timers_stops_in_every_org(org_a, org_b):
    entry_a = _seed_stale_timer(org_a, "timer-a@test.com")
    entry_b = _seed_stale_timer(org_b, "timer-b@test.com")

    with _record_save_context(TimeEntry) as seen:
        assert _run_as_worker(auto_stop_stale_timers, threshold_hours=12) == 2

    _assert_each_write_in_own_org(seen, org_a, org_b)
    for org, entry in ((org_a, entry_a), (org_b, entry_b)):
        with rls_org(org):
            entry.refresh_from_db()
            assert entry.ended_at is not None
            assert entry.auto_stopped is True


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------


def _seed_invoice(org, **fields):
    with rls_org(org):
        return Invoice.objects.create(
            invoice_title="Sweep",
            invoice_number=f"INV-T-{uuid4().hex[:12]}",
            currency="USD",
            client_email="client@example.com",
            org=org,
            **fields,
        )


def test_generate_recurring_invoices_generates_in_every_org(org_a, org_b):
    due = timezone.localdate() - timedelta(days=2)
    recurring = {}
    for org in (org_a, org_b):
        with rls_org(org):
            recurring[org.id] = RecurringInvoice.objects.create(
                title=f"Hosting {org.name}",
                frequency="MONTHLY",
                start_date=due,
                next_generation_date=due,
                currency="USD",
                is_active=True,
                org=org,
            )

    # Both orgs mint INV-<date>-0001 here, which only works because invoice
    # numbers are unique per org rather than across every org.
    with _record_save_context(Invoice) as seen:
        _run_as_worker(generate_recurring_invoices)

    _assert_each_write_in_own_org(seen, org_a, org_b)
    for org in (org_a, org_b):
        with rls_org(org):
            assert Invoice.objects.filter(org=org).count() == 1
            assert (
                RecurringInvoice.objects.get(pk=recurring[org.id].pk).invoices_generated
                == 1
            )


def test_check_overdue_invoices_marks_in_every_org(org_a, org_b):
    past_due = timezone.localdate() - timedelta(days=5)
    invoice_a = _seed_invoice(org_a, status="Sent", due_date=past_due)
    invoice_b = _seed_invoice(org_b, status="Sent", due_date=past_due)

    with _record_save_context(Invoice) as seen:
        _run_as_worker(check_overdue_invoices)

    _assert_each_write_in_own_org(seen, org_a, org_b)
    for org, invoice in ((org_a, invoice_a), (org_b, invoice_b)):
        with rls_org(org):
            invoice.refresh_from_db()
            assert invoice.status == "Overdue"


@mock.patch("invoices.tasks.send_payment_reminder")
def test_process_payment_reminders_reminds_in_every_org(mock_send, org_a, org_b):
    due_soon = timezone.localdate() + timedelta(days=2)
    invoice_a = _seed_invoice(
        org_a, status="Sent", due_date=due_soon, reminder_days_before=3
    )
    invoice_b = _seed_invoice(
        org_b, status="Sent", due_date=due_soon, reminder_days_before=3
    )

    dispatched = []
    mock_send.delay.side_effect = lambda invoice_id, org_id, **kw: dispatched.append(
        (invoice_id, org_id, _current_org())
    )
    _run_as_worker(process_payment_reminders)

    assert sorted(dispatched) == sorted(
        [
            (str(invoice_a.id), str(org_a.id), str(org_a.id)),
            (str(invoice_b.id), str(org_b.id), str(org_b.id)),
        ]
    )


def test_check_expired_estimates_expires_in_every_org(org_a, org_b):
    expired_on = timezone.localdate() - timedelta(days=5)
    estimates = {}
    for org in (org_a, org_b):
        with rls_org(org):
            estimates[org.id] = Estimate.objects.create(
                title="Quote",
                estimate_number=f"EST-T-{uuid4().hex[:12]}",
                status="Sent",
                currency="USD",
                expiry_date=expired_on,
                org=org,
            )

    with _record_save_context(Estimate) as seen:
        _run_as_worker(check_expired_estimates)

    _assert_each_write_in_own_org(seen, org_a, org_b)
    for org in (org_a, org_b):
        with rls_org(org):
            estimate = Estimate.objects.get(pk=estimates[org.id].pk)
            assert estimate.status == "Expired"
