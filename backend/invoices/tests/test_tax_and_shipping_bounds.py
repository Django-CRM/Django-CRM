"""Tax rates are 0 to 100 and shipping is never negative, on every document
and every line (1.11.0), through the API and the Django admin alike.

Before this the API took a negative tax rate or shipping amount, either of
which lowers the bill, and a tax rate up to the column's 999.99.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from accounts.models import Account
from contacts.models import Contact
from invoices.models import (
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceLineItem,
    RecurringInvoice,
    RecurringInvoiceLineItem,
)


@pytest.fixture(autouse=True)
def _no_background_work():
    with (
        patch("invoices.api_views.create_invoice_history"),
        patch("invoices.api_views.send_email"),
    ):
        yield


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Buyer Co", org=org_a)


@pytest.fixture
def contact(org_a):
    return Contact.objects.create(first_name="Ada", last_name="Buyer", org=org_a)


LINE = {"name": "Work", "quantity": "1", "unit_price": "100"}


def _body(kind, account, contact, **extra):
    body = {
        "account_id": str(account.id),
        "contact_id": str(contact.id),
        "line_items": [dict(LINE)],
    }
    if kind == "invoice":
        body["invoice_title"] = "Bill"
    else:
        body["title"] = "Bill"
    if kind == "recurring":
        today = str(timezone.localdate())
        body.update(frequency="MONTHLY", start_date=today, next_generation_date=today)
    body.update(extra)
    return body


DOCUMENTS = [
    ("invoice", "/api/invoices/", Invoice),
    ("estimate", "/api/invoices/estimates/", Estimate),
    ("recurring", "/api/invoices/recurring/", RecurringInvoice),
]

REFUSED = [
    ({"tax_rate": "-0.01"}, "tax_rate", "Tax rate cannot be negative."),
    ({"tax_rate": "100.01"}, "tax_rate", "Tax rate cannot exceed 100."),
]


@pytest.mark.django_db
class TestDocumentTaxRate:
    @pytest.mark.parametrize("kind, url, model", DOCUMENTS)
    @pytest.mark.parametrize("extra, field, message", REFUSED)
    def test_out_of_range_is_a_400(
        self, admin_client, account, contact, kind, url, model, extra, field, message
    ):
        response = admin_client.post(
            url, _body(kind, account, contact, **extra), format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"][field] == [message]
        assert not model.objects.exists()

    @pytest.mark.parametrize("kind, url, model", DOCUMENTS)
    @pytest.mark.parametrize("rate", ["0", "100"])
    def test_the_ends_of_the_range_are_allowed(
        self, admin_client, account, contact, kind, url, model, rate
    ):
        response = admin_client.post(
            url, _body(kind, account, contact, tax_rate=rate), format="json"
        )

        assert response.status_code == 201, response.content

    @pytest.mark.parametrize("kind, url, model", DOCUMENTS)
    def test_an_update_is_held_to_it_too(
        self, admin_client, account, contact, kind, url, model
    ):
        admin_client.post(url, _body(kind, account, contact), format="json")
        detail = f"{url}{model.objects.get().id}/"

        refused = admin_client.put(detail, {"tax_rate": "-5"}, format="json")
        allowed = admin_client.put(detail, {"tax_rate": "5"}, format="json")

        assert refused.status_code == 400, refused.content
        assert allowed.status_code == 200, allowed.content


@pytest.mark.django_db
class TestShipping:
    def test_negative_shipping_is_a_400(self, admin_client, account, contact):
        response = admin_client.post(
            "/api/invoices/",
            _body("invoice", account, contact, shipping_amount="-1"),
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["shipping_amount"] == [
            "Shipping cannot be negative."
        ]
        assert not Invoice.objects.exists()

    def test_zero_and_positive_shipping_are_allowed(
        self, admin_client, account, contact
    ):
        for amount in ("0", "12.50"):
            response = admin_client.post(
                "/api/invoices/",
                _body("invoice", account, contact, shipping_amount=amount),
                format="json",
            )
            assert response.status_code == 201, response.content


@pytest.mark.django_db
class TestLineTaxRate:
    @pytest.mark.parametrize("kind, url, model", DOCUMENTS)
    @pytest.mark.parametrize("rate, ok", [("-1", False), ("101", False), ("20", True)])
    def test_nested_line_tax_rate(
        self, admin_client, account, contact, kind, url, model, rate, ok
    ):
        body = _body(kind, account, contact)
        body["line_items"][0]["tax_rate"] = rate

        response = admin_client.post(url, body, format="json")

        assert response.status_code == (201 if ok else 400), response.content

    def test_line_item_endpoint_tax_rate(self, admin_client, account, contact):
        admin_client.post(
            "/api/invoices/", _body("invoice", account, contact), format="json"
        )
        invoice = Invoice.objects.get()
        line = invoice.line_items.get()
        url = f"/api/invoices/{invoice.id}/line-items/{line.id}/"

        refused = admin_client.put(url, {"tax_rate": "-1"}, format="json")
        assert refused.status_code == 400, refused.content
        assert refused.json()["errors"]["tax_rate"] == ["Tax rate cannot be negative."]

        added = admin_client.post(
            f"/api/invoices/{invoice.id}/line-items/",
            {**LINE, "tax_rate": "100.5"},
            format="json",
        )
        assert added.status_code == 400, added.content
        assert InvoiceLineItem.objects.filter(invoice=invoice).count() == 1

        allowed = admin_client.put(url, {"tax_rate": "18"}, format="json")
        assert allowed.status_code == 200, allowed.content


MODELS_WITH_TAX = [
    Invoice,
    Estimate,
    RecurringInvoice,
    InvoiceLineItem,
    EstimateLineItem,
    RecurringInvoiceLineItem,
]


@pytest.mark.parametrize("model", MODELS_WITH_TAX)
@pytest.mark.parametrize(
    "rate, message",
    [
        ("-0.01", "Tax rate cannot be negative."),
        ("100.01", "Tax rate cannot exceed 100."),
        ("100", None),
        ("0", None),
    ],
)
def test_the_model_field_holds_the_bound(model, rate, message):
    """The model field's own validator, which the Django admin's forms run."""
    field = model._meta.get_field("tax_rate")
    if message is None:
        field.run_validators(Decimal(rate))
        return
    with pytest.raises(DjangoValidationError) as refused:
        field.run_validators(Decimal(rate))
    assert refused.value.messages == [message]


def test_the_model_field_refuses_negative_shipping():
    field = Invoice._meta.get_field("shipping_amount")
    with pytest.raises(DjangoValidationError) as refused:
        field.run_validators(Decimal("-1"))
    assert refused.value.messages == ["Shipping cannot be negative."]
    field.run_validators(Decimal("0"))


@pytest.mark.django_db
def test_the_admin_form_refuses_it(org_a, account, admin_user):
    from django.contrib.admin.sites import site
    from django.test import RequestFactory

    invoice = Invoice.objects.create(org=org_a, account=account, invoice_title="W")
    request = RequestFactory().get("/admin/")
    request.user = admin_user
    Form = site._registry[Invoice].get_form(request, invoice)
    data = {
        name: getattr(invoice, name)
        for name in Form.base_fields
        if getattr(invoice, name, None) is not None
        and name not in ("assigned_to", "teams")
    }
    data.update(account=account.pk, org=org_a.pk, tax_rate="150", shipping_amount="-2")

    form = Form(data=data, instance=invoice)

    assert not form.is_valid()
    assert form.errors["tax_rate"] == ["Tax rate cannot exceed 100."]
    assert form.errors["shipping_amount"] == ["Shipping cannot be negative."]
