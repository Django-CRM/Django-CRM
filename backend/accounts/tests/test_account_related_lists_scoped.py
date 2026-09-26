"""No response embeds an account's deals, tickets, tasks or contacts unasked.

`AccountSerializer` carries the account's related records as nested lists. Only
`AccountDetailView` narrowed them to what the viewer may open. Every other
caller (the account list rows, the `accounts_list` pickers on `/api/cases/`,
`/api/opportunities/` and `/api/tasks/`, and the `account` nested inside a
ticket, deal or invoice) handed any org member every deal name, stage and
amount, every ticket and every task on the account. The serializer now emits
those lists only for a caller that names the viewer, and the pickers send only
`id` and `name`, which is all either client reads from them.
"""

import json

import pytest
from rest_framework import status

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from invoices.models import Invoice
from opportunity.models import Opportunity
from tasks.models import Task

pytestmark = pytest.mark.django_db

HIDDEN_AMOUNT = "424242"
HIDDEN = ("Hidden deal", "Hidden ticket", "Hidden task", "Hidden Person")
RELATED = ("opportunities", "cases", "tasks", "contacts")


@pytest.fixture
def world(org_a, admin_user, user_profile):
    """An account the member is assigned to, carrying one record of each kind
    they may open and one they may not, plus an account they cannot open."""
    account = Account.objects.create(name="Analytical Engines", org=org_a)
    Account.objects.filter(pk=account.pk).update(created_by=admin_user)
    account.assigned_to.add(user_profile)

    stranger = Account.objects.create(name="Stranger Account", org=org_a)
    Account.objects.filter(pk=stranger.pk).update(created_by=admin_user)

    def related(model, visible, **fields):
        obj = model.objects.create(org=org_a, account=account, **fields)
        model.objects.filter(pk=obj.pk).update(created_by=admin_user)
        if visible:
            obj.assigned_to.add(user_profile)
        return obj

    mine = {
        "deal": related(Opportunity, True, name="Mine deal", amount="10"),
        "ticket": related(Case, True, name="Mine ticket", status="New"),
        "task": related(Task, True, title="Mine task", status="New", priority="Low"),
        "invoice": related(
            Invoice, True, invoice_title="Mine invoice", invoice_number="M-1"
        ),
    }
    related(Opportunity, False, name="Hidden deal", amount=HIDDEN_AMOUNT)
    related(Case, False, name="Hidden ticket", status="New")
    related(Task, False, title="Hidden task", status="New", priority="Low")

    person = Contact.objects.create(
        first_name="Hidden", last_name="Person", email="hp@example.com", org=org_a
    )
    Contact.objects.filter(pk=person.pk).update(created_by=admin_user)
    account.contacts.add(person)
    return {"account": account, "stranger": stranger, **mine}


