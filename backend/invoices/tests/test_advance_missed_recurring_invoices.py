"""`advance_missed_recurring_invoices`: skip missed periods before beat starts.

`generate_recurring_invoices` bills one period per template per run and
advances by one period, so a template left months behind while no scheduler
ran would bill every missed period, one a night. The command moves such a
template to its first period on or after today (a date equal to today is the
current period, not a missed one) and does nothing else.
"""

import datetime
from io import StringIO
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from invoices.management.commands import advance_missed_recurring_invoices as cmd
from invoices.models import Invoice, RecurringInvoice


def _template(org, next_date, **extra):
    return RecurringInvoice.objects.create(
        title="Hosting",
        frequency="WEEKLY",
        start_date=next_date,
        next_generation_date=next_date,
        is_active=True,
        org=org,
        **extra,
    )


def _run(*args):
    out = StringIO()
    call_command("advance_missed_recurring_invoices", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_past_template_advanced_to_first_future_period(org_a):
    today = timezone.localdate()
    # Weekly from 20 days ago: -13, -6, then +1 is the first period not missed.
    recurring = _template(org_a, today - datetime.timedelta(days=20))
    # Weekly from 14 days ago lands on today, the current period: stop there.
    lands_today = _template(org_a, today - datetime.timedelta(days=14))

    _run()

    recurring.refresh_from_db()
    lands_today.refresh_from_db()
    assert recurring.next_generation_date == today + datetime.timedelta(days=1)
    assert lands_today.next_generation_date == today
    assert recurring.is_active is True
    assert recurring.invoices_generated == 0
    assert Invoice.objects.count() == 0
    assert mail.outbox == []


@pytest.mark.django_db
def test_future_and_due_today_templates_untouched(org_a):
    today = timezone.localdate()
    future = _template(org_a, today + datetime.timedelta(days=5))
    due_today = _template(org_a, today)

    out = _run()

    future.refresh_from_db()
    due_today.refresh_from_db()
    assert future.next_generation_date == today + datetime.timedelta(days=5)
    assert due_today.next_generation_date == today
    assert "0 template(s)" in out


@pytest.mark.django_db
def test_ended_and_inactive_templates_left_for_the_task(org_a):
    today = timezone.localdate()
    past = today - datetime.timedelta(days=20)
    ended = _template(org_a, past, end_date=today - datetime.timedelta(days=1))
    inactive = _template(org_a, past)
    RecurringInvoice.objects.filter(pk=inactive.pk).update(is_active=False)

    _run()

    ended.refresh_from_db()
    inactive.refresh_from_db()
    # generate_recurring_invoices deactivates an ended template without billing
    # it; the command leaves that decision where it is.
    assert ended.next_generation_date == past
    assert ended.is_active is True
    assert inactive.next_generation_date == past


@pytest.mark.django_db
def test_dry_run_changes_nothing_and_reports_counts(org_a):
    past = timezone.localdate() - datetime.timedelta(days=20)
    recurring = _template(org_a, past)

    out = _run("--dry-run")

    recurring.refresh_from_db()
    assert recurring.next_generation_date == past
    assert str(org_a.id) in out
    assert "1 template(s) would be advanced" in out
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_second_run_is_a_no_op(org_a):
    today = timezone.localdate()
    recurring = _template(org_a, today - datetime.timedelta(days=20))

    _run()
    out = _run()

    recurring.refresh_from_db()
    assert recurring.next_generation_date == today + datetime.timedelta(days=1)
    assert "Total: 0 template(s) advanced" in out


@pytest.mark.django_db
def test_org_a_run_never_touches_org_b(org_a, org_b):
    """Every template is advanced under its own org's RLS context.

    SQLite has no RLS, so this watches the context the command sets and fails
    if a template is computed while another org's context is active, which is
    what dropping the explicit `org=` filter would do.
    """
    today = timezone.localdate()
    past = today - datetime.timedelta(days=20)
    a = _template(org_a, past)
    b = _template(org_b, past)

    context = {}
    real_next = RecurringInvoice.calculate_next_date

    def spy_next(self):
        assert self.org_id == context["org"], "template read under another org"
        return real_next(self)

    with (
        mock.patch.object(
            cmd, "set_rls_context", side_effect=lambda o: context.update(org=o)
        ),
        mock.patch.object(RecurringInvoice, "calculate_next_date", spy_next),
    ):
        _run()

    a.refresh_from_db()
    b.refresh_from_db()
    assert a.next_generation_date == today + datetime.timedelta(days=1)
    assert b.next_generation_date == today + datetime.timedelta(days=1)
