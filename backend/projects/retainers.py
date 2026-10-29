"""Retainers: the month rules a project's writes apply, billing one month,
and what billing the next month would count today.

A month is the date of its first day, a calendar month in the org's timezone.
``retainer_next_month`` is the month the next invoice bills and
``retainer_last_month`` the last one to bill (empty while the retainer is open).
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from cases.services.billing import attach_entries_to_invoice
from cases.time_reports import hours
from common.models import Profile
from common.modules import module_is_available, org_of
from common.org_time import org_timezone
from invoices.api_views import _first_visible_contact
from invoices.models import Invoice, InvoiceLineItem
from projects.burn import unbilled_entries
from projects.models import Project

RETAINER = "retainer"
CENTS = Decimal("0.01")
ZERO = Decimal("0.00")

NOT_A_RETAINER = "This project is not on a retainer."
ALREADY_BILLED = "This month is already billed."
ENDED = "This retainer has ended."
NOT_RUNNING = "This retainer is not running. Make the project active to bill it."


def month_of(day):
    return day.replace(day=1)


def add_months(month, count=1):
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_end(month):
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def month_key(month):
    return f"{month:%Y-%m}"


def apply_month_rules(project, before_status, before_billing_type, user=None):
    """Set the retainer months after a create (both ``before_*`` None) or a
    PATCH, as `docs/features/projects.md#retainers` states, inside the write's
    transaction and under its row lock. Saves only the fields it changes.
    Becoming a retainer takes rule 1 alone, so a project switched to one while
    on hold bills nothing until it is active. ``user`` is who made the write:
    coming back to active first drafts, as theirs, any ended month still owed.
    """
    if project.billing_type != RETAINER:
        return
    org = org_of(project)
    with org_timezone(org):
        today = timezone.localdate()
    current = month_of(today)
    if before_billing_type != RETAINER:
        start = month_of(project.start_date) if project.start_date else current
        # Never backwards: a month already billed ahead stays billed.
        first = max(current, start, project.retainer_next_month or current)
        changes = {
            "retainer_next_month": first,
            "retainer_carried_hours": ZERO,
            "retainer_last_month": (
                None if project.status == "active" else add_months(first, -1)
            ),
        }
    elif before_status == "active" and project.status != "active":
        changes = {"retainer_last_month": current}
    elif before_status != "active" and project.status == "active":
        # Leaving active promised the last month is still billed. With
        # Invoices off nothing is drafted, and the month falls to the skip rule.
        if module_is_available(org, "invoices"):
            while bill_month(project.pk, org, today, user=user) is not None:
                pass
        project.refresh_from_db(
            fields=["retainer_next_month", "retainer_carried_hours"]
        )
        changes = {
            "retainer_last_month": None,
            "retainer_next_month": max(project.retainer_next_month or current, current),
        }
    else:
        return
    changed = [
        name for name, value in changes.items() if getattr(project, name) != value
    ]
    for name in changed:
        setattr(project, name, changes[name])
    if changed:
        project.save(update_fields=changed)


def refusal(project, today):
    """Why billing ``project``'s next month on ``today`` by hand is refused,
    or None. Read under the row lock where it decides a write."""
    month = project.retainer_next_month
    if (
        project.billing_type != RETAINER
        or month is None
        or project.retainer_fee is None
    ):
        return NOT_A_RETAINER
    if project.retainer_last_month is not None and month > project.retainer_last_month:
        return ENDED if project.status == "active" else NOT_RUNNING
    if month > month_of(today):
        if project.start_date and month == month_of(project.start_date):
            return f"This retainer starts in {month:%B %Y}."
        return ALREADY_BILLED
    return None


def _figures(project, minutes):
    """``(available, overage, carried next)`` in hours for ``minutes`` used
    in a month. Available is None without an allowance, which never has an
    overage. Worked out from minutes, not from rounded hours."""
    if project.retainer_hours is None:
        return None, ZERO, ZERO
    available = project.retainer_hours
    if project.retainer_rollover:
        available += project.retainer_carried_hours
    over = (Decimal(minutes) - available * 60) / 60
    overage = max(over, Decimal(0)).quantize(CENTS, rounding=ROUND_HALF_UP)
    carried = ZERO
    if project.retainer_rollover:
        carried = max(-over, Decimal(0)).quantize(CENTS, rounding=ROUND_HALF_UP)
    return available, overage, carried


def _contact(account, org, user):
    """A person's invoice is billed to a contact they may open; the
    scheduler's to the account's newest contact in the org."""
    if user is None:
        return account.contacts.filter(org=org).order_by("-created_at", "id").first()
    # The acting member, who may be an app member (`common.app_members`).
    profile = Profile.all_objects.filter(user=user, org=org, is_active=True).first()
    if profile is None:
        return None
    return _first_visible_contact(account.contacts, profile)


def _draft(project, org, month, overage, user, today):
    label = f"{month:%B %Y}"
    suffix = f", retainer {label}"
    invoice = Invoice.objects.create(
        invoice_title=project.name[: 100 - len(suffix)] + suffix,
        account=project.account,
        contact=_contact(project.account, org, user),
        currency=project.currency,
        status="Draft",
        issue_date=today,
        project=project,
        org=org,
        created_by=user,
    )
    lines = [(f"{label} retainer", Decimal("1"), project.retainer_fee)]
    if overage > 0:
        lines.append(
            ("Hours over the allowance", overage, project.retainer_overage_rate)
        )
    for order, (name, quantity, price) in enumerate(lines):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            org=org,
            name=name,
            description=name,
            quantity=quantity,
            unit_price=price,
            order=order,
        )
    invoice.recalculate_totals()
    invoice.save()
    return invoice


def bill_month(project_id, org, today, user=None, skip_before=None, open_month=False):
    """Draft the invoice for the project's ``retainer_next_month`` and move
    that month on, in one transaction under the project's row lock. ``today``
    is the org's day. The returned invoice carries the month it bills as
    ``retainer_month``.

    None when there is nothing to bill: not a retainer, ended, a later month,
    or the open month unless ``open_month`` (the button). A month that ended
    before ``skip_before`` (a module came back on) is skipped: the month moves
    on and None is returned. Every unbilled entry up to the month's end (or
    ``today`` for the open month) counts, however early it started. While
    Timesheet is off its time is out of sight, so the fee is billed alone and
    the carried hours stay as they are.
    """
    with org_timezone(org), transaction.atomic():
        project = (
            Project.objects.select_for_update().filter(pk=project_id, org=org).first()
        )
        if project is None or refusal(project, today) is not None:
            return None
        month = project.retainer_next_month
        is_open = month == month_of(today)
        if is_open and not open_month:
            return None
        if skip_before is not None and month_end(month) < skip_before:
            project.retainer_next_month = add_months(month)
            project.save(update_fields=["retainer_next_month"])
            return None
        through = today if is_open else month_end(month)
        tracked = module_is_available(org, "timesheet")
        entries = []
        if tracked:
            entries = list(
                unbilled_entries(project, through).select_for_update(of=("self",))
            )
        minutes = sum(entry.duration_minutes for entry in entries)
        _available, overage, carried = _figures(project, minutes)
        if not tracked:
            carried = project.retainer_carried_hours
        invoice = _draft(project, org, month, overage, user, today)
        attach_entries_to_invoice(entries, invoice)
        project.retainer_next_month = add_months(month)
        project.retainer_carried_hours = carried
        project.save(update_fields=["retainer_next_month", "retainer_carried_hours"])
    invoice.retainer_month = month
    return invoice


def retainer_usage(project, today):
    """`ProjectDetailSerializer.retainer_usage`: what billing
    ``retainer_next_month`` would count on ``today``, or None for a project
    that is not a retainer."""
    month = project.retainer_next_month
    if project.billing_type != RETAINER or month is None:
        return None
    org = org_of(project)
    minutes = 0
    if module_is_available(org, "timesheet"):
        through = min(month_end(month), today)
        minutes = unbilled_entries(project, through).aggregate(
            total=Sum("duration_minutes")
        )["total"]
    available, overage, _carried = _figures(project, minutes or 0)
    return {
        "month": month_key(month),
        "used_hours": hours(minutes),
        "available_hours": None
        if available is None
        else str(available.quantize(CENTS)),
        "overage_hours": str(overage),
        "billable_now": (
            module_is_available(org, "invoices") and refusal(project, today) is None
        ),
    }
