"""The contact on an invoice, estimate or recurring invoice must be one the
caller may open.

`validate_contact_id` checked the org only, so a member could put a contact
they cannot open on a document they then own as its creator, and read the
person back through it. The two generators picked `.contacts.first()`, which
could be a contact that stayed linked to the deal or account without the
caller being able to open it.

`user_client` is a member (role USER). `mine` is a contact that member
created; `hidden` was created by the admin and is not assigned to the member,
so `visible_contacts_qs` does not include it for them.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from cases.models import Case, TimeEntry
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from opportunity.models import Opportunity, OpportunityLineItem

REFUSED = "Contact not found, or you do not have access to it."


@pytest.fixture
def account(org_a, regular_user):
    # The member's own, so the account check passes and the contact is what
    # each test here exercises.
    return Account.objects.create(name="Billing Co", org=org_a, created_by=regular_user)


@pytest.fixture
def mine(org_a, regular_user):
    return Contact.objects.create(
        first_name="Mine", last_name="Visible", org=org_a, created_by=regular_user
    )


@pytest.fixture
def hidden(org_a, admin_user):
    return Contact.objects.create(
        first_name="Hidden", last_name="Person", org=org_a, created_by=admin_user
    )


@pytest.fixture
def theirs(org_b):
    return Contact.objects.create(first_name="Other", last_name="Org", org=org_b)


def _invoice_body(account, contact):
    return {
        "invoice_title": "Contact access",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "USD",
    }


def _estimate_body(account, contact):
    today = timezone.localdate()
    return {
        "title": "Contact access",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "USD",
        "issue_date": str(today),
        "expiry_date": str(today + datetime.timedelta(days=30)),
    }


def _recurring_body(account, contact):
    today = timezone.localdate()
    return {
        "title": "Contact access",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "frequency": "MONTHLY",
        "start_date": str(today),
        "next_generation_date": str(today),
        "payment_terms": "NET_30",
        "currency": "USD",
        "is_active": True,
    }


def _stored(model, org, account, contact, creator, **fields):
    """A document already carrying ``contact``, created by ``creator``."""
    today = timezone.localdate()
    extra = {
        Invoice: {"invoice_title": "Stored"},
        Estimate: {
            "title": "Stored",
            "issue_date": today,
            "expiry_date": today + datetime.timedelta(days=30),
        },
        RecurringInvoice: {
            "title": "Stored",
            "frequency": "MONTHLY",
            "start_date": today,
            "next_generation_date": today,
        },
    }[model]
    return model.objects.create(
        org=org,
        account=account,
        contact=contact,
        currency="USD",
        created_by=creator,
        **extra,
        **fields,
    )


KINDS = [
    pytest.param(Invoice, "/api/invoices/", _invoice_body, "invoice", id="invoice"),
    pytest.param(
        Estimate, "/api/invoices/estimates/", _estimate_body, "estimate", id="estimate"
    ),
    pytest.param(
        RecurringInvoice,
        "/api/invoices/recurring/",
        _recurring_body,
        "recurring_invoice",
        id="recurring",
    ),
]


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
    ):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize("model, url, body, key", KINDS)
class TestDocumentContactOnCreate:
    def test_member_cannot_set_a_contact_they_cannot_open(
        self, model, url, body, key, user_client, account, hidden
    ):
        response = user_client.post(url, body(account, hidden), format="json")

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["contact_id"] == [REFUSED]
        assert not model.objects.exists()

    def test_member_can_set_a_contact_they_can_open_and_reads_the_result(
        self, model, url, body, key, user_client, account, mine
    ):
        response = user_client.post(url, body(account, mine), format="json")

        assert response.status_code == 201, response.content
        document = model.objects.get()
        assert document.contact == mine
        # The creator can open what they made, which is why the contact on it
        # has to be one they could open already.
        assert user_client.get(f"{url}{document.id}/").status_code == 200

    def test_admin_can_set_any_contact_in_the_org(
        self, model, url, body, key, admin_client, account, hidden
    ):
        response = admin_client.post(url, body(account, hidden), format="json")

        assert response.status_code == 201, response.content
        assert model.objects.get().contact == hidden

    def test_another_orgs_contact_is_refused_with_the_same_message(
        self, model, url, body, key, admin_client, user_client, account, theirs
    ):
        for client in (admin_client, user_client):
            response = client.post(url, body(account, theirs), format="json")

            assert response.status_code == 400, response.content
            assert response.json()["errors"]["contact_id"] == [REFUSED]
        assert not model.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("model, url, body, key", KINDS)
class TestDocumentContactOnUpdate:
    def test_member_keeps_a_stored_contact_they_cannot_open(
        self, model, url, body, key, user_client, org_a, account, hidden, regular_user
    ):
        """An edit form sends back the contact it loaded."""
        document = _stored(model, org_a, account, hidden, regular_user)

        response = user_client.put(
            f"{url}{document.id}/", body(account, hidden), format="json"
        )

        assert response.status_code == 200, response.content
        document.refresh_from_db()
        assert document.contact == hidden

    def test_member_cannot_switch_to_another_contact_they_cannot_open(
        self,
        model,
        url,
        body,
        key,
        user_client,
        org_a,
        account,
        mine,
        hidden,
        regular_user,
    ):
        document = _stored(model, org_a, account, mine, regular_user)

        response = user_client.put(
            f"{url}{document.id}/", body(account, hidden), format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["contact_id"] == [REFUSED]
        document.refresh_from_db()
        assert document.contact == mine


def _newer(contact):
    """Make ``contact`` the newest, so the old `.first()` would pick it."""
    Contact.objects.filter(id=contact.id).update(
        created_at=timezone.now() + datetime.timedelta(hours=1)
    )


def _won_deal(org, account, creator, *contacts):
    deal = Opportunity.objects.create(
        name="Won deal",
        org=org,
        account=account,
        stage="CLOSED_WON",
        amount=Decimal("100"),
        currency="USD",
        closed_on=timezone.localdate(),
        created_by=creator,
    )
    deal.contacts.add(*contacts)
    OpportunityLineItem.objects.create(
        opportunity=deal,
        org=org,
        name="Widget",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
    )
    return deal


@pytest.mark.django_db
class TestInvoiceFromDealPicksAVisibleContact:
    def _url(self, deal):
        return f"/api/invoices/from-opportunity/{deal.id}/"

    def test_member_gets_the_contact_they_can_open(
        self, user_client, org_a, account, mine, hidden, regular_user
    ):
        _newer(hidden)
        deal = _won_deal(org_a, account, regular_user, mine, hidden)

        response = user_client.post(self._url(deal))

        assert response.status_code == 201, response.content
        assert Invoice.objects.get(opportunity=deal).contact == mine

    def test_member_with_only_hidden_contacts_gets_no_invoice(
        self, user_client, org_a, account, hidden, regular_user
    ):
        deal = _won_deal(org_a, account, regular_user, hidden)

        response = user_client.post(self._url(deal))

        assert response.status_code == 400, response.content
        assert not Invoice.objects.exists()

    def test_admin_may_bill_any_contact_on_the_deal(
        self, admin_client, org_a, account, hidden, regular_user
    ):
        deal = _won_deal(org_a, account, regular_user, hidden)

        response = admin_client.post(self._url(deal))

        assert response.status_code == 201, response.content
        assert Invoice.objects.get(opportunity=deal).contact == hidden


@pytest.mark.django_db
class TestInvoiceFromTimeEntriesPicksAVisibleContact:
    """Only admins and superusers may bill time, so a member never reaches the
    contact pick here: their refusal is pinned in
    ``test_invoice_from_time_entries_authz.py``."""

    URL = "/api/invoices/from-time-entries/"

    def _post(self, client, org, account, profile, creator):
        case = Case.objects.create(
            name="Billable work",
            status="New",
            priority="Normal",
            org=org,
            account=account,
            created_by=creator,
        )
        entry = TimeEntry.objects.create(
            org=org,
            case=case,
            profile=profile,
            started_at=timezone.now() - datetime.timedelta(hours=2),
            ended_at=timezone.now() - datetime.timedelta(hours=1),
            billable=True,
            hourly_rate=Decimal("100.00"),
            currency="USD",
        )
        return client.post(
            self.URL,
            {"account_id": str(account.id), "entry_ids": [str(entry.id)]},
            format="json",
        )

    def test_admin_gets_the_newest_contact(
        self, admin_client, org_a, account, mine, hidden, admin_profile, admin_user
    ):
        _newer(hidden)
        account.contacts.add(mine, hidden)

        response = self._post(admin_client, org_a, account, admin_profile, admin_user)

        assert response.status_code == 201, response.content
        assert Invoice.objects.get().contact == hidden
