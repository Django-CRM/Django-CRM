"""A schedule that mails its invoices needs something to bill (1.11.0).

Lines stay optional on a schedule, but auto-send with no lines is refused on
create and update, and generation never mails an invoice with no lines from
a row saved before that rule: it raises a draft and logs why.
"""

import logging
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import Invoice, RecurringInvoice, RecurringInvoiceLineItem
from invoices.tasks import generate_recurring_invoices

URL = "/api/invoices/recurring/"
MESSAGE = "Add at least one line before turning on auto-send."
LINE = {"name": "Hosting", "quantity": "1", "unit_price": "50"}


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Acme", org=org_a)


@pytest.fixture
def contact(org_a):
    return Contact.objects.create(first_name="Ada", last_name="Buyer", org=org_a)


def _body(account, contact, **extra):
    today = str(timezone.localdate())
    return {
        "title": "Monthly",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "frequency": "MONTHLY",
        "start_date": today,
        "next_generation_date": today,
        **extra,
    }


@pytest.mark.django_db
class TestCreate:
    def test_auto_send_with_no_lines_is_refused(self, admin_client, account, contact):
        response = admin_client.post(
            URL, _body(account, contact, auto_send=True), format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["auto_send"] == [MESSAGE]
        assert not RecurringInvoice.objects.exists()

    def test_auto_send_with_a_line_is_allowed(self, admin_client, account, contact):
        response = admin_client.post(
            URL,
            _body(account, contact, auto_send=True, line_items=[LINE]),
            format="json",
        )

        assert response.status_code == 201, response.content

    def test_no_lines_without_auto_send_is_allowed(
        self, admin_client, account, contact
    ):
        response = admin_client.post(URL, _body(account, contact), format="json")

        assert response.status_code == 201, response.content


@pytest.mark.django_db
class TestUpdate:
    def _create(self, client, account, contact, **extra):
        client.post(URL, _body(account, contact, **extra), format="json")
        return RecurringInvoice.objects.get()

    def test_turning_auto_send_on_with_no_lines_is_refused(
        self, admin_client, account, contact
    ):
        schedule = self._create(admin_client, account, contact)

        response = admin_client.put(
            f"{URL}{schedule.id}/", {"auto_send": True}, format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["auto_send"] == [MESSAGE]
        schedule.refresh_from_db()
        assert schedule.auto_send is False

    def test_removing_every_line_while_auto_send_is_on_is_refused(
        self, admin_client, account, contact
    ):
        schedule = self._create(
            admin_client, account, contact, auto_send=True, line_items=[LINE]
        )

        response = admin_client.put(
            f"{URL}{schedule.id}/", {"line_items": []}, format="json"
        )

        assert response.status_code == 400, response.content
        assert schedule.line_items.count() == 1

    def test_an_older_row_can_still_have_other_fields_edited(
        self, admin_client, account, contact
    ):
        schedule = self._create(admin_client, account, contact)
        RecurringInvoice.objects.filter(pk=schedule.pk).update(auto_send=True)

        response = admin_client.put(
            f"{URL}{schedule.id}/", {"title": "Renamed"}, format="json"
        )

        assert response.status_code == 200, response.content


@pytest.mark.django_db
class TestGeneration:
    def _schedule(self, org, account, *, lines):
        today = timezone.localdate()
        schedule = RecurringInvoice.objects.create(
            org=org,
            account=account,
            title="Monthly",
            start_date=today,
            next_generation_date=today,
            auto_send=True,
            client_email="buyer@example.com",
        )
        if lines:
            RecurringInvoiceLineItem.objects.create(
                recurring_invoice=schedule, org=org, **LINE
            )
        return schedule

    def test_no_lines_is_raised_as_a_draft_and_not_sent(self, org_a, account, caplog):
        schedule = self._schedule(org_a, account, lines=False)

        with (
            patch("invoices.tasks.send_invoice_to_client.delay") as send,
            caplog.at_level(logging.WARNING, logger="invoices.tasks"),
        ):
            generate_recurring_invoices()

        invoice = Invoice.objects.get()
        assert invoice.status == "Draft"
        send.assert_not_called()
        assert str(schedule.id) in caplog.text

    def test_with_lines_it_is_sent(self, org_a, account):
        self._schedule(org_a, account, lines=True)

        with patch("invoices.tasks.send_invoice_to_client.delay") as send:
            generate_recurring_invoices()

        invoice = Invoice.objects.get()
        assert invoice.status == "Sent"
        assert invoice.line_items.count() == 1
        send.assert_called_once()
