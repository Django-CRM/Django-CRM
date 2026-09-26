"""What a ticket, deal or invoice says about its account, and what a picker says
about a contact.

1. `CaseSerializer`, `OpportunitySerializer`, `InvoiceSerializer`,
   `EstimateSerializer` and `RecurringInvoiceSerializer` nested the full
   `AccountSerializer`, so anyone who could open the ticket read the account's
   email, phone, address, tags and assignees, account access or not. Both
   clients read only `id` and `name` of that nested account (web:
   `lib/server/v2/{tickets,deals,invoices,approvals}.js`; mobile:
   `data/models/{ticket,deal,invoice}.dart`), so that is all it now carries,
   for every viewer.
2. `contacts_list` on `/api/cases/`, `/api/opportunities/` and `/api/tasks/`
   feeds a contact select, and carried each contact's full record. It now
   carries the fields the web select reads, and mobile reads none.
"""

import datetime
from decimal import Decimal

import pytest

from accounts.models import Account
from cases.models import Case
from cases.serializer import CaseSerializer
from common.models import Tags
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from invoices.serializer import (
    EstimateSerializer,
    InvoiceSerializer,
    RecurringInvoiceSerializer,
)
from opportunity.models import Opportunity
from opportunity.serializer import OpportunitySerializer

PICKER_FIELDS = {"id", "first_name", "last_name"}


@pytest.fixture
def account(org_a, admin_profile):
    account = Account.objects.create(
        name="Private Co",
        email="ceo@private.example",
        phone="+15550100",
        address_line="1 Secret Lane",
        city="Hidden",
        org=org_a,
    )
    account.tags.add(Tags.objects.create(name="VIP", org=org_a))
    account.assigned_to.add(admin_profile)
    return account


def _only_id_and_name(nested, account):
    assert nested == {"id": str(account.id), "name": account.name}


@pytest.mark.django_db
class TestNestedAccountOverTheApi:
    @pytest.fixture
    def case(self, org_a, account, user_profile):
        case = Case.objects.create(
            name="Their ticket",
            org=org_a,
            account=account,
            status="New",
            priority="Normal",
            case_type="Question",
        )
        case.assigned_to.add(user_profile)
        return case

    @pytest.fixture
    def deal(self, org_a, account, user_profile):
        deal = Opportunity.objects.create(
            name="Their deal", org=org_a, account=account, stage="PROPOSAL"
        )
        deal.assigned_to.add(user_profile)
        return deal

    def test_case_viewer_without_account_access_gets_id_and_name(
        self, user_client, account, case
    ):
        assert user_client.get(f"/api/accounts/{account.id}/").status_code == 403
        response = user_client.get(f"/api/cases/{case.id}/")
        assert response.status_code == 200, response.content
        _only_id_and_name(response.json()["cases_obj"]["account"], account)

    def test_deal_viewer_without_account_access_gets_id_and_name(
        self, user_client, account, deal
    ):
        response = user_client.get(f"/api/opportunities/{deal.id}/")
        assert response.status_code == 200, response.content
        _only_id_and_name(response.json()["opportunity_obj"]["account"], account)

    def test_the_case_list_carries_id_and_name(self, user_client, account, case):
        (row,) = user_client.get("/api/cases/").json()["cases"]
        _only_id_and_name(row["account"], account)

    def test_account_viewer_gets_what_the_clients_read(
        self, user_client, user_profile, account, case, deal
    ):
        """Opening the account is where its details live, not the ticket."""
        account.assigned_to.add(user_profile)
        assert user_client.get(f"/api/accounts/{account.id}/").status_code == 200
        case_body = user_client.get(f"/api/cases/{case.id}/").json()
        deal_body = user_client.get(f"/api/opportunities/{deal.id}/").json()
        _only_id_and_name(case_body["cases_obj"]["account"], account)
        _only_id_and_name(deal_body["opportunity_obj"]["account"], account)


@pytest.mark.django_db
def test_every_record_serializer_nests_id_and_name_only(org_a, account):
    """The invoice family has the same nesting; checked at the serializer."""
    today = datetime.date.today()
    records = [
        (
            CaseSerializer,
            Case.objects.create(
                name="T", org=org_a, account=account, status="New", priority="Normal"
            ),
        ),
        (
            OpportunitySerializer,
            Opportunity.objects.create(
                name="D", org=org_a, account=account, stage="PROPOSAL"
            ),
        ),
        (
            InvoiceSerializer,
            Invoice.objects.create(invoice_title="I", org=org_a, account=account),
        ),
        (
            EstimateSerializer,
            Estimate.objects.create(title="E", org=org_a, account=account),
        ),
        (
            RecurringInvoiceSerializer,
            RecurringInvoice.objects.create(
                title="R",
                frequency="MONTHLY",
                start_date=today,
                next_generation_date=today,
                org=org_a,
                account=account,
            ),
        ),
    ]
    for serializer, record in records:
        _only_id_and_name(serializer(record).data["account"], account)


@pytest.mark.django_db
class TestContactPickers:
    @pytest.fixture
    def contact(self, org_a):
        return Contact.objects.create(
            first_name="Pat",
            last_name="Lee",
            email="pat@example.com",
            phone="+15550199",
            address_line="2 Home Road",
            description="Private notes",
            org=org_a,
        )

    @pytest.mark.parametrize(
        "path", ["/api/cases/", "/api/opportunities/", "/api/tasks/"]
    )
    def test_contacts_list_carries_picker_fields_only(
        self, admin_client, contact, path
    ):
        response = admin_client.get(path)
        assert response.status_code == 200, response.content
        (row,) = response.json()["contacts_list"]
        assert set(row) == PICKER_FIELDS
        assert row == {
            "id": str(contact.id),
            "first_name": "Pat",
            "last_name": "Lee",
        }

    def test_contact_picker_is_still_narrowed_for_a_member(
        self, user_client, contact, org_a, regular_user
    ):
        mine = Contact.objects.create(first_name="Mine", last_name="M", org=org_a)
        Contact.objects.filter(pk=mine.pk).update(created_by=regular_user)
        rows = user_client.get("/api/tasks/").json()["contacts_list"]
        assert [r["id"] for r in rows] == [str(mine.id)]


@pytest.mark.django_db
def test_amount_field_survives_on_the_deal(user_client, user_profile, org_a, account):
    """Guard: trimming the nested account must not touch the deal's own fields."""
    deal = Opportunity.objects.create(
        name="Priced",
        org=org_a,
        account=account,
        stage="PROPOSAL",
        amount=Decimal("42"),
    )
    deal.assigned_to.add(user_profile)
    body = user_client.get(f"/api/opportunities/{deal.id}/").json()
    assert Decimal(body["opportunity_obj"]["amount"]) == Decimal("42")
