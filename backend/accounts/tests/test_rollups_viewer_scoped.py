"""Account rollups count only the records the viewer may open.

`annotate_rollups` and `attach_money_rollups` used to total every deal, ticket
and invoice on the account. A member assigned to the account but to one deal of
ten could subtract the deals they can open from the "Won" figure and read the
value of the nine they cannot. Every rollup is now computed over the viewer's
read rule for that module (`visible_deals_qs`, `visible_cases_qs`,
`visible_invoices_qs`); an admin's rule is the whole org, so admin totals are
what they always were.
"""

import datetime
from decimal import Decimal

import pytest

from accounts.models import Account
from cases.models import Case
from invoices.models import Invoice
from opportunity.models import Opportunity

EARLY = datetime.date(2023, 1, 1)
LATER = datetime.date(2024, 6, 1)
PAST_DUE = datetime.date(2020, 1, 1)


def _invoice(org, account, number, amount_due):
    """An overdue invoice owing ``amount_due``; see the sibling rollup tests."""
    invoice = Invoice.objects.create(
        invoice_title=number,
        invoice_number=number,
        org=org,
        account=account,
        status="Sent",
        due_date=PAST_DUE,
    )
    Invoice.objects.filter(pk=invoice.pk).update(
        total_amount=amount_due, amount_paid=Decimal("0"), amount_due=amount_due
    )
    return invoice


@pytest.fixture
def account(org_a, user_profile):
    account = Account.objects.create(name="Shared Co", org=org_a)
    account.assigned_to.add(user_profile)
    return account


@pytest.fixture
def records(org_a, account, user_profile, admin_profile):
    """Half of everything is the member's, half is somebody else's.

    The member's won deal has two assignees on purpose: the visibility filter
    joins the assignee table, so summing over it directly counts that deal
    twice. The hidden won deal closed first, so `first_won_on` would leak its
    date too.
    """
    mine_won = Opportunity.objects.create(
        name="Mine won",
        org=org_a,
        account=account,
        stage="CLOSED_WON",
        amount=Decimal("100"),
        currency="USD",
        closed_on=LATER,
    )
    mine_won.assigned_to.add(user_profile, admin_profile)
    Opportunity.objects.create(
        name="Hidden won",
        org=org_a,
        account=account,
        stage="CLOSED_WON",
        amount=Decimal("900"),
        currency="USD",
        closed_on=EARLY,
    )
    mine_open = Opportunity.objects.create(
        name="Mine open",
        org=org_a,
        account=account,
        stage="PROPOSAL",
        amount=Decimal("50"),
        currency="USD",
    )
    mine_open.assigned_to.add(user_profile)
    Opportunity.objects.create(
        name="Hidden open",
        org=org_a,
        account=account,
        stage="PROPOSAL",
        amount=Decimal("500"),
        currency="USD",
    )
    for name, assignee in (("Mine ticket", user_profile), ("Hidden ticket", None)):
        case = Case.objects.create(
            name=name,
            org=org_a,
            account=account,
            status="New",
            priority="Normal",
            case_type="Question",
        )
        if assignee:
            case.assigned_to.add(assignee, admin_profile)
    mine_invoice = _invoice(org_a, account, "MINE-1", Decimal("30"))
    mine_invoice.assigned_to.add(user_profile, admin_profile)
    _invoice(org_a, account, "HIDDEN-1", Decimal("300"))


def _detail(client, account):
    response = client.get(f"/api/accounts/{account.id}/")
    assert response.status_code == 200, response.content
    return response.json()


def _listed(client, account):
    rows = client.get("/api/accounts/").json()["active_accounts"]["open_accounts"]
    return next(row for row in rows if row["id"] == str(account.id))["rollups"]


@pytest.mark.django_db
class TestMemberRollups:
    def test_detail_totals_only_what_the_member_can_open(
        self, user_client, account, records
    ):
        rollups = _detail(user_client, account)["account_obj"]["rollups"]

        assert rollups["won_count"] == 1
        assert Decimal(rollups["won_amount"]) == Decimal("100")
        assert rollups["first_won_on"] == LATER.isoformat()
        assert rollups["open_deal_count"] == 1
        assert Decimal(rollups["open_pipeline"]) == Decimal("50")
        assert rollups["open_tickets"] == 1
        assert Decimal(rollups["overdue_amount"]) == Decimal("30")

    def test_the_list_row_agrees_with_the_detail(self, user_client, account, records):
        assert (
            _listed(user_client, account)
            == _detail(user_client, account)["account_obj"]["rollups"]
        )

    def test_nothing_is_left_to_subtract(self, user_client, account, records):
        """The rollup equals the sum of the rows the same page lists.

        So `rollup - listed` is zero and reveals nothing the member could not
        already open.
        """
        body = _detail(user_client, account)
        rollups = body["account_obj"]["rollups"]
        deals = body["opportunity_list"]

        won = sum(Decimal(d["amount"]) for d in deals if d["stage"] == "CLOSED_WON")
        live = sum(Decimal(d["amount"]) for d in deals if d["stage"] == "PROPOSAL")
        assert Decimal(rollups["won_amount"]) == won
        assert Decimal(rollups["open_pipeline"]) == live
        assert rollups["open_tickets"] == len(body["cases"])
        assert Decimal(rollups["overdue_amount"]) == sum(
            Decimal(i["amount_due"]) for i in body["invoices"]
        )

    def test_member_with_no_records_sees_zero_not_the_accounts_total(
        self, user_client, account, org_a
    ):
        Opportunity.objects.create(
            name="Hidden",
            org=org_a,
            account=account,
            stage="CLOSED_WON",
            amount=Decimal("700"),
            closed_on=EARLY,
        )

        rollups = _detail(user_client, account)["account_obj"]["rollups"]

        assert rollups["won_count"] == 0
        assert Decimal(rollups["won_amount"]) == Decimal("0")
        assert rollups["first_won_on"] is None
        assert rollups["by_currency"] == []


@pytest.mark.django_db
class TestAdminRollupsUnchanged:
    def test_admin_totals_everything(self, admin_client, account, records):
        rollups = _detail(admin_client, account)["account_obj"]["rollups"]

        assert rollups["won_count"] == 2
        assert Decimal(rollups["won_amount"]) == Decimal("1000")
        assert rollups["first_won_on"] == EARLY.isoformat()
        assert rollups["open_deal_count"] == 2
        assert Decimal(rollups["open_pipeline"]) == Decimal("550")
        assert rollups["open_tickets"] == 2
        assert Decimal(rollups["overdue_amount"]) == Decimal("330")
        assert _listed(admin_client, account) == rollups
