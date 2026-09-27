"""A document that sends no date is dated in the org's day, not the server's.

`issue_date` on invoices and estimates, and `start_date` and
`next_generation_date` on a recurring schedule, defaulted to
`datetime.date.today`, which reads the server clock and ignores the org's
timezone. The invoice and estimate numbers (`INV-YYYYMMDD-`) read the server's
naive local clock too. Both now go through `timezone.localdate()`, which is the
org's day inside a request (`GetProfileAndOrg` activates it) and inside the
recurring generator (which activates each org's zone itself).

The clock is frozen across a year boundary: 23:30 UTC on 31 December is
already 1 January in Asia/Kolkata, and 02:00 UTC on 1 January is still
31 December in America/New_York. A UTC or server-local date fails one of each
pair. Patching `django.utils.timezone.now` moves `localdate()` but not
`datetime.date.today`, so the old defaults cannot pass these by accident.
"""

import datetime
from unittest.mock import patch

import pytest

from accounts.models import Account
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from invoices.tasks import generate_recurring_invoices

pytestmark = pytest.mark.django_db

KOLKATA_AHEAD = datetime.datetime(2026, 12, 31, 23, 30, tzinfo=datetime.timezone.utc)
NEW_YORK_BEHIND = datetime.datetime(2027, 1, 1, 2, 0, tzinfo=datetime.timezone.utc)
NEW_YEARS_DAY = datetime.date(2027, 1, 1)
NEW_YEARS_EVE = datetime.date(2026, 12, 31)

CASES = [
    pytest.param("Asia/Kolkata", KOLKATA_AHEAD, NEW_YEARS_DAY, id="ahead-of-utc"),
    pytest.param("America/New_York", NEW_YORK_BEHIND, NEW_YEARS_EVE, id="behind-utc"),
]


def _frozen(instant):
    return patch("django.utils.timezone.now", return_value=instant)


def _in_zone(org, name):
    org.timezone = name
    org.save(update_fields=["timezone"])


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Acme", org=org_a)


@pytest.fixture
def contact(org_a):
    return Contact.objects.create(first_name="Ada", last_name="Buyer", org=org_a)


@pytest.fixture(autouse=True)
def no_history():
    with patch("invoices.api_views.create_invoice_history.delay"):
        yield


@pytest.mark.parametrize("zone,instant,day", CASES)
def test_invoice_without_an_issue_date(
    admin_client, org_a, account, contact, zone, instant, day
):
    _in_zone(org_a, zone)
    with _frozen(instant):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Undated",
                "account_id": str(account.id),
                "contact_id": str(contact.id),
                "currency": "USD",
            },
            format="json",
        )
    assert response.status_code == 201, response.content
    invoice = Invoice.objects.get(invoice_title="Undated")
    assert invoice.issue_date == day
    assert invoice.invoice_number == f"INV-{day:%Y%m%d}-0001"


@pytest.mark.parametrize("zone,instant,day", CASES)
def test_estimate_without_an_issue_date(
    admin_client, org_a, account, contact, zone, instant, day
):
    _in_zone(org_a, zone)
    with _frozen(instant):
        response = admin_client.post(
            "/api/invoices/estimates/",
            {
                "title": "Undated",
                "account_id": str(account.id),
                "contact_id": str(contact.id),
                "currency": "USD",
            },
            format="json",
        )
    assert response.status_code == 201, response.content
    estimate = Estimate.objects.get(title="Undated")
    assert estimate.issue_date == day
    assert estimate.expiry_date is None
    assert estimate.estimate_number == f"EST-{day:%Y%m%d}-0001"


@pytest.mark.parametrize("zone,instant,day", CASES)
def test_recurring_schedule_without_dates(
    admin_client, org_a, account, contact, zone, instant, day
):
    _in_zone(org_a, zone)
    with _frozen(instant):
        response = admin_client.post(
            "/api/invoices/recurring/",
            {
                "title": "Undated",
                "account_id": str(account.id),
                "contact_id": str(contact.id),
                "frequency": "MONTHLY",
            },
            format="json",
        )
    assert response.status_code == 201, response.content
    recurring = RecurringInvoice.objects.get(title="Undated")
    assert (recurring.start_date, recurring.next_generation_date) == (day, day)


def test_a_date_the_caller_sends_wins(admin_client, org_a, account, contact):
    _in_zone(org_a, "Asia/Kolkata")
    with _frozen(KOLKATA_AHEAD):
        response = admin_client.post(
            "/api/invoices/",
            {
                "invoice_title": "Dated",
                "account_id": str(account.id),
                "contact_id": str(contact.id),
                "currency": "USD",
                "issue_date": "2026-12-15",
            },
            format="json",
        )
    assert response.status_code == 201, response.content
    assert Invoice.objects.get(invoice_title="Dated").issue_date == datetime.date(
        2026, 12, 15
    )


@pytest.mark.parametrize("zone,instant,day", CASES)
def test_recurring_generator_dates_and_numbers_in_the_org_day(
    org_a, account, contact, zone, instant, day
):
    """The Celery task runs no middleware; it activates each org's zone."""
    _in_zone(org_a, zone)
    RecurringInvoice.objects.create(
        title="Monthly",
        account=account,
        contact=contact,
        frequency="MONTHLY",
        start_date=day,
        next_generation_date=day,
        org=org_a,
    )
    with _frozen(instant):
        generate_recurring_invoices()
    invoice = Invoice.objects.get(org=org_a)
    assert invoice.issue_date == day
    assert invoice.invoice_number == f"INV-{day:%Y%m%d}-0001"
