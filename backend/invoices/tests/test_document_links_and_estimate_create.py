"""Creating an estimate, the account and deal a document may carry, and
raising an invoice from a won deal.

`validate_account_id` and `validate_opportunity_id` on the three document
create serializers (invoice, estimate, recurring) checked the org only, so a
member could bill an account or cite a deal they cannot open and read its name
back through a document they own as its creator. They now use the account and
deal modules' own visibility querysets, with the one message for a hidden
record, another org's and one that does not exist.

`InvoiceFromOpportunityView` carried a hand-copied access rule that answered
403 for a hidden deal, which told the caller the deal exists. It now reads
`visible_deals_qs` and answers 404, the same as for no deal at all.

`user_client` is a member (role USER). `mine_*` records were created by that
member; `hidden_*` were created by the admin and are not assigned to the
member, so the visibility querysets exclude them for the member.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import Estimate, Invoice, RecurringInvoice
from opportunity.models import (
    DealPipeline,
    DealStage,
    Opportunity,
    OpportunityLineItem,
)

ESTIMATES = "/api/invoices/estimates/"


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
    ):
        yield


@pytest.fixture
def mine_account(org_a, regular_user):
    return Account.objects.create(name="Mine Co", org=org_a, created_by=regular_user)


@pytest.fixture
def hidden_account(org_a, admin_user):
    return Account.objects.create(name="Hidden Co", org=org_a, created_by=admin_user)


@pytest.fixture
def other_org_account(org_b):
    return Account.objects.create(name="Other Org Co", org=org_b)


@pytest.fixture
def contact(org_a, regular_user):
    return Contact.objects.create(
        first_name="Ada", last_name="Buyer", org=org_a, created_by=regular_user
    )


def _deal(org, creator, name="Deal", stage="PROSPECTING", **fields):
    return Opportunity.objects.create(
        name=name,
        org=org,
        stage=stage,
        amount=Decimal("100"),
        currency="USD",
        created_by=creator,
        **fields,
    )


@pytest.fixture
def mine_deal(org_a, regular_user):
    return _deal(org_a, regular_user, name="Mine deal")


@pytest.fixture
def hidden_deal(org_a, admin_user):
    return _deal(org_a, admin_user, name="Hidden deal")


@pytest.fixture
def other_org_deal(org_b):
    return Opportunity.objects.create(name="Other org deal", org=org_b, stage="")


def _estimate_body(account, contact, **extra):
    today = timezone.localdate()
    body = {
        "title": "Website rebuild",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "EUR",
        "issue_date": str(today),
        "expiry_date": str(today + datetime.timedelta(days=30)),
        "line_items": [
            {"name": "Design", "quantity": "2", "unit_price": "150.00"},
            {"name": "Build", "quantity": "1", "unit_price": "700.00"},
        ],
    }
    body.update(extra)
    return body


def _invoice_body(account, contact, **extra):
    return {
        "invoice_title": "Links",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "USD",
        **extra,
    }


def _recurring_body(account, contact, **extra):
    today = timezone.localdate()
    return {
        "title": "Links",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "frequency": "MONTHLY",
        "start_date": str(today),
        "next_generation_date": str(today),
        "payment_terms": "NET_30",
        "currency": "USD",
        "is_active": True,
        **extra,
    }


KINDS = [
    pytest.param(Invoice, "/api/invoices/", _invoice_body, id="invoice"),
    pytest.param(Estimate, ESTIMATES, _estimate_body, id="estimate"),
    pytest.param(
        RecurringInvoice, "/api/invoices/recurring/", _recurring_body, id="recurring"
    ),
]


def _superuser(user):
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])


def _stored(model, org, account, contact, creator):
    """A document already carrying ``account``, created by ``creator``."""
    today = timezone.localdate()
    extra = {
        Invoice: {"invoice_title": "Stored"},
        Estimate: {"title": "Stored", "issue_date": today},
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
    )


# ---------------------------------------------------------------------------
# Estimate create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateCreate:
    def test_member_creates_a_draft_with_lines_and_server_totals(
        self, user_client, mine_account, contact, mine_deal
    ):
        body = _estimate_body(
            mine_account,
            contact,
            opportunity_id=str(mine_deal.id),
            discount_type="PERCENTAGE",
            discount_value="10",
            tax_rate="20",
            notes="Valid for thirty days",
            terms="Half up front",
            # Server-derived: never taken from the body.
            status="Accepted",
            total_amount="1.00",
        )

        response = user_client.post(ESTIMATES, body, format="json")

        assert response.status_code == 201, response.content
        estimate = Estimate.objects.get()
        assert estimate.status == "Draft"
        assert estimate.estimate_number.startswith("EST-")
        assert estimate.opportunity == mine_deal
        assert estimate.currency == "EUR"
        assert [li.name for li in estimate.line_items.order_by("order")] == [
            "Design",
            "Build",
        ]
        # 2x150 + 700 = 1000; 10% off = 900; 20% tax on that = 180.
        assert estimate.subtotal == Decimal("1000.00")
        assert estimate.discount_amount == Decimal("100.00")
        assert estimate.tax_amount == Decimal("180.00")
        assert estimate.total_amount == Decimal("1080.00")
        payload = response.json()["estimate"]
        assert Decimal(payload["total_amount"]) == Decimal("1080.00")
        # The creator can open what they made.
        assert user_client.get(f"{ESTIMATES}{estimate.id}/").status_code == 200

    @pytest.mark.parametrize("missing", ["title", "account_id", "contact_id"])
    def test_a_required_field_missing_is_a_400(
        self, user_client, mine_account, contact, missing
    ):
        body = _estimate_body(mine_account, contact)
        del body[missing]

        response = user_client.post(ESTIMATES, body, format="json")

        assert response.status_code == 400, response.content
        assert missing in response.json()["errors"]
        assert not Estimate.objects.exists()

    @pytest.mark.parametrize(
        "line, field",
        [
            ({"name": "Zero", "quantity": "0", "unit_price": "10"}, "quantity"),
            ({"name": "Credit", "quantity": "-1", "unit_price": "10"}, "quantity"),
            ({"name": "Rebate", "quantity": "1", "unit_price": "-5"}, "unit_price"),
        ],
    )
    def test_a_line_that_would_reduce_the_bill_is_a_400(
        self, user_client, mine_account, contact, line, field
    ):
        body = _estimate_body(mine_account, contact, line_items=[line])

        response = user_client.post(ESTIMATES, body, format="json")

        assert response.status_code == 400, response.content
        assert field in response.json()["errors"]["line_items"]["0"]
        assert not Estimate.objects.exists()

    def test_an_expiry_before_the_issue_date_is_a_400(
        self, user_client, mine_account, contact
    ):
        today = timezone.localdate()
        body = _estimate_body(
            mine_account,
            contact,
            expiry_date=str(today - datetime.timedelta(days=1)),
        )

        response = user_client.post(ESTIMATES, body, format="json")

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["expiry_date"] == [
            "An estimate cannot expire before it is issued."
        ]
        assert not Estimate.objects.exists()

    def test_an_edit_moving_only_the_expiry_is_checked_against_the_stored_issue(
        self, user_client, mine_account, contact
    ):
        created = user_client.post(
            ESTIMATES, _estimate_body(mine_account, contact), format="json"
        )
        estimate = Estimate.objects.get(id=created.json()["estimate"]["id"])
        before = estimate.issue_date - datetime.timedelta(days=1)

        response = user_client.put(
            f"{ESTIMATES}{estimate.id}/", {"expiry_date": str(before)}, format="json"
        )

        assert response.status_code == 400, response.content
        estimate.refresh_from_db()
        assert estimate.expiry_date != before

    def test_a_free_line_is_allowed(self, user_client, mine_account, contact):
        body = _estimate_body(
            mine_account,
            contact,
            line_items=[{"name": "Setup", "quantity": "1", "unit_price": "0"}],
        )

        response = user_client.post(ESTIMATES, body, format="json")

        assert response.status_code == 201, response.content


@pytest.mark.django_db
def test_the_line_rule_also_holds_on_invoices_and_schedules(
    user_client, mine_account, contact
):
    bad = [{"name": "Credit", "quantity": "-1", "unit_price": "10"}]
    for url, body in (
        ("/api/invoices/", _invoice_body(mine_account, contact, line_items=bad)),
        (
            "/api/invoices/recurring/",
            _recurring_body(mine_account, contact, line_items=bad),
        ),
    ):
        response = user_client.post(url, body, format="json")
        assert response.status_code == 400, response.content
        assert "quantity" in response.json()["errors"]["line_items"]["0"]


# ---------------------------------------------------------------------------
# The account and deal on every document kind
# ---------------------------------------------------------------------------

ACCOUNT_REFUSED = "Account not found, or you do not have access to it."
DEAL_REFUSED = "Opportunity not found, or you do not have access to it."


@pytest.mark.django_db
@pytest.mark.parametrize("model, url, body", KINDS)
class TestDocumentAccount:
    def test_member_may_bill_an_account_they_can_open(
        self, model, url, body, user_client, mine_account, contact
    ):
        response = user_client.post(url, body(mine_account, contact), format="json")

        assert response.status_code == 201, response.content
        assert model.objects.get().account == mine_account

    def test_member_may_bill_an_account_assigned_to_them(
        self, model, url, body, user_client, hidden_account, contact, user_profile
    ):
        hidden_account.assigned_to.add(user_profile)

        response = user_client.post(url, body(hidden_account, contact), format="json")

        assert response.status_code == 201, response.content

    def test_member_cannot_bill_an_account_they_cannot_open(
        self, model, url, body, user_client, hidden_account, contact
    ):
        response = user_client.post(url, body(hidden_account, contact), format="json")

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["account_id"] == [ACCOUNT_REFUSED]
        assert not model.objects.exists()

    def test_hidden_other_org_and_missing_accounts_answer_alike(
        self, model, url, body, user_client, hidden_account, other_org_account, contact
    ):
        missing = Account(id="00000000-0000-0000-0000-000000000001")
        answers = set()
        for account in (hidden_account, other_org_account, missing):
            response = user_client.post(url, body(account, contact), format="json")
            assert response.status_code == 400, response.content
            answers.add(tuple(response.json()["errors"]["account_id"]))
        assert answers == {(ACCOUNT_REFUSED,)}

    def test_admin_may_bill_any_account_in_the_org(
        self, model, url, body, admin_client, hidden_account, contact
    ):
        response = admin_client.post(url, body(hidden_account, contact), format="json")

        assert response.status_code == 201, response.content

    def test_superuser_member_may_bill_any_account_in_the_org(
        self, model, url, body, user_client, regular_user, hidden_account, contact
    ):
        _superuser(regular_user)

        response = user_client.post(url, body(hidden_account, contact), format="json")

        assert response.status_code == 201, response.content

    def test_admin_cannot_bill_another_orgs_account(
        self, model, url, body, admin_client, other_org_account, contact
    ):
        response = admin_client.post(
            url, body(other_org_account, contact), format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["account_id"] == [ACCOUNT_REFUSED]

    def test_an_edit_keeps_a_stored_account_the_member_cannot_open(
        self,
        model,
        url,
        body,
        user_client,
        org_a,
        regular_user,
        hidden_account,
        contact,
    ):
        """An edit form sends back the account it loaded."""
        document = _stored(model, org_a, hidden_account, contact, regular_user)

        response = user_client.put(
            f"{url}{document.id}/", body(hidden_account, contact), format="json"
        )

        assert response.status_code == 200, response.content
        document.refresh_from_db()
        assert document.account == hidden_account

    def test_an_edit_cannot_switch_to_an_account_the_member_cannot_open(
        self,
        model,
        url,
        body,
        user_client,
        mine_account,
        hidden_account,
        contact,
    ):
        created = user_client.post(url, body(mine_account, contact), format="json")
        assert created.status_code == 201, created.content
        document = model.objects.get()

        response = user_client.put(
            f"{url}{document.id}/", body(hidden_account, contact), format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["account_id"] == [ACCOUNT_REFUSED]
        document.refresh_from_db()
        assert document.account == mine_account


@pytest.mark.django_db
@pytest.mark.parametrize("model, url, body", KINDS)
class TestDocumentDeal:
    def test_member_may_cite_a_deal_they_can_open(
        self, model, url, body, user_client, mine_account, contact, mine_deal
    ):
        response = user_client.post(
            url,
            body(mine_account, contact, opportunity_id=str(mine_deal.id)),
            format="json",
        )

        assert response.status_code == 201, response.content
        assert model.objects.get().opportunity == mine_deal

    def test_member_cannot_cite_a_deal_they_cannot_open(
        self, model, url, body, user_client, mine_account, contact, hidden_deal
    ):
        response = user_client.post(
            url,
            body(mine_account, contact, opportunity_id=str(hidden_deal.id)),
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["opportunity_id"] == [DEAL_REFUSED]
        assert not model.objects.exists()

    def test_hidden_other_org_and_missing_deals_answer_alike(
        self,
        model,
        url,
        body,
        user_client,
        mine_account,
        contact,
        hidden_deal,
        other_org_deal,
    ):
        answers = set()
        for deal_id in (
            hidden_deal.id,
            other_org_deal.id,
            "00000000-0000-0000-0000-000000000002",
        ):
            response = user_client.post(
                url,
                body(mine_account, contact, opportunity_id=str(deal_id)),
                format="json",
            )
            assert response.status_code == 400, response.content
            answers.add(tuple(response.json()["errors"]["opportunity_id"]))
        assert answers == {(DEAL_REFUSED,)}

    def test_admin_may_cite_any_deal_in_the_org(
        self, model, url, body, admin_client, mine_account, contact, hidden_deal
    ):
        response = admin_client.post(
            url,
            body(mine_account, contact, opportunity_id=str(hidden_deal.id)),
            format="json",
        )

        assert response.status_code == 201, response.content

    def test_superuser_member_may_cite_any_deal_in_the_org(
        self,
        model,
        url,
        body,
        user_client,
        regular_user,
        mine_account,
        contact,
        hidden_deal,
    ):
        _superuser(regular_user)

        response = user_client.post(
            url,
            body(mine_account, contact, opportunity_id=str(hidden_deal.id)),
            format="json",
        )

        assert response.status_code == 201, response.content

    def test_admin_cannot_cite_another_orgs_deal(
        self, model, url, body, admin_client, mine_account, contact, other_org_deal
    ):
        response = admin_client.post(
            url,
            body(mine_account, contact, opportunity_id=str(other_org_deal.id)),
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["opportunity_id"] == [DEAL_REFUSED]

    def test_no_deal_is_fine(
        self, model, url, body, user_client, mine_account, contact
    ):
        response = user_client.post(
            url, body(mine_account, contact, opportunity_id=None), format="json"
        )

        assert response.status_code == 201, response.content
        assert model.objects.get().opportunity is None


# ---------------------------------------------------------------------------
# Invoice from a won deal
# ---------------------------------------------------------------------------


@pytest.fixture
def renewals(org_a):
    """A pipeline whose won stage is called Signed, not Closed Won."""
    pipeline = DealPipeline.objects.create(org=org_a, name="Renewals")
    for order, (code, label, kind) in enumerate(
        [("TALKING", "Talking", "open"), ("SIGNED", "Signed", "won")], start=1
    ):
        DealStage.objects.create(
            org=org_a, pipeline=pipeline, code=code, label=label, kind=kind, order=order
        )
    return pipeline


def _won_deal(org, creator, pipeline, account, contact, *, stage="SIGNED", lines=1):
    deal = _deal(
        org,
        creator,
        name="Renewal",
        stage=stage,
        pipeline=pipeline,
        account=account,
        closed_on=timezone.localdate(),
    )
    deal.contacts.add(contact)
    for i in range(lines):
        OpportunityLineItem.objects.create(
            opportunity=deal,
            org=org,
            name=f"Seat {i}",
            quantity=Decimal("2"),
            unit_price=Decimal("50"),
        )
    return deal


def _from_deal(client, deal):
    return client.post(f"/api/invoices/from-opportunity/{deal.id}/")


@pytest.mark.django_db
class TestInvoiceFromDeal:
    def test_a_custom_named_won_stage_raises_a_draft_with_the_lines(
        self, user_client, org_a, regular_user, renewals, mine_account, contact
    ):
        deal = _won_deal(org_a, regular_user, renewals, mine_account, contact)
        assert deal.stage == "SIGNED"

        response = _from_deal(user_client, deal)

        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get(opportunity=deal)
        assert invoice.status == "Draft"
        assert invoice.account == mine_account
        assert invoice.contact == contact
        assert [li.name for li in invoice.line_items.all()] == ["Seat 0"]
        assert invoice.total_amount == Decimal("100.00")
        assert response.json()["invoice"]["id"] == str(invoice.id)

    def test_an_open_deal_is_a_400(
        self, user_client, org_a, regular_user, renewals, mine_account, contact
    ):
        deal = _won_deal(
            org_a, regular_user, renewals, mine_account, contact, stage="TALKING"
        )

        response = _from_deal(user_client, deal)

        assert response.status_code == 400, response.content
        assert response.json()["message"] == (
            "Invoice can only be created from won opportunities"
        )
        assert not Invoice.objects.exists()

    def test_a_won_deal_with_no_lines_is_a_400(
        self, user_client, org_a, regular_user, renewals, mine_account, contact
    ):
        deal = _won_deal(org_a, regular_user, renewals, mine_account, contact, lines=0)

        response = _from_deal(user_client, deal)

        assert response.status_code == 400, response.content
        assert response.json()["message"] == (
            "Opportunity has no products/line items to invoice"
        )
        assert not Invoice.objects.exists()

    def test_a_hidden_deal_answers_like_a_missing_one(
        self, user_client, org_a, admin_user, renewals, mine_account, contact
    ):
        deal = _won_deal(org_a, admin_user, renewals, mine_account, contact)

        hidden = _from_deal(user_client, deal)
        missing = user_client.post(
            "/api/invoices/from-opportunity/00000000-0000-0000-0000-000000000003/"
        )

        assert hidden.status_code == 404, hidden.content
        assert hidden.json() == missing.json()
        assert not Invoice.objects.exists()

    def test_a_member_assigned_to_the_deal_may_invoice_it(
        self,
        user_client,
        org_a,
        admin_user,
        user_profile,
        renewals,
        mine_account,
        contact,
    ):
        deal = _won_deal(org_a, admin_user, renewals, mine_account, contact)
        deal.assigned_to.add(user_profile)

        response = _from_deal(user_client, deal)

        assert response.status_code == 201, response.content

    def test_admin_may_invoice_any_won_deal(
        self, admin_client, org_a, regular_user, renewals, mine_account, contact
    ):
        deal = _won_deal(org_a, regular_user, renewals, mine_account, contact)

        response = _from_deal(admin_client, deal)

        assert response.status_code == 201, response.content

    def test_superuser_member_may_invoice_a_deal_they_are_not_on(
        self,
        user_client,
        regular_user,
        org_a,
        admin_user,
        renewals,
        mine_account,
        contact,
    ):
        _superuser(regular_user)
        deal = _won_deal(org_a, admin_user, renewals, mine_account, contact)

        response = _from_deal(user_client, deal)

        assert response.status_code == 201, response.content

    def test_another_orgs_deal_is_a_404(
        self, org_b_client, org_a, regular_user, renewals, mine_account, contact
    ):
        deal = _won_deal(org_a, regular_user, renewals, mine_account, contact)

        response = _from_deal(org_b_client, deal)

        assert response.status_code == 404, response.content
