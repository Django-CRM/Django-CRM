"""The estimate and recurring-invoice lists read through the shared helpers.

Both lists used to carry inline copies of the read rule (D40). They now call
`visible_estimates_qs` and `visible_recurring_qs`, beside `visible_invoices_qs`,
and these tests pin that the rows each caller sees did not change: an admin
and a superuser see the whole org, a member sees what they created or are
assigned, and nobody sees another org's records.
"""

import datetime

import pytest

from accounts.models import Account
from common.tests.export_personas import make_personas, stamp_creator
from invoices.models import Estimate, RecurringInvoice

ESTIMATES_URL = "/api/invoices/estimates/"
RECURRING_URL = "/api/invoices/recurring/"

EXPECTED = {
    "admin": {"Assigned", "Created", "Nobody's"},
    "superuser": {"Assigned", "Created", "Nobody's"},
    "assignee": {"Assigned"},
    "creator": {"Created"},
    "unrelated": set(),
}


def _seed(model, org, org_b, people, **fields):
    account = Account.objects.create(name="Acme", org=org)

    def make(title, owner_org=org, owner_account=account):
        return model.objects.create(
            org=owner_org, account=owner_account, title=title, **fields
        )

    make("Assigned").assigned_to.add(people["assignee"].profile)
    stamp_creator(model, make("Created"), people["creator"].user)
    make("Nobody's")
    make(
        "Other org",
        owner_org=org_b,
        owner_account=Account.objects.create(name="Elsewhere", org=org_b),
    )


@pytest.fixture
def people(org_a):
    return make_personas(org_a)


@pytest.mark.django_db
@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_estimate_list_rows_per_caller(org_a, org_b, people, who):
    _seed(Estimate, org_a, org_b, people)

    response = people[who].client().get(ESTIMATES_URL, {"limit": 50})

    assert response.status_code == 200
    assert {e["title"] for e in response.data["results"]} == EXPECTED[who]


@pytest.mark.django_db
@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_recurring_list_rows_per_caller(org_a, org_b, people, who):
    today = datetime.date(2026, 9, 1)
    _seed(
        RecurringInvoice,
        org_a,
        org_b,
        people,
        start_date=today,
        next_generation_date=today,
    )

    response = people[who].client().get(RECURRING_URL, {"limit": 50})

    assert response.status_code == 200
    assert {r["title"] for r in response.data["results"]} == EXPECTED[who]


@pytest.mark.django_db
def test_estimate_list_refuses_a_malformed_account_filter(people):
    response = people["admin"].client().get(ESTIMATES_URL, {"account": "not-a-uuid"})

    assert response.status_code == 400
