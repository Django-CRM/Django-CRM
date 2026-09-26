"""A line's own discount counts toward its document's total.

`Invoice.recalculate_totals` and `Estimate.recalculate_totals` summed each
line's gross (quantity x unit price), so a line discount was stored, shown on
the line and then billed at full price. An invoice raised from a deal with
discounted lines billed more than the deal was won for. The one definition is
now `LineAmounts.net_amount` (gross less the line's own discount); every
document subtotal, the PDF and the portal read it.

Worked numbers used throughout:

    Design  2 x 100.00, 10% off   gross 200.00, discount 20.00, net 180.00
    Hosting 1 x  50.00, 5.00 off  gross  50.00, discount  5.00, net  45.00
    subtotal                                                       225.00
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import Estimate, Invoice, InvoiceLineItem, RecurringInvoice
from invoices.tasks import generate_recurring_invoices
from opportunity.models import (
    DealPipeline,
    DealStage,
    Opportunity,
    OpportunityLineItem,
)

LINES = [
    {
        "name": "Design",
        "quantity": "2",
        "unit_price": "100.00",
        "discount_type": "PERCENTAGE",
        "discount_value": "10",
    },
    {
        "name": "Hosting",
        "quantity": "1",
        "unit_price": "50.00",
        "discount_type": "FIXED",
        "discount_value": "5",
    },
]


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
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


def _body(account, contact, **extra):
    return {
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "currency": "USD",
        "issue_date": str(timezone.localdate()),
        "line_items": LINES,
        **extra,
    }


def _nets(document):
    return [item.net_amount for item in document.line_items.order_by("order")]


@pytest.mark.django_db
class TestInvoiceTotals:
    def test_line_discounts_then_document_discount_tax_and_shipping(
        self, admin_client, account, contact
    ):
        response = admin_client.post(
            "/api/invoices/",
            _body(
                account,
                contact,
                invoice_title="Rebuild",
                discount_type="PERCENTAGE",
                discount_value="10",
                tax_rate="20",
                shipping_amount="10",
            ),
            format="json",
        )

        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get()
        assert _nets(invoice) == [Decimal("180.00"), Decimal("45.00")]
        assert invoice.subtotal == Decimal("225.00")
        # 10% of 225 = 22.50; 20% tax on 202.50 = 40.50; shipping 10 untaxed.
        assert invoice.discount_amount == Decimal("22.50")
        assert invoice.tax_amount == Decimal("40.50")
        assert invoice.total_amount == Decimal("253.00")
        assert invoice.amount_due == Decimal("253.00")

        lines = admin_client.get(f"/api/invoices/{invoice.id}/").json()["invoice"][
            "line_items"
        ]
        assert [li["net_amount"] for li in lines] == ["180.00", "45.00"]

    def test_a_line_tax_rate_is_not_added_to_the_document(self, org_a, account):
        """The document's tax_rate is what is charged; a line's own rate is
        informational. Seeded data sets both to the same rate, so adding the
        line's would tax those lines twice."""
        invoice = Invoice.objects.create(
            invoice_title="Taxed line", account=account, org=org_a
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            org=org_a,
            name="Seat",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            tax_rate=Decimal("10"),
        )
        invoice.save()

        assert invoice.total_amount == Decimal("100.00")

    def test_net_amount_is_what_the_portal_and_pdf_lines_show(
        self, admin_client, account, contact, client
    ):
        admin_client.post(
            "/api/invoices/",
            _body(account, contact, invoice_title="Rebuild"),
            format="json",
        )
        invoice = Invoice.objects.get()

        public = client.get(f"/api/public/invoice/{invoice.public_token}/").json()

        assert [li["amount"] for li in public["line_items"]] == ["180.00", "45.00"]
        assert public["subtotal"] == "225.00"


@pytest.mark.django_db
class TestEstimateTotalsAndConversion:
    def _estimate(self, admin_client, account, contact):
        response = admin_client.post(
            "/api/invoices/estimates/",
            _body(
                account,
                contact,
                title="Rebuild",
                expiry_date=str(timezone.localdate() + datetime.timedelta(days=30)),
                discount_type="FIXED",
                discount_value="25",
                tax_rate="10",
            ),
            format="json",
        )
        assert response.status_code == 201, response.content
        return Estimate.objects.get()

    def test_line_discounts_then_document_discount_and_tax(
        self, admin_client, account, contact
    ):
        estimate = self._estimate(admin_client, account, contact)

        assert _nets(estimate) == [Decimal("180.00"), Decimal("45.00")]
        assert estimate.subtotal == Decimal("225.00")
        # 225 - 25 = 200, 10% tax = 20.
        assert estimate.tax_amount == Decimal("20.00")
        assert estimate.total_amount == Decimal("220.00")

    def test_conversion_keeps_the_estimate_total(self, admin_client, account, contact):
        estimate = self._estimate(admin_client, account, contact)

        response = admin_client.post(f"/api/invoices/estimates/{estimate.id}/convert/")

        assert response.status_code in (200, 201), response.content
        invoice = Invoice.objects.get()
        assert _nets(invoice) == [Decimal("180.00"), Decimal("45.00")]
        assert invoice.subtotal == estimate.subtotal == Decimal("225.00")
        assert invoice.total_amount == estimate.total_amount == Decimal("220.00")


