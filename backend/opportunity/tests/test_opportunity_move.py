"""PATCH /api/opportunities/<pk>/move/.

This endpoint had no tests at all while leads, cases and tasks each had a move
test class, which is how the creator branch of its permission check stayed dead
long enough to ship. The RBAC tests below pin both answers: a non-admin creator
gets through, an unrelated non-admin does not.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from opportunity.models import Opportunity


def _deal(org, name="deal", **kwargs):
    kwargs.setdefault("stage", "PROSPECTING")
    kwargs.setdefault("kanban_order", Decimal("1000"))
    return Opportunity.objects.create(org=org, name=name, **kwargs)


def _move(client, deal, **payload):
    payload.setdefault("column_id", "NEGOTIATION")
    return client.patch(
        f"/api/opportunities/{deal.id}/move/", payload, content_type="application/json"
    )


@pytest.mark.django_db
class TestMoveRBAC:
    def test_non_admin_creator_may_move_their_own_deal(
        self, user_client, regular_user, user_profile, org_a
    ):
        """The branch that was dead: creator, not assigned to themselves."""
        deal = _deal(org_a, created_by=regular_user)
        assert deal.created_by == regular_user
        assert user_profile not in deal.assigned_to.all()

        res = _move(user_client, deal)

        assert res.status_code == 200
        deal.refresh_from_db()
        assert deal.stage == "NEGOTIATION"

    def test_non_admin_assignee_may_move(
        self, user_client, admin_user, user_profile, org_a
    ):
        deal = _deal(org_a, created_by=admin_user)
        deal.assigned_to.add(user_profile)

        assert _move(user_client, deal).status_code == 200

    def test_unrelated_non_admin_is_refused(self, user_client, admin_user, org_a):
        deal = _deal(org_a, created_by=admin_user)

        res = _move(user_client, deal)

        assert res.status_code == 403
        deal.refresh_from_db()
        assert deal.stage == "PROSPECTING"

    def test_admin_may_move_any_deal(self, admin_client, regular_user, org_a):
        deal = _deal(org_a, created_by=regular_user)
        assert _move(admin_client, deal).status_code == 200

    def test_another_orgs_deal_is_not_found(self, org_b_client, org_a):
        deal = _deal(org_a)
        assert _move(org_b_client, deal).status_code == 404


@pytest.mark.django_db
class TestMoveContract:
    def test_column_id_is_required(self, admin_client, org_a):
        deal = _deal(org_a)
        res = admin_client.patch(
            f"/api/opportunities/{deal.id}/move/", {}, content_type="application/json"
        )
        assert res.status_code == 400
        assert "column_id" in res.json()["errors"]

    def test_an_unknown_column_is_rejected(self, admin_client, org_a):
        deal = _deal(org_a)
        assert _move(admin_client, deal, column_id="NOT_A_STAGE").status_code == 400

    def test_a_drop_between_two_cards_lands_between_them(self, admin_client, org_a):
        above = _deal(org_a, "above", stage="NEGOTIATION", kanban_order=Decimal("1000"))
        below = _deal(org_a, "below", stage="NEGOTIATION", kanban_order=Decimal("2000"))
        moving = _deal(org_a, "moving")

        res = _move(
            admin_client, moving, above_id=str(above.id), below_id=str(below.id)
        )

        assert res.status_code == 200
        moving.refresh_from_db()
        assert above.kanban_order < moving.kanban_order < below.kanban_order


@pytest.mark.django_db
class TestClosingByDragging:
    def test_dragging_to_won_stamps_the_close(self, admin_client, admin_profile, org_a):
        deal = _deal(org_a, amount=Decimal("5000"))

        res = _move(admin_client, deal, column_id="CLOSED_WON")

        assert res.status_code == 200
        deal.refresh_from_db()
        assert deal.stage == "CLOSED_WON"
        assert deal.closed_on == timezone.localdate()
        assert deal.closed_by == admin_profile

    def test_an_existing_expected_close_date_is_not_overwritten(
        self, admin_client, org_a
    ):
        """`closed_on` is the owner's expected close date until the deal closes.

        Stamping today over it would silently discard a forecast the user set,
        and the drag carries no date to replace it with.
        """
        expected = timezone.localdate() - timedelta(days=30)
        deal = _deal(org_a, amount=Decimal("5000"), closed_on=expected)

        assert _move(admin_client, deal, column_id="CLOSED_WON").status_code == 200

        deal.refresh_from_db()
        assert deal.closed_on == expected

    def test_dragging_to_won_without_an_amount_is_refused(self, admin_client, org_a):
        deal = _deal(org_a, amount=None)

        res = _move(admin_client, deal, column_id="CLOSED_WON")

        assert res.status_code == 400
        assert "amount" in res.json()["errors"]
        deal.refresh_from_db()
        assert deal.stage == "PROSPECTING"
        assert deal.closed_on is None

    def test_dragging_to_lost_needs_no_amount(self, admin_client, org_a):
        deal = _deal(org_a, amount=None)

        assert _move(admin_client, deal, column_id="CLOSED_LOST").status_code == 200
        deal.refresh_from_db()
        assert deal.closed_on == timezone.localdate()

    def test_reopening_clears_who_closed_it_but_keeps_the_date(
        self, admin_client, admin_profile, org_a
    ):
        """An open deal claiming a closer is a lie; a close date is a forecast."""
        closed_on = timezone.localdate()
        deal = _deal(
            org_a,
            stage="CLOSED_WON",
            amount=Decimal("5000"),
            closed_on=closed_on,
            closed_by=admin_profile,
        )

        assert _move(admin_client, deal, column_id="NEGOTIATION").status_code == 200

        deal.refresh_from_db()
        assert deal.closed_by is None
        assert deal.closed_on == closed_on


@pytest.mark.django_db
class TestProbability:
    def test_a_stage_change_refreshes_the_probability(self, admin_client, org_a):
        """save() only fills a 0/None probability, so the move has to set it."""
        deal = _deal(org_a, stage="NEGOTIATION", probability=75)

        assert _move(admin_client, deal, column_id="CLOSED_LOST").status_code == 200

        deal.refresh_from_db()
        assert deal.probability == 0
