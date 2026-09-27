"""The deal board carries what the mobile board shows and filters by.

The mobile board used to build its lanes from every page of the deal list.
It now reads `GET /api/opportunities/kanban/` like the web board, so the card
carries the fields the phone's deal card shows (tags, a product count) and
sorts a lane by (`updated_at`, `stage_changed_at`), and the board takes the
same filters as the list (`deal_list_queryset`), so every filter on the phone
still narrows the board.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from common.models import Tags
from opportunity.models import Opportunity, OpportunityLineItem

URL = "/api/opportunities/kanban/"


def _deal(org, name, **kwargs):
    kwargs.setdefault("stage", "PROSPECTING")
    kwargs.setdefault("kanban_order", Decimal("1000"))
    return Opportunity.objects.create(org=org, name=name, **kwargs)


def _cards(client, **params):
    response = client.get(URL, params)
    assert response.status_code == 200, response.content
    return {
        card["name"]: card
        for column in response.json()["columns"]
        for card in column["items"]
    }


class TestCardFields:
    def test_tags_line_items_and_timestamps_are_on_the_card(self, admin_client, org_a):
        deal = _deal(org_a, "Big", amount=Decimal("500"), currency="EUR")
        tag = Tags.objects.create(org=org_a, name="Hot", slug="hot")
        deal.tags.add(tag)
        OpportunityLineItem.objects.create(
            org=org_a,
            opportunity=deal,
            name="Seat",
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
        )

        card = _cards(admin_client)["Big"]
        assert [t["name"] for t in card["tags"]] == ["Hot"]
        assert [line["name"] for line in card["line_items"]] == ["Seat"]
        assert set(card["line_items"][0]) == {
            "id",
            "name",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
        }
        assert card["updated_at"]
        assert "stage_changed_at" in card
        # Unchanged fields the web board reads. The line sets the amount.
        assert card["amount"] == "200.00"
        assert card["currency"] == "EUR"
        assert card["can_move"] is True

    def test_the_new_fields_add_no_query_per_card(self, admin_client, org_a):
        tag = Tags.objects.create(org=org_a, name="Hot", slug="hot")

        def add(n):
            for i in range(n):
                deal = _deal(org_a, f"d{n}-{i}")
                deal.tags.add(tag)
                OpportunityLineItem.objects.create(
                    org=org_a, opportunity=deal, name="x", quantity=1, unit_price=1
                )

        add(2)
        with CaptureQueriesContext(connection) as few:
            admin_client.get(URL)
        add(6)
        with CaptureQueriesContext(connection) as many:
            admin_client.get(URL)
        assert len(many) == len(few)


class TestBoardTakesTheListFilters:
    def test_amount_range(self, admin_client, org_a):
        _deal(org_a, "small", amount=Decimal("10"))
        _deal(org_a, "large", amount=Decimal("5000"))
        assert set(_cards(admin_client, amount__gte="100")) == {"large"}
        assert set(_cards(admin_client, amount__lte="100")) == {"small"}

    def test_created_range(self, admin_client, org_a):
        old = _deal(org_a, "old")
        Opportunity.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        _deal(org_a, "new")
        since = (timezone.now() - timedelta(days=2)).date().isoformat()
        assert set(_cards(admin_client, created_at__gte=since)) == {"new"}

    def test_name_as_the_phone_sends_it(self, admin_client, org_a):
        _deal(org_a, "Acme renewal")
        _deal(org_a, "Globex")
        assert set(_cards(admin_client, name="acme")) == {"Acme renewal"}
        assert set(_cards(admin_client, search="globex")) == {"Globex"}

    def test_stalled_only(self, admin_client, org_a):
        stalled = _deal(org_a, "stalled")
        Opportunity.objects.filter(pk=stalled.pk).update(
            stage_changed_at=timezone.now() - timedelta(days=400)
        )
        _deal(org_a, "fresh")
        assert set(_cards(admin_client, rotten="true")) == {"stalled"}

    def test_a_filter_narrows_the_count_too(self, admin_client, org_a):
        _deal(org_a, "small", amount=Decimal("10"))
        _deal(org_a, "large", amount=Decimal("5000"))
        response = admin_client.get(URL, {"amount__gte": "100"})
        column = next(c for c in response.json()["columns"] if c["id"] == "PROSPECTING")
        assert column["item_count"] == 1
        assert response.json()["total_items"] == 1

    def test_a_malformed_amount_is_a_400(self, admin_client, org_a):
        response = admin_client.get(URL, {"amount__gte": "lots"})
        assert response.status_code == 400

    def test_the_read_rule_still_applies(
        self, user_client, user_profile, admin_user, org_a
    ):
        mine = _deal(org_a, "mine", created_by=admin_user)
        mine.assigned_to.add(user_profile)
        _deal(org_a, "theirs", created_by=admin_user)
        assert set(_cards(user_client)) == {"mine"}
        assert set(_cards(user_client, name="theirs")) == set()
