"""The contact page lists only related records the viewer may open.

A non-admin who can open a contact used to see every deal, ticket and task
naming that person, and every colleague at the same company, including records
the detail endpoints of those modules refuse them. Each list now applies its
module's own read rule.
"""

from decimal import Decimal

import pytest
from rest_framework import status

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from opportunity.models import Opportunity
from tasks.models import Task

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(org_a, admin_user, user_profile):
    """A contact the non-admin is assigned to, with one visible and one hidden
    record of each related kind."""
    contact = Contact.objects.create(
        first_name="Ada", last_name="Lovelace", org=org_a, created_by=admin_user
    )
    contact.assigned_to.add(user_profile)

    def related(model, visible, **fields):
        obj = model.objects.create(org=org_a, created_by=admin_user, **fields)
        obj.contacts.add(contact)
        if visible:
            obj.assigned_to.add(user_profile)
        return obj

    for visible, label in ((True, "Mine"), (False, "Hidden")):
        related(Opportunity, visible, name=f"{label} deal", amount="10")
        related(Case, visible, name=f"{label} ticket", status="New")
        related(Task, visible, title=f"{label} task", status="New", priority="Low")

    account_contacts = []
    for visible, label in ((True, "Mine"), (False, "Hidden")):
        colleague = Contact.objects.create(
            first_name=f"{label}",
            last_name="Colleague",
            org=org_a,
            created_by=admin_user,
        )
        if visible:
            colleague.assigned_to.add(user_profile)
        account_contacts.append(colleague)
    account = Account.objects.create(name="Analytical Engines", org=org_a)
    account.contacts.add(contact, *account_contacts)
    return contact


def _names(data):
    return {
        "deals": {d["name"] for d in data["opportunities"]},
        "tickets": {c["name"] for c in data["cases"]},
        "tasks": {t["title"] for t in data["tasks"]},
        "colleagues": {c["first_name"] for c in data["colleagues"]},
    }


def test_non_admin_sees_only_related_records_they_may_open(user_client, setup):
    response = user_client.get(f"/api/contacts/{setup.pk}/")
    assert response.status_code == status.HTTP_200_OK
    assert _names(response.data) == {
        "deals": {"Mine deal"},
        "tickets": {"Mine ticket"},
        "tasks": {"Mine task"},
        "colleagues": {"Mine"},
    }


def test_admin_sees_every_related_record(admin_client, setup):
    response = admin_client.get(f"/api/contacts/{setup.pk}/")
    assert response.status_code == status.HTTP_200_OK
    assert _names(response.data) == {
        "deals": {"Mine deal", "Hidden deal"},
        "tickets": {"Mine ticket", "Hidden ticket"},
        "tasks": {"Mine task", "Hidden task"},
        "colleagues": {"Mine", "Hidden"},
    }


def test_open_deal_summary_counts_every_visible_open_deal(user_client, setup, org_a):
    """The page's "N open deals worth X" used to count the 10-row list, so a
    contact on more than 10 deals was undercounted. The summary is computed on
    the server over every open deal the viewer may open, per currency."""
    org_a.default_currency = "USD"
    org_a.save()
    user = setup.assigned_to.first()
    for i in range(11):
        deal = Opportunity.objects.create(
            org=org_a, name=f"Big {i}", amount="100", currency="USD"
        )
        deal.contacts.add(setup)
        deal.assigned_to.add(user)
    euro = Opportunity.objects.create(
        org=org_a, name="Euro", amount="5", currency="EUR"
    )
    euro.contacts.add(setup)
    euro.assigned_to.add(user)
    won = Opportunity.objects.create(
        org=org_a, name="Won", amount="999", currency="USD", stage="CLOSED_WON"
    )
    won.contacts.add(setup)
    won.assigned_to.add(user)

    response = user_client.get(f"/api/contacts/{setup.pk}/")

    summary = response.data["open_deals"]
    # 11 USD + 1 EUR + the fixture's visible "Mine deal" (10, blank currency
    # counts as the org default). The hidden deal and the won deal are out.
    assert summary["count"] == 13
    assert summary["amount"] is None
    # Amounts compared as numbers: SQLite and Postgres render the sum's scale
    # differently.
    assert [
        (row["currency"], row["count"], Decimal(row["amount"]))
        for row in summary["by_currency"]
    ] == [("EUR", 1, Decimal("5")), ("USD", 12, Decimal("1110"))]
    assert len(response.data["opportunities"]) == 10
    # The edit form's "N deals" reads this uncapped count of visible deals:
    # 11 USD, the EUR one, the won one and the fixture's own "Mine deal".
    assert response.data["opportunity_count"] == 14


def test_deal_with_two_assignees_counts_once(
    user_client, setup, org_a, regular_user, admin_profile
):
    """Visible because the viewer created it, with two other assignees: the
    visibility join yields one row per assignee, which must not double it."""
    from common.models import Profile, User

    third = User.objects.create_user(email="third@test.com", password="x")
    other = Profile.objects.create(user=third, org=org_a, role="USER")
    deal = Opportunity.objects.create(
        org=org_a, name="Shared", amount="7", currency="EUR", created_by=regular_user
    )
    deal.contacts.add(setup)
    deal.assigned_to.add(admin_profile, other)

    summary = user_client.get(f"/api/contacts/{setup.pk}/").data["open_deals"]

    eur = next(row for row in summary["by_currency"] if row["currency"] == "EUR")
    assert (eur["count"], Decimal(eur["amount"])) == (1, Decimal("7"))
