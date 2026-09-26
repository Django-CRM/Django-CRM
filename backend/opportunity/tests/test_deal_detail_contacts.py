"""The deal detail GET's top-level `contacts`: visible people, picker fields.

It sent every linked contact as a full `ContactSerializer` record, so opening
a deal handed over the phone, address and notes of people the viewer could
not open. It now lists only the contacts the viewer may open, with the fields
the web reads (name, `title`, `department`).
"""

import pytest

from contacts.models import Contact
from opportunity.models import Opportunity

FIELDS = {"id", "first_name", "last_name", "title", "department"}


@pytest.fixture
def deal(org_a, regular_user, admin_user):
    deal = Opportunity.objects.create(
        name="Shared deal", org=org_a, stage="PROSPECTING", created_by=regular_user
    )
    mine = Contact.objects.create(
        first_name="Mine",
        last_name="Visible",
        title="CTO",
        department="Engineering",
        email="mine@example.com",
        phone="+15550000001",
        description="Private notes",
        org=org_a,
        created_by=regular_user,
    )
    hidden = Contact.objects.create(
        first_name="Hidden",
        last_name="Person",
        email="hidden@example.com",
        phone="+15550000002",
        org=org_a,
        created_by=admin_user,
    )
    deal.contacts.add(mine, hidden)
    deal.mine, deal.hidden = mine, hidden
    return deal


@pytest.mark.django_db
class TestDealDetailContacts:
    def test_member_sees_only_contacts_they_can_open(self, user_client, deal):
        response = user_client.get(f"/api/opportunities/{deal.id}/")

        assert response.status_code == 200, response.content
        contacts = response.json()["contacts"]
        assert [c["id"] for c in contacts] == [str(deal.mine.id)]
        assert contacts[0]["title"] == "CTO"
        assert contacts[0]["department"] == "Engineering"

    def test_only_the_fields_the_client_reads_are_sent(self, user_client, deal):
        response = user_client.get(f"/api/opportunities/{deal.id}/")

        (contact,) = response.json()["contacts"]
        assert set(contact) == FIELDS

    def test_admin_sees_every_contact_on_the_deal(self, admin_client, deal):
        response = admin_client.get(f"/api/opportunities/{deal.id}/")

        assert response.status_code == 200, response.content
        contacts = response.json()["contacts"]
        assert {c["id"] for c in contacts} == {str(deal.mine.id), str(deal.hidden.id)}
        assert all(set(c) == FIELDS for c in contacts)
