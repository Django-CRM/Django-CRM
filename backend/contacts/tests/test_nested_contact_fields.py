"""What a ticket, deal, task, lead or invoice says about its contacts.

Every one of them nested the full `ContactSerializer`, so anyone who could open
the parent read each linked person's phone, address, notes, assignees and
account links, whether or not they could open that contact. The clients read
far less:

- ticket: web `lib/server/v2/tickets.js` reads id and the name; mobile
  `data/models/ticket.dart` falls back to `email` when the name is blank.
- deal: mobile `deal_detail_screen.dart` shows each contact's name and email.
- task: web `lib/server/v2/tasks.js` reads the name and the email.
- lead: neither client reads `contacts`.
- invoice, estimate, recurring: mobile `invoice.dart` reads the name, the web
  reads `contact_name` from the list rows instead.

So the ticket, deal and task nest `{id, first_name, last_name, email}`, and the
lead and the invoice family nest the picker's `{id, first_name, last_name}`.
"""

import datetime

import pytest

from cases.models import Case
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from invoices.serializer import (
    EstimateSerializer,
    InvoiceSerializer,
    RecurringInvoiceSerializer,
)
from leads.models import Lead
from leads.serializer import LeadSerializer
from opportunity.models import Opportunity
from tasks.models import Task

NAME_FIELDS = {"id", "first_name", "last_name"}
LINK_FIELDS = NAME_FIELDS | {"email"}


@pytest.fixture
def person(org_a, admin_user):
    contact = Contact.objects.create(
        first_name="Pat",
        last_name="Private",
        email="pat@private.example",
        phone="+15550100",
        address_line="1 Secret Lane",
        city="Hidden",
        description="Owes us money",
        org=org_a,
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=admin_user)
    return contact


def _assert_link(nested, person):
    assert nested == {
        "id": str(person.id),
        "first_name": "Pat",
        "last_name": "Private",
        "email": "pat@private.example",
    }


@pytest.mark.django_db
class TestLinkedContactsOverTheApi:
    """A non-admin assignee of the parent, who cannot open the contact."""

    def test_the_viewer_cannot_open_the_contact(self, user_client, person):
        assert user_client.get(f"/api/contacts/{person.id}/").status_code == 403

    def test_ticket_carries_name_and_email_only(
        self, user_client, org_a, user_profile, person
    ):
        case = Case.objects.create(
            name="T", org=org_a, status="New", priority="Normal", case_type="Question"
        )
        case.assigned_to.add(user_profile)
        case.contacts.add(person)

        body = user_client.get(f"/api/cases/{case.id}/").json()
        (nested,) = body["cases_obj"]["contacts"]
        _assert_link(nested, person)
        # The detail response repeats the same people at the top level.
        (repeated,) = body["contacts"]
        _assert_link(repeated, person)

    def test_deal_carries_name_and_email_only(
        self, user_client, org_a, user_profile, person
    ):
        deal = Opportunity.objects.create(name="D", org=org_a, stage="PROPOSAL")
        deal.assigned_to.add(user_profile)
        deal.contacts.add(person)

        body = user_client.get(f"/api/opportunities/{deal.id}/").json()
        (nested,) = body["opportunity_obj"]["contacts"]
        _assert_link(nested, person)

    def test_task_carries_name_and_email_only(
        self, user_client, org_a, user_profile, person
    ):
        task = Task.objects.create(
            title="Call", org=org_a, status="New", priority="Low"
        )
        task.assigned_to.add(user_profile)
        task.contacts.add(person)

        response = user_client.get(f"/api/tasks/{task.id}/")
        assert response.status_code == 200, response.content
        (nested,) = response.json()["task_obj"]["contacts"]
        _assert_link(nested, person)


@pytest.mark.django_db
def test_lead_nests_the_name_only(org_a, person):
    lead = Lead.objects.create(first_name="L", last_name="Q", org=org_a)
    lead.contacts.add(person)
    (nested,) = LeadSerializer(lead).data["contacts"]
    assert set(nested) == NAME_FIELDS


@pytest.mark.django_db
def test_invoice_family_nests_the_name_only(org_a, person):
    today = datetime.date.today()
    records = [
        (
            InvoiceSerializer,
            Invoice.objects.create(invoice_title="I", org=org_a, contact=person),
        ),
        (
            EstimateSerializer,
            Estimate.objects.create(title="E", org=org_a, contact=person),
        ),
        (
            RecurringInvoiceSerializer,
            RecurringInvoice.objects.create(
                title="R",
                frequency="MONTHLY",
                start_date=today,
                next_generation_date=today,
                org=org_a,
                contact=person,
            ),
        ),
    ]
    for serializer, record in records:
        assert serializer(record).data["contact"] == {
            "id": str(person.id),
            "first_name": "Pat",
            "last_name": "Private",
        }, serializer.__name__
