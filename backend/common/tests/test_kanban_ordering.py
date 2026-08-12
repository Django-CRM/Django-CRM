"""Contract for the shared kanban ordering helper.

Every board endpoint (leads, cases, tasks, opportunities) computes a card's
position with the same four rules, and each of the four copies got a different
subset of them wrong. These tests pin the rules once, against the helper the
four views now share.

Opportunity is the model under test only because it is the module the board UI
lands in first. The helper takes a queryset, so nothing here is deal-specific.
"""

from decimal import Decimal

import pytest

from common.kanban import MIN_GAP, STRIDE, place_in_column, rebalance_column
from opportunity.models import Opportunity


def _deal(org, name, stage="PROSPECTING", order=None):
    return Opportunity.objects.create(
        org=org, name=name, stage=stage, kanban_order=order
    )


def _column(org, stage="PROSPECTING"):
    return Opportunity.objects.filter(org=org, stage=stage)


def _orders(org, stage="PROSPECTING"):
    return [d.kanban_order for d in _column(org, stage).order_by("kanban_order")]


@pytest.mark.django_db
class TestAppend:
    def test_first_card_in_an_empty_column_gets_the_stride(self, org_a):
        assert place_in_column(_column(org_a)) == STRIDE

    def test_no_hints_appends_after_the_last_card(self, org_a):
        _deal(org_a, "a", order=Decimal("1000"))
        _deal(org_a, "b", order=Decimal("2000"))
        assert place_in_column(_column(org_a)) == Decimal("3000")

    def test_the_moving_card_is_not_its_own_neighbour(self, org_a):
        _deal(org_a, "a", order=Decimal("1000"))
        moving = _deal(org_a, "moving", order=Decimal("9999"))
        # Without the exclusion the card appends after itself and drifts up the
        # column by a stride on every no-op move.
        assert place_in_column(_column(org_a), exclude_pk=moving.pk) == Decimal("2000")


@pytest.mark.django_db
class TestBetweenTwoCards:
    def test_both_hints_average_the_neighbours(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        b = _deal(org_a, "b", order=Decimal("2000"))
        got = place_in_column(_column(org_a), above_id=a.pk, below_id=b.pk)
        assert got == Decimal("1500")

    def test_above_only_averages_against_the_next_card_down(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        _deal(org_a, "b", order=Decimal("2000"))
        assert place_in_column(_column(org_a), above_id=a.pk) == Decimal("1500")

    def test_above_the_last_card_appends(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        assert place_in_column(_column(org_a), above_id=a.pk) == Decimal("2000")


@pytest.mark.django_db
class TestBelowOnly:
    """The branch every copy got wrong: a flat ``below - STRIDE``."""

    def test_below_only_averages_against_the_card_above_it(self, org_a):
        _deal(org_a, "a", order=Decimal("1000"))
        _deal(org_a, "b", order=Decimal("2000"))
        c = _deal(org_a, "c", order=Decimal("3000"))
        # The old code returned 3000 - 1000 = 2000, colliding exactly with b.
        got = place_in_column(_column(org_a), below_id=c.pk)
        assert got == Decimal("2500")
        assert got not in _orders(org_a)

    def test_below_the_first_card_offsets_by_a_stride(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        assert place_in_column(_column(org_a), below_id=a.pk) == Decimal("0")


@pytest.mark.django_db
class TestNeighboursOutsideTheColumn:
    def test_a_neighbour_in_another_column_is_ignored(self, org_a):
        _deal(org_a, "here", order=Decimal("1000"))
        elsewhere = _deal(
            org_a, "elsewhere", stage="NEGOTIATION", order=Decimal("500000")
        )
        # Resolving neighbours from the destination queryset means a foreign id
        # simply is not found, so we append instead of returning 501000.
        got = place_in_column(_column(org_a), above_id=elsewhere.pk)
        assert got == Decimal("2000")

    def test_a_neighbour_in_another_org_is_ignored(self, org_a, org_b):
        _deal(org_a, "here", order=Decimal("1000"))
        theirs = _deal(org_b, "theirs", order=Decimal("777"))
        assert place_in_column(_column(org_a), above_id=theirs.pk) == Decimal("2000")

    def test_an_unknown_id_is_ignored(self, org_a):
        _deal(org_a, "here", order=Decimal("1000"))
        missing = "0" * 8 + "-0000-0000-0000-" + "0" * 12
        assert place_in_column(_column(org_a), above_id=missing) == Decimal("2000")


@pytest.mark.django_db
class TestExplicitOrder:
    def test_an_explicit_order_wins_over_the_hints(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        b = _deal(org_a, "b", order=Decimal("2000"))
        got = place_in_column(
            _column(org_a), above_id=a.pk, below_id=b.pk, explicit=Decimal("42")
        )
        assert got == Decimal("42")


@pytest.mark.django_db
class TestRebalance:
    def test_a_collapsed_gap_renumbers_the_column(self, org_a):
        a = _deal(org_a, "a", order=Decimal("1000"))
        b = _deal(org_a, "b", order=Decimal("1000.000001"))
        _deal(org_a, "c", order=Decimal("2000"))

        got = place_in_column(_column(org_a), above_id=a.pk, below_id=b.pk)

        # The column was renumbered to clean strides, so there is room again.
        assert _orders(org_a) == [STRIDE, STRIDE * 2, STRIDE * 3]
        assert got == Decimal("1500")

    def test_rebalance_preserves_the_visible_order(self, org_a):
        for i, order in enumerate([Decimal("5"), Decimal("7"), Decimal("9")]):
            _deal(org_a, f"deal-{i}", order=order)

        rebalance_column(_column(org_a))

        names = [d.name for d in _column(org_a).order_by("kanban_order")]
        assert names == ["deal-0", "deal-1", "deal-2"]
        assert _orders(org_a) == [STRIDE, STRIDE * 2, STRIDE * 3]

    def test_min_gap_leaves_headroom_over_the_column_resolution(self):
        # kanban_order is DecimalField(decimal_places=6), so two orders closer
        # than 1e-6 cannot both be stored. Rebalancing has to trigger before
        # that, not at it.
        assert MIN_GAP > Decimal("0.000002")
