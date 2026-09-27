"""A recurring schedule never bills before its start date.

The generator reads only `next_generation_date`. A create that sent a future
`start_date` and no `next_generation_date` took the model default (today), so
a schedule meant to start next month billed its first invoice today. An
omitted date now follows `start_date`, and a date before the start is refused
on create and on update.
"""

import datetime

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import RecurringInvoice

pytestmark = pytest.mark.django_db

URL = "/api/invoices/recurring/"


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Acme", org=org_a)


@pytest.fixture
def contact(org_a):
    return Contact.objects.create(first_name="Ada", last_name="Buyer", org=org_a)


@pytest.fixture
def body(account, contact):
    return {
        "title": "Hosting",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "frequency": "MONTHLY",
    }


@pytest.fixture
def schedule(account, contact, org_a):
    today = timezone.localdate()
    return RecurringInvoice.objects.create(
        title="Hosting",
        account=account,
        contact=contact,
        frequency="MONTHLY",
        start_date=today,
        next_generation_date=today + datetime.timedelta(days=10),
        org=org_a,
    )


def _future(days):
    return timezone.localdate() + datetime.timedelta(days=days)


def test_an_omitted_first_run_follows_a_future_start(admin_client, body):
    start = _future(30)
    response = admin_client.post(URL, {**body, "start_date": str(start)}, format="json")

    assert response.status_code == 201, response.content
    recurring = RecurringInvoice.objects.get(title="Hosting")
    assert recurring.start_date == start
    assert recurring.next_generation_date == start


def test_a_first_run_before_the_start_is_refused_on_create(admin_client, body):
    response = admin_client.post(
        URL,
        {
            **body,
            "start_date": str(_future(30)),
            "next_generation_date": str(_future(29)),
        },
        format="json",
    )

    assert response.status_code == 400
    assert "next_generation_date" in response.json()["errors"]
    assert not RecurringInvoice.objects.filter(title="Hosting").exists()


def test_a_first_run_after_the_start_is_accepted(admin_client, body):
    response = admin_client.post(
        URL,
        {
            **body,
            "start_date": str(_future(1)),
            "next_generation_date": str(_future(15)),
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    recurring = RecurringInvoice.objects.get(title="Hosting")
    assert recurring.next_generation_date == _future(15)


def test_a_first_run_before_an_omitted_start_is_refused(admin_client, body):
    """An omitted start is the org's today, so yesterday is before it."""
    response = admin_client.post(
        URL, {**body, "next_generation_date": str(_future(-1))}, format="json"
    )

    assert response.status_code == 400
    assert "next_generation_date" in response.json()["errors"]


def test_moving_the_start_past_the_next_run_is_refused(admin_client, schedule):
    response = admin_client.put(
        f"{URL}{schedule.id}/", {"start_date": str(_future(11))}, format="json"
    )

    assert response.status_code == 400
    assert "next_generation_date" in response.json()["errors"]
    schedule.refresh_from_db()
    assert schedule.start_date == timezone.localdate()


def test_moving_the_next_run_before_the_start_is_refused(admin_client, schedule):
    response = admin_client.put(
        f"{URL}{schedule.id}/",
        {"next_generation_date": str(_future(-1))},
        format="json",
    )

    assert response.status_code == 400
    assert "next_generation_date" in response.json()["errors"]
    schedule.refresh_from_db()
    assert schedule.next_generation_date == _future(10)


def test_moving_either_date_within_the_rule_is_accepted(admin_client, schedule):
    response = admin_client.put(
        f"{URL}{schedule.id}/",
        {"start_date": str(_future(5)), "next_generation_date": str(_future(5))},
        format="json",
    )

    assert response.status_code == 200, response.content
    schedule.refresh_from_db()
    assert (schedule.start_date, schedule.next_generation_date) == (
        _future(5),
        _future(5),
    )


def test_an_older_row_can_still_be_edited_without_touching_its_dates(
    admin_client, schedule
):
    """A row saved before the rule may already break it; renaming it is fine."""
    RecurringInvoice.objects.filter(pk=schedule.pk).update(start_date=_future(20))

    response = admin_client.put(
        f"{URL}{schedule.id}/", {"title": "Renamed"}, format="json"
    )

    assert response.status_code == 200, response.content
    schedule.refresh_from_db()
    assert schedule.title == "Renamed"
