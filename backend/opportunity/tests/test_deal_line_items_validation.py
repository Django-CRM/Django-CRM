"""Deal line items: the product comes from the deal's org, and the numbers
follow the invoice line rules.

`OpportunityLineItemCreateSerializer` looked the product up with
``Product.objects.get(id=...)``, no org filter, and swallowed a miss. With RLS
off (tests, a superuser DB role) another org's product attached and its name
and price landed on the deal. Now the lookup is scoped to the deal's org and a
miss is a 400 whose message does not say whether the id exists elsewhere.

The line itself was unchecked: a flat discount above the line's amount made a
negative line, and an invoice raised from the deal copies it.
"""

from decimal import Decimal

import pytest

from invoices.models import Product
from opportunity.models import Opportunity, OpportunityLineItem

MISSING = "Product not found or does not belong to your organization"


@pytest.fixture
def deal(org_a, admin_user):
    return Opportunity.objects.create(
        name="Deal", org=org_a, currency="USD", created_by=admin_user
    )


@pytest.fixture
def own_product(org_a):
    return Product.objects.create(name="Seat", price=Decimal("40"), org=org_a)


@pytest.fixture
def other_product(org_b):
    return Product.objects.create(name="Theirs", price=Decimal("999"), org=org_b)


def _list_url(deal):
    return f"/api/opportunities/{deal.id}/line-items/"


def _detail_url(deal, line):
    return f"/api/opportunities/{deal.id}/line-items/{line.id}/"


@pytest.mark.django_db
class TestProductIsFromTheDealsOrg:
    def test_another_orgs_product_is_a_400_on_create(
        self, admin_client, deal, other_product
    ):
        response = admin_client.post(
            _list_url(deal),
            {"product_id": str(other_product.id), "quantity": "1"},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["product_id"] == [MISSING]
        assert not OpportunityLineItem.objects.exists()

    def test_a_missing_product_gets_the_same_answer(self, admin_client, deal):
        import uuid

        response = admin_client.post(
            _list_url(deal),
            {"product_id": str(uuid.uuid4()), "quantity": "1"},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert response.json()["errors"]["product_id"] == [MISSING]

    def test_a_malformed_id_is_a_400(self, admin_client, deal):
        response = admin_client.post(
            _list_url(deal),
            {"product_id": "not-a-uuid", "quantity": "1"},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert not OpportunityLineItem.objects.exists()

    def test_own_product_is_attached_and_prices_the_line(
        self, admin_client, deal, own_product
    ):
        response = admin_client.post(
            _list_url(deal),
            {"product_id": str(own_product.id), "quantity": "2"},
            format="json",
        )

        assert response.status_code == 201, response.content
        line = OpportunityLineItem.objects.get()
        assert line.product == own_product
        assert line.unit_price == Decimal("40")
        deal.refresh_from_db()
        assert deal.amount == Decimal("80")

    def test_another_orgs_product_is_a_400_on_update(
        self, admin_client, org_a, deal, own_product, other_product
    ):
        line = OpportunityLineItem.objects.create(
            opportunity=deal, org=org_a, product=own_product, quantity=Decimal("1")
        )

        refused = admin_client.put(
            _detail_url(deal, line),
            {"product_id": str(other_product.id)},
            format="json",
        )
        assert refused.status_code == 400, refused.content
        assert refused.json()["errors"]["product_id"] == [MISSING]
        line.refresh_from_db()
        assert line.product == own_product

        cleared = admin_client.put(
            _detail_url(deal, line), {"product_id": None}, format="json"
        )
        assert cleared.status_code == 200, cleared.content
        line.refresh_from_db()
        assert line.product is None


@pytest.mark.django_db
class TestLineNumbers:
    @pytest.mark.parametrize(
        "body, field",
        [
            ({"discount_type": "FIXED", "discount_value": "50.01"}, "discount_value"),
            ({"discount_type": "", "discount_value": "50.01"}, "discount_value"),
            (
                {"discount_type": "PERCENTAGE", "discount_value": "101"},
                "discount_value",
            ),
            ({"discount_type": "FIXED", "discount_value": "-1"}, "discount_value"),
            ({"quantity": "0"}, "quantity"),
            ({"unit_price": "-1"}, "unit_price"),
        ],
    )
    def test_a_line_that_would_pay_the_customer_is_a_400(
        self, admin_client, deal, body, field
    ):
        payload = {"name": "Seat", "quantity": "1", "unit_price": "50", **body}

        response = admin_client.post(_list_url(deal), payload, format="json")

        assert response.status_code == 400, response.content
        assert field in response.json()["errors"]
        assert not OpportunityLineItem.objects.exists()

    def test_the_whole_line_off_is_allowed(self, admin_client, deal):
        response = admin_client.post(
            _list_url(deal),
            {
                "name": "Seat",
                "quantity": "1",
                "unit_price": "50",
                "discount_type": "FIXED",
                "discount_value": "50",
            },
            format="json",
        )

        assert response.status_code == 201, response.content
        assert OpportunityLineItem.objects.get().total == Decimal("0")

    def test_discount_is_checked_against_the_product_price(
        self, admin_client, deal, own_product
    ):
        """A line with no price takes its product's (40), so 40 off is the
        whole line and 40.01 is more than it."""
        base = {"product_id": str(own_product.id), "discount_type": "FIXED"}

        refused = admin_client.post(
            _list_url(deal), {**base, "discount_value": "40.01"}, format="json"
        )
        assert refused.status_code == 400, refused.content

        allowed = admin_client.post(
            _list_url(deal), {**base, "discount_value": "40"}, format="json"
        )
        assert allowed.status_code == 201, allowed.content

    def test_partial_update_checks_against_the_stored_line(
        self, admin_client, org_a, deal
    ):
        line = OpportunityLineItem.objects.create(
            opportunity=deal,
            org=org_a,
            name="Seat",
            quantity=Decimal("2"),
            unit_price=Decimal("50"),
        )

        refused = admin_client.put(
            _detail_url(deal, line),
            {"discount_type": "FIXED", "discount_value": "100.01"},
            format="json",
        )
        assert refused.status_code == 400, refused.content

        allowed = admin_client.put(
            _detail_url(deal, line),
            {"discount_type": "FIXED", "discount_value": "100"},
            format="json",
        )
        assert allowed.status_code == 200, allowed.content