@pytest.mark.django_db
class TestRecurringTotals:
    def test_schedule_and_generated_invoice_both_net_the_lines(
        self, admin_client, org_a, account, contact
    ):
        today = timezone.localdate()
        response = admin_client.post(
            "/api/invoices/recurring/",
            _body(
                account,
                contact,
                title="Monthly",
                frequency="MONTHLY",
                start_date=str(today),
                next_generation_date=str(today),
            ),
            format="json",
        )
        assert response.status_code == 201, response.content
        schedule = RecurringInvoice.objects.get()
        assert _nets(schedule) == [Decimal("180.00"), Decimal("45.00")]
        assert schedule.total_amount == Decimal("225.00")

        RecurringInvoice.objects.filter(id=schedule.id).update(
            next_generation_date=today
        )
        generate_recurring_invoices()

        invoice = Invoice.objects.get(org=org_a)
        assert invoice.total_amount == Decimal("225.00")


@pytest.fixture
def renewals(org_a):
    pipeline = DealPipeline.objects.create(org=org_a, name="Renewals")
    for order, (code, label, kind) in enumerate(
        [("TALKING", "Talking", "open"), ("SIGNED", "Signed", "won")], start=1
    ):
        DealStage.objects.create(
            org=org_a, pipeline=pipeline, code=code, label=label, kind=kind, order=order
        )
    return pipeline


@pytest.mark.django_db
def test_invoice_from_a_deal_totals_what_the_deal_was_won_for(
    admin_client, admin_user, org_a, renewals, account, contact
):
    deal = Opportunity.objects.create(
        name="Renewal",
        org=org_a,
        stage="SIGNED",
        pipeline=renewals,
        account=account,
        currency="USD",
        closed_on=timezone.localdate(),
        created_by=admin_user,
    )
    deal.contacts.add(contact)
    # 2 x 50 less 10% = 90, and 1 x 100 less 20 = 80.
    OpportunityLineItem.objects.create(
        opportunity=deal,
        org=org_a,
        name="Seats",
        quantity=Decimal("2"),
        unit_price=Decimal("50"),
        discount_type="PERCENTAGE",
        discount_value=Decimal("10"),
        order=0,
    )
    OpportunityLineItem.objects.create(
        opportunity=deal,
        org=org_a,
        name="Onboarding",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        discount_type="FIXED",
        discount_value=Decimal("20"),
        order=1,
    )
    deal.refresh_from_db()
    assert deal.amount == Decimal("170.00")

    response = admin_client.post(f"/api/invoices/from-opportunity/{deal.id}/")

    assert response.status_code == 201, response.content
    invoice = Invoice.objects.get(opportunity=deal)
    assert _nets(invoice) == [Decimal("90.00"), Decimal("80.00")]
    assert invoice.subtotal == invoice.total_amount == deal.amount


@pytest.mark.django_db
class TestLineDiscountValidation:
    """A discount that counts has to be a discount: not negative, not over
    100%, not more than the line's own amount."""

    @pytest.mark.parametrize(
        "line, message",
        [
            (
                {"discount_type": "FIXED", "discount_value": "-5"},
                "A discount cannot be negative.",
            ),
            (
                {"discount_type": "PERCENTAGE", "discount_value": "101"},
                "A percentage discount cannot exceed 100.",
            ),
            (
                {"discount_type": "FIXED", "discount_value": "50.01"},
                "A discount cannot exceed the line's amount.",
            ),
        ],
    )
    def test_nested_line_with_a_bad_discount_is_a_400(
        self, admin_client, account, contact, line, message
    ):
        body = _body(account, contact, invoice_title="Bad")
        body["line_items"] = [
            {"name": "Seat", "quantity": "1", "unit_price": "50.00", **line}
        ]

        response = admin_client.post("/api/invoices/", body, format="json")

        assert response.status_code == 400
        assert message in str(response.content.decode())
        assert not Invoice.objects.exists()

    def test_the_whole_line_off_and_100_percent_are_allowed(
        self, admin_client, account, contact
    ):
        body = _body(account, contact, invoice_title="Free")
        body["line_items"] = [
            {
                "name": "Seat",
                "quantity": "1",
                "unit_price": "50.00",
                "discount_type": "FIXED",
                "discount_value": "50",
            },
            {
                "name": "Gift",
                "quantity": "3",
                "unit_price": "10.00",
                "discount_type": "PERCENTAGE",
                "discount_value": "100",
            },
        ]

        response = admin_client.post("/api/invoices/", body, format="json")

        assert response.status_code == 201, response.content
        assert Invoice.objects.get().total_amount == Decimal("0.00")

    def test_partial_line_update_checks_against_the_stored_amount(
        self, admin_client, account, contact
    ):
        admin_client.post(
            "/api/invoices/",
            _body(account, contact, invoice_title="Rebuild"),
            format="json",
        )
        invoice = Invoice.objects.get()
        hosting = invoice.line_items.get(name="Hosting")
        url = f"/api/invoices/{invoice.id}/line-items/{hosting.id}/"

        refused = admin_client.put(url, {"discount_value": "60"}, format="json")
        assert refused.status_code == 400
        assert "cannot exceed the line's amount" in refused.content.decode()

        allowed = admin_client.put(url, {"discount_value": "20"}, format="json")
        assert allowed.status_code == 200, allowed.content
        invoice.refresh_from_db()
        # 180 + (50 - 20)
        assert invoice.subtotal == Decimal("210.00")
