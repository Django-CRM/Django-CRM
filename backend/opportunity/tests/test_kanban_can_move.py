"""Each deal board card says whether the viewer may move it (`can_move`).

The flag is `has_deal_access`, the one-deal form of the `visible_deals_qs`
rule that `OpportunityMoveView` looks the deal up through. The board reads
with that same rule, so every card a caller is shown is one they may move and
the flag is True on each; a deal outside the rule is not on the board and its
move answers 404. The flag is there so both clients gate the drag on the one
field every kanban card carries, and so it follows the move rule if the two
ever part.
"""

from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test.utils import CaptureQueriesContext

from opportunity.models import Opportunity
from opportunity.next_activity import attach_next_activity
from opportunity.serializer import OpportunityKanbanCardSerializer


def _deal(org, name="deal", **kwargs):
    kwargs.setdefault("stage", "PROSPECTING")
    kwargs.setdefault("kanban_order", Decimal("1000"))
    return Opportunity.objects.create(org=org, name=name, **kwargs)


def _cards(client):
    response = client.get("/api/opportunities/kanban/")
    assert response.status_code == 200, response.content
    return {
        card["id"]: card for col in response.json()["columns"] for card in col["items"]
    }


def _move(client, deal):
    return client.patch(
        f"/api/opportunities/{deal.id}/move/",
        {"column_id": "NEGOTIATION"},
        format="json",
    )


class TestDealCardCanMove:
    def test_an_assignee_may_move_and_the_card_says_so(
        self, user_client, user_profile, admin_user, org_a
    ):
        deal = _deal(org_a, created_by=admin_user)
        deal.assigned_to.add(user_profile)

        assert _cards(user_client)[str(deal.id)]["can_move"] is True
        response = _move(user_client, deal)
        assert response.status_code == 200, response.content
        assert response.json()["opportunity"]["can_move"] is True

    def test_the_creator_may_move(self, user_client, regular_user, org_a):
        deal = _deal(org_a, created_by=regular_user)
        assert _cards(user_client)[str(deal.id)]["can_move"] is True
        assert _move(user_client, deal).status_code == 200

    def test_an_admin_may_move_every_card(self, admin_client, regular_user, org_a):
        deal = _deal(org_a, created_by=regular_user)
        assert _cards(admin_client)[str(deal.id)]["can_move"] is True
        assert _move(admin_client, deal).status_code == 200

    def test_a_superuser_on_a_member_profile_may_move(
        self, user_client, regular_user, admin_user, org_a
    ):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        deal = _deal(org_a, created_by=admin_user)
        assert _cards(user_client)[str(deal.id)]["can_move"] is True
        assert _move(user_client, deal).status_code == 200

    def test_a_deal_the_caller_cannot_open_is_absent_and_404s(
        self, user_client, admin_user, org_a
    ):
        hidden = _deal(org_a, created_by=admin_user)
        assert str(hidden.id) not in _cards(user_client)
        assert _move(user_client, hidden).status_code == 404

    def test_the_rule_refuses_a_deal_outside_it(
        self, user_profile, regular_user, admin_user, org_a
    ):
        hidden = _deal(org_a, created_by=admin_user)
        attach_next_activity([hidden], user_profile)
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        data = OpportunityKanbanCardSerializer(hidden, context={"request": viewer}).data
        assert data["can_move"] is False

    def test_without_a_request_the_flag_is_false(
        self, regular_user, user_profile, org_a
    ):
        deal = _deal(org_a, created_by=regular_user)
        attach_next_activity([deal], user_profile)
        assert OpportunityKanbanCardSerializer(deal).data["can_move"] is False

    def test_the_flag_costs_no_query_per_card(
        self, user_profile, regular_user, admin_user, org_a
    ):
        for n in range(4):
            _deal(org_a, f"held {n}", created_by=admin_user).assigned_to.add(
                user_profile
            )
        deals = attach_next_activity(
            list(
                Opportunity.objects.filter(org=org_a)
                .select_related("account")
                .prefetch_related("assigned_to", "tags")
            ),
            user_profile,
        )
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        OpportunityKanbanCardSerializer(deals, many=True).data

        with CaptureQueriesContext(connection) as without_flag:
            OpportunityKanbanCardSerializer(deals, many=True).data
        with CaptureQueriesContext(connection) as with_flag:
            data = OpportunityKanbanCardSerializer(
                deals, many=True, context={"request": viewer}
            ).data
        assert all(card["can_move"] for card in data)
        assert len(with_flag) == len(without_flag)
