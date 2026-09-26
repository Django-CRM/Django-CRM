"""An issued document keeps the totals it was issued with.

`Invoice.save()` and `Estimate.save()` recomputed the totals on every save.
When line discounts started counting (``LineAmounts.net_amount``), every path
that full-saves an issued document without anyone editing it re-priced it: a
Paid invoice stored at 200.00 (2 x 100.00, 10% off the line) became 180.00
with amount_due -20.00 and stayed Paid. The payment path saved with
``update_fields`` but still recomputed in memory, so it wrote an amount_due
from the new total beside the old stored total_amount.

Now ``save()`` recomputes only a new document or one still a Draft in the
database (`totals_follow_lines`), and an explicit edit through the API
recomputes because it asks to.

Every invoice here is stored the way the old code left it: total 200.00 from
the gross, with a line whose own discount makes the net 180.00.

Also here: the document discount rules (not negative, not over 100%, a flat
one not over the subtotal) and the account check on invoice-from-deal.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import (
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceLineItem,
    RecurringInvoice,
)
from invoices.tasks import (
    check_expired_estimates,
    check_overdue_invoices,
    send_estimate_to_client,
    send_invoice_to_client,
    send_payment_reminder,
)
from opportunity.models import Opportunity, OpportunityLineItem

STORED = Decimal("200.00")
NET = Decimal("180.00")
DISCOUNTED_LINE = {
    "name": "Design",
    "quantity": Decimal("2"),
    "unit_price": Decimal("100"),
    "discount_type": "PERCENTAGE",
    "discount_value": Decimal("10"),
}


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
        patch("invoices.api_views.send_invoice_to_client"),
        patch("invoices.tasks.send_estimate_to_client.delay"),
    ):
        yield


@pytest.fixture
def account(org_a, admin_user):
    return Account.objects.create(name="Buyer Co", org=org_a, created_by=admin_user)


@pytest.fixture
def contact(org_a, admin_user):
    return Contact.objects.create(
        first_name="Ada", last_name="Buyer", org=org_a, created_by=admin_user
    )


def _issued_invoice(org, account, status, amount_paid=Decimal("0"), **fields):
    invoice = Invoice.objects.create(
        invoice_title="Issued",
        account=account,
        client_email="buyer@example.com",
        org=org,
        **fields,
    )
    InvoiceLineItem.objects.create(invoice=invoice, org=org, **DISCOUNTED_LINE)
    Invoice.objects.filter(id=invoice.id).update(
        status=status,
        subtotal=STORED,
        total_amount=STORED,
        amount_paid=amount_paid,
        amount_due=STORED - amount_paid,
    )
    invoice.refresh_from_db()
    return invoice


def _issued_estimate(org, account, status, **fields):
    estimate = Estimate.objects.create(
        title="Issued",
        account=account,
        client_email="buyer@example.com",
        org=org,
        **fields,
    )
    EstimateLineItem.objects.create(estimate=estimate, org=org, **DISCOUNTED_LINE)
    Estimate.objects.filter(id=estimate.id).update(
        status=status, subtotal=STORED, total_amount=STORED
    )
    estimate.refresh_from_db()
    return estimate


def _totals(document):
    document.refresh_from_db()
    fields = [document.subtotal, document.total_amount]
    if isinstance(document, Invoice):
        fields.append(document.amount_due)
    return fields


@pytest.mark.django_db
class TestIssuedInvoiceTotalsAreLeftAlone:
    def test_portal_first_view(self, client, org_a, account):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = client.get(f"/api/public/invoice/{invoice.public_token}/")

        assert response.status_code == 200
        invoice.refresh_from_db()
        assert invoice.status == "Viewed"
        assert _totals(invoice) == [STORED, STORED, STORED]

    def test_send_view_resend(self, admin_client, org_a, account):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = admin_client.post(f"/api/invoices/{invoice.id}/send/")

        assert response.status_code == 200, response.content
        assert _totals(invoice) == [STORED, STORED, STORED]

    def test_send_task(self, org_a, account):
        invoice = _issued_invoice(org_a, account, "Viewed")

        send_invoice_to_client(str(invoice.id), str(org_a.id), include_pdf=False)

        invoice.refresh_from_db()
        assert invoice.is_email_sent
        assert _totals(invoice) == [STORED, STORED, STORED]

    def test_overdue_sweep(self, org_a, account):
        invoice = _issued_invoice(
            org_a,
            account,
            "Sent",
            due_date=timezone.localdate() - datetime.timedelta(days=3),
        )

        check_overdue_invoices()

        invoice.refresh_from_db()
        assert invoice.status == "Overdue"
        assert _totals(invoice) == [STORED, STORED, STORED]

    def test_payment_reminder(self, org_a, account):
        invoice = _issued_invoice(org_a, account, "Overdue")

        send_payment_reminder(str(invoice.id), str(org_a.id))

        invoice.refresh_from_db()
        assert invoice.reminder_count == 1
        assert _totals(invoice) == [STORED, STORED, STORED]

    def test_paid_invoice_full_save(self, org_a, account):
        invoice = _issued_invoice(org_a, account, "Paid", amount_paid=STORED)

        invoice.notes = "Thanks"
        invoice.save()

        invoice.refresh_from_db()
        assert (invoice.total_amount, invoice.amount_due) == (STORED, Decimal("0"))

    def test_edit_that_leaves_the_totals_inputs_alone(
        self, admin_client, org_a, account
    ):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = admin_client.put(
            f"/api/invoices/{invoice.id}/", {"notes": "PO attached"}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _totals(invoice) == [STORED, STORED, STORED]


@pytest.mark.django_db
class TestPaymentOnAnIssuedInvoice:
    def test_part_payment_is_taken_off_the_stored_total(
        self, admin_client, org_a, account
    ):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = admin_client.post(
            f"/api/invoices/{invoice.id}/mark-paid/", {"amount": "0.01"}, format="json"
        )

        assert response.status_code == 200, response.content
        invoice.refresh_from_db()
        assert invoice.total_amount == STORED
        assert invoice.amount_paid == Decimal("0.01")
        assert invoice.amount_due == Decimal("199.99")
        assert invoice.status == "Partially_Paid"

    def test_paying_the_stored_total_settles_it(self, admin_client, org_a, account):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = admin_client.post(f"/api/invoices/{invoice.id}/mark-paid/")

        assert response.status_code == 200, response.content
        invoice.refresh_from_db()
        assert (invoice.total_amount, invoice.amount_paid, invoice.amount_due) == (
            STORED,
            STORED,
            Decimal("0"),
        )
        assert invoice.status == "Paid"


@pytest.mark.django_db
class TestWhatStillRecomputes:
    def test_a_draft_recomputes_on_save(self, org_a, account):
        invoice = _issued_invoice(org_a, account, "Draft")

        invoice.save()

        assert _totals(invoice) == [NET, NET, NET]

    def test_sending_a_draft_prices_it_as_issued(self, admin_client, org_a, account):
        invoice = _issued_invoice(org_a, account, "Draft")

        admin_client.post(f"/api/invoices/{invoice.id}/send/")

        invoice.refresh_from_db()
        assert invoice.status == "Sent"
        assert _totals(invoice) == [NET, NET, NET]

    def test_a_line_edit_on_an_issued_invoice_recomputes(
        self, admin_client, org_a, account
    ):
        """Editing a non-Draft invoice's lines was allowed before and still
        is; the edit asks for the new totals."""
        invoice = _issued_invoice(org_a, account, "Sent")
        line = invoice.line_items.get()

        response = admin_client.put(
            f"/api/invoices/{invoice.id}/line-items/{line.id}/",
            {"quantity": "3"},
            format="json",
        )

        assert response.status_code == 200, response.content
        # 3 x 100 less 10%.
        assert _totals(invoice) == [Decimal("270.00")] * 3

    def test_a_totals_edit_on_an_issued_invoice_recomputes(
        self, admin_client, org_a, account
    ):
        invoice = _issued_invoice(org_a, account, "Sent")

        response = admin_client.put(
            f"/api/invoices/{invoice.id}/", {"shipping_amount": "5"}, format="json"
        )

        assert response.status_code == 200, response.content
        assert _totals(invoice) == [NET, NET + 5, NET + 5]


@pytest.mark.django_db
class TestIssuedEstimateTotalsAreLeftAlone:
    def test_portal_first_view(self, client, org_a, account):
        estimate = _issued_estimate(org_a, account, "Sent")

        response = client.get(f"/api/public/estimate/{estimate.public_token}/")

        assert response.status_code == 200
        estimate.refresh_from_db()
        assert estimate.status == "Viewed"
        assert _totals(estimate) == [STORED, STORED]

    def test_send_view_resend(self, admin_client, org_a, account):
        estimate = _issued_estimate(org_a, account, "Sent")

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/send/")

        assert response.status_code == 200, response.content
        assert _totals(estimate) == [STORED, STORED]

    def test_send_task(self, org_a, account):
        estimate = _issued_estimate(org_a, account, "Viewed")

        send_estimate_to_client(str(estimate.id), str(org_a.id), include_pdf=False)

        estimate.refresh_from_db()
        assert estimate.sent_at is not None
        assert _totals(estimate) == [STORED, STORED]

    def test_expiry_sweep(self, org_a, account):
        estimate = _issued_estimate(
            org_a,
            account,
            "Sent",
            expiry_date=timezone.localdate() - datetime.timedelta(days=1),
        )

        check_expired_estimates()

        estimate.refresh_from_db()
        assert estimate.status == "Expired"
        assert _totals(estimate) == [STORED, STORED]

    def test_a_draft_recomputes_on_save(self, org_a, account):
        estimate = _issued_estimate(org_a, account, "Draft")

        estimate.save()

        assert _totals(estimate) == [NET, NET]


def _lines(*nets):
    return [
        {"name": f"Line {i}", "quantity": "1", "unit_price": str(net)}
        for i, net in enumerate(nets)
    ]


def _invoice_body(account, contact, **extra):
    return {
        "invoice_title": "Discounted",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "line_items": _lines("60.00", "40.00"),
        **extra,
    }


def _estimate_body(account, contact, **extra):
    return {
        "title": "Discounted",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "line_items": _lines("60.00", "40.00"),
        **extra,
    }


def _recurring_body(account, contact, **extra):
    today = str(timezone.localdate())
    return {
        "title": "Discounted",
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "frequency": "MONTHLY",
        "start_date": today,
        "next_generation_date": today,
        "line_items": _lines("60.00", "40.00"),
        **extra,
    }


DOCUMENTS = [
    ("/api/invoices/", _invoice_body, Invoice),
    ("/api/invoices/estimates/", _estimate_body, Estimate),
    ("/api/invoices/recurring/", _recurring_body, RecurringInvoice),
]

BAD_DISCOUNTS = [
    ({"discount_type": "FIXED", "discount_value": "-1"}, "cannot be negative"),
    ({"discount_type": "PERCENTAGE", "discount_value": "100.01"}, "exceed 100"),
    ({"discount_type": "FIXED", "discount_value": "100.01"}, "exceed the subtotal"),
    ({"discount_type": "", "discount_value": "100.01"}, "exceed the subtotal"),
]


@pytest.mark.django_db
class TestDocumentDiscountValidation:
    @pytest.mark.parametrize("url, body, model", DOCUMENTS)
    @pytest.mark.parametrize("discount, message", BAD_DISCOUNTS)
    def test_bad_discount_on_create_is_a_400(
        self, admin_client, account, contact, url, body, model, discount, message
    ):
        response = admin_client.post(
            url, body(account, contact, **discount), format="json"
        )

        assert response.status_code == 400, response.content
        assert message in str(response.json()["errors"]["discount_value"])
        assert not model.objects.exists()

    @pytest.mark.parametrize("url, body, model", DOCUMENTS)
    @pytest.mark.parametrize(
        "discount",
        [
            {"discount_type": "FIXED", "discount_value": "100"},
            {"discount_type": "PERCENTAGE", "discount_value": "100"},
        ],
    )
    def test_the_whole_subtotal_off_is_allowed(
        self, admin_client, account, contact, url, body, model, discount
    ):
        response = admin_client.post(
            url, body(account, contact, **discount), format="json"
        )

        assert response.status_code == 201, response.content
        assert model.objects.get().total_amount == Decimal("0")

    @pytest.mark.parametrize("url, body, model", DOCUMENTS)
    def test_partial_update_checks_against_the_stored_lines(
        self, admin_client, account, contact, url, body, model
    ):
        admin_client.post(url, body(account, contact), format="json")
        document = model.objects.get()
        detail = f"{url}{document.id}/"

        refused = admin_client.put(
            detail,
            {"discount_type": "FIXED", "discount_value": "100.01"},
            format="json",
        )
        assert refused.status_code == 400, refused.content
        assert "exceed the subtotal" in str(refused.json()["errors"])

        allowed = admin_client.put(
            detail, {"discount_type": "FIXED", "discount_value": "30"}, format="json"
        )
        assert allowed.status_code == 200, allowed.content
        assert model.objects.get().total_amount == Decimal("70.00")

    @pytest.mark.parametrize("url, body, model", DOCUMENTS)
    def test_partial_update_checks_new_lines_against_the_stored_discount(
        self, admin_client, account, contact, url, body, model
    ):
        admin_client.post(
            url,
            body(account, contact, discount_type="FIXED", discount_value="80"),
            format="json",
        )
        document = model.objects.get()

        refused = admin_client.put(
            f"{url}{document.id}/", {"line_items": _lines("50.00")}, format="json"
        )

        assert refused.status_code == 400, refused.content
        assert "exceed the subtotal" in str(refused.json()["errors"])

    def test_removing_a_line_never_takes_the_total_below_zero(
        self, admin_client, account, contact
    ):
        admin_client.post(
            "/api/invoices/",
            _invoice_body(account, contact, discount_type="FIXED", discount_value="80"),
            format="json",
        )
        invoice = Invoice.objects.get()
        big = invoice.line_items.get(unit_price=Decimal("60"))

        response = admin_client.delete(
            f"/api/invoices/{invoice.id}/line-items/{big.id}/"
        )

        assert response.status_code == 200, response.content
        invoice.refresh_from_db()
        assert (invoice.discount_amount, invoice.total_amount) == (
            Decimal("40.00"),
            Decimal("0.00"),
        )


def _won_deal(org, account, creator):
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
    OpportunityLineItem.objects.create(
        opportunity=deal,
        org=org,
        name="Widget",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
    )
    return deal


@pytest.mark.django_db
class TestInvoiceFromDealNeedsTheAccount:
    """Seeing the deal is not access to its account; the create serializers
    already refuse an account the caller cannot open."""

    @pytest.fixture
    def mine(self, org_a, regular_user):
        return Contact.objects.create(
            first_name="Mine", last_name="Visible", org=org_a, created_by=regular_user
        )

    def _url(self, deal):
        return f"/api/invoices/from-opportunity/{deal.id}/"

    def test_member_who_cannot_open_the_account_is_refused(
        self, user_client, org_a, account, mine, regular_user
    ):
        deal = _won_deal(org_a, account, regular_user)
        deal.contacts.add(mine)

        response = user_client.post(self._url(deal))

        assert response.status_code == 400, response.content
        assert (
            response.json()["message"]
            == "Account not found, or you do not have access to it."
        )
        assert not Invoice.objects.exists()

    def test_member_who_can_open_the_account_gets_the_invoice(
        self, user_client, org_a, mine, regular_user
    ):
        own = Account.objects.create(name="Own Co", org=org_a, created_by=regular_user)
        deal = _won_deal(org_a, own, regular_user)
        deal.contacts.add(mine)

        response = user_client.post(self._url(deal))

        assert response.status_code == 201, response.content
        assert Invoice.objects.get(opportunity=deal).account == own

    def test_admin_may_bill_any_account(
        self, admin_client, org_a, account, mine, regular_user
    ):
        deal = _won_deal(org_a, account, regular_user)
        deal.contacts.add(mine)

        response = admin_client.post(self._url(deal))

        assert response.status_code == 201, response.content