def _assert_nothing_hidden(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    text = json.dumps(response.json())
    for name in HIDDEN:
        assert name not in text, name
    assert HIDDEN_AMOUNT not in text


def _assert_no_related_lists(account_json):
    for key in RELATED:
        assert key not in account_json, key


def _picker(rows):
    return {row["name"]: set(row) for row in rows}


class TestAccountList:
    def test_member_rows_carry_no_related_lists(self, user_client, world):
        response = user_client.get("/api/accounts/")
        _assert_nothing_hidden(response)
        rows = response.json()["active_accounts"]["open_accounts"]
        assert [r["name"] for r in rows] == ["Analytical Engines"]
        _assert_no_related_lists(rows[0])

    def test_admin_rows_keep_what_the_clients_read(self, admin_client, world):
        response = admin_client.get("/api/accounts/")
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["active_accounts"]["open_accounts"]
        row = next(r for r in rows if r["name"] == "Analytical Engines")
        assert row["id"] == str(world["account"].id)
        assert row["rollups"]["open_deal_count"] == 2
        _assert_no_related_lists(row)


@pytest.mark.parametrize("path", ["/api/cases/", "/api/opportunities/", "/api/tasks/"])
class TestAccountPickers:
    def test_member_picker_is_id_and_name_of_openable_accounts(
        self, user_client, world, path
    ):
        response = user_client.get(path)
        _assert_nothing_hidden(response)
        assert _picker(response.json()["accounts_list"]) == {
            "Analytical Engines": {"id", "name"}
        }

    def test_admin_picker_lists_every_account(self, admin_client, world, path):
        response = admin_client.get(path)
        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["accounts_list"]
        assert _picker(rows) == {
            "Analytical Engines": {"id", "name"},
            "Stranger Account": {"id", "name"},
        }
        ids = {r["name"]: r["id"] for r in rows}
        assert ids["Analytical Engines"] == str(world["account"].id)


class TestNestedAccount:
    """A ticket, deal or invoice the member may open nests its account, and
    that nested account must not re-serve the siblings they may not open."""

    @pytest.mark.parametrize(
        "path",
        [
            lambda w: f"/api/cases/{w['ticket'].id}/",
            lambda w: f"/api/opportunities/{w['deal'].id}/",
            lambda w: f"/api/invoices/{w['invoice'].id}/",
            lambda w: "/api/cases/?slim=true",
            lambda w: "/api/opportunities/",
        ],
        ids=["case-detail", "deal-detail", "invoice-detail", "case-list", "deal-list"],
    )
    def test_member_gets_no_hidden_siblings(self, user_client, world, path):
        _assert_nothing_hidden(user_client.get(path(world)))

    def test_admin_nested_account_keeps_id_and_name(self, admin_client, world):
        response = admin_client.get(f"/api/cases/{world['ticket'].id}/")
        assert response.status_code == status.HTTP_200_OK
        nested = response.json()["cases_obj"]["account"]
        assert nested["id"] == str(world["account"].id)
        assert nested["name"] == "Analytical Engines"
        _assert_no_related_lists(nested)


def test_serializer_emits_related_lists_only_for_a_named_viewer(world, admin_profile):
    """The fail-closed default, pinned at the serializer itself."""
    from accounts.serializer import AccountSerializer

    account = world["account"]
    _assert_no_related_lists(AccountSerializer(account).data)
    data = AccountSerializer(account, context={"profile": admin_profile}).data
    assert {d["name"] for d in data["opportunities"]} == {"Mine deal", "Hidden deal"}
    assert {c["name"] for c in data["cases"]} == {"Mine ticket", "Hidden ticket"}
    assert {t["title"] for t in data["tasks"]} == {"Mine task", "Hidden task"}
    assert [c["first_name"] for c in data["contacts"]] == ["Hidden"]


@pytest.mark.django_db
def test_nested_deals_carry_the_stage_label_in_one_query(org_a, admin_profile):
    """Stages are configurable, so a code alone reads as `DEMO_BOOKED`."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from accounts.serializer import AccountSerializer
    from opportunity.models import DealStage

    account = Account.objects.create(name="Labels Ltd", org=org_a)
    Opportunity.objects.create(
        name="One", stage="PROSPECTING", account=account, org=org_a
    )
    DealStage.objects.filter(org=org_a, code="PROSPECTING").update(
        label="Discovery call"
    )

    def serialize():
        with CaptureQueriesContext(connection) as queries:
            deals = AccountSerializer(account, context={"profile": admin_profile}).data[
                "opportunities"
            ]
        return deals, len(queries)

    deals, one_deal = serialize()
    assert deals[0]["stage"] == "PROSPECTING"
    assert deals[0]["stage_label"] == "Discovery call"

    for name in ("Two", "Three"):
        Opportunity.objects.create(
            name=name, stage="PROSPECTING", account=account, org=org_a
        )
    deals, three_deals = serialize()
    assert {d["stage_label"] for d in deals} == {"Discovery call"}
    assert three_deals == one_deal
