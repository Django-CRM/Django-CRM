"""A goal's target is one number, so a goal has one currency.

Progress used to add the amount of every won deal in the period regardless of
its currency, so a USD quota was "met" by a EUR deal and an org selling in two
currencies read a sum of unlike units as attainment. There are no exchange
rates here, so a REVENUE goal now counts only the won deals in its own
currency, with a blank deal currency meaning the org default (the rule
`common.money.deal_currency` owns). DEALS_CLOSED and ACTIVITIES are counts and
are not currency-bound.
"""

import importlib
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps as global_apps
from django.db import connection
from django.utils import timezone

from common.models import Org
from opportunity.models import Opportunity, SalesGoal

GOALS_URL = "/api/opportunities/goals/"


def _month():
    today = timezone.localdate()
    start = today.replace(day=1)
    end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def _goal(org, profile, goal_type="REVENUE", currency="USD", target="1000", **kw):
    start, end = _month()
    fields = {
        "name": f"{goal_type} {currency}",
        "goal_type": goal_type,
        "target_value": Decimal(target),
        "period_type": "MONTHLY",
        "period_start": start,
        "period_end": end,
        "assigned_to": profile,
        "currency": currency,
        "org": org,
    }
    fields.update(kw)
    return SalesGoal.objects.create(**fields)


def _won(org, user, profile, amount, currency, closed_on=None):
    opp = Opportunity.objects.create(
        name=f"Won {amount} {currency}",
        stage="CLOSED_WON",
        amount=Decimal(str(amount)),
        currency=currency,
        closed_on=closed_on or timezone.localdate(),
        org=org,
        created_by=user,
    )
    opp.assigned_to.add(profile)
    return opp


def _listed_progress(client, goal_id):
    goals = client.get(GOALS_URL).json()["goals"]
    return next(g for g in goals if g["id"] == str(goal_id))["progress_value"]


@pytest.mark.django_db
class TestRevenueCountsOnlyTheGoalsCurrency:
    def test_a_usd_goal_ignores_a_won_eur_deal(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _goal(org_a, admin_profile, currency="USD")
        _won(org_a, admin_user, admin_profile, 300, "USD")
        _won(org_a, admin_user, admin_profile, 500, "EUR")

        assert goal.compute_progress() == Decimal("300")
        # The batched path the list uses must agree with the single-goal one.
        assert _listed_progress(admin_client, goal.id) == 300.0

    def test_a_eur_goal_counts_only_the_eur_deal(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _goal(org_a, admin_profile, currency="EUR")
        _won(org_a, admin_user, admin_profile, 300, "USD")
        _won(org_a, admin_user, admin_profile, 500, "EUR")

        assert goal.compute_progress() == Decimal("500")
        assert _listed_progress(admin_client, goal.id) == 500.0

    @pytest.mark.parametrize("blank", [None, ""])
    def test_a_blank_currency_deal_counts_when_the_goal_is_in_the_org_default(
        self, blank, admin_client, org_a, admin_user, admin_profile
    ):
        org_a.default_currency = "GBP"
        org_a.save(update_fields=["default_currency"])
        goal = _goal(org_a, admin_profile, currency="GBP")
        _won(org_a, admin_user, admin_profile, 700, blank)

        assert goal.compute_progress() == Decimal("700")
        assert _listed_progress(admin_client, goal.id) == 700.0

    def test_a_blank_currency_deal_does_not_count_toward_another_currency(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        # org_a's default is USD, so a blank-currency deal is a USD deal.
        goal = _goal(org_a, admin_profile, currency="EUR")
        _won(org_a, admin_user, admin_profile, 700, None)

        assert goal.compute_progress() == Decimal("0")
        assert _listed_progress(admin_client, goal.id) == 0.0


@pytest.mark.django_db
class TestCountGoalsIgnoreCurrency:
    def test_a_deals_closed_goal_counts_deals_in_every_currency(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _goal(org_a, admin_profile, goal_type="DEALS_CLOSED", currency="EUR")
        _won(org_a, admin_user, admin_profile, 300, "USD")
        _won(org_a, admin_user, admin_profile, 500, "EUR")
        _won(org_a, admin_user, admin_profile, 900, None)

        assert goal.compute_progress() == Decimal("3")
        assert _listed_progress(admin_client, goal.id) == 3.0


@pytest.mark.django_db
class TestGoalCurrencyOnWrite:
    def _payload(self, **overrides):
        today = timezone.localdate()
        payload = {
            "name": "Q3 Revenue",
            "goal_type": "REVENUE",
            "target_value": "50000",
            "period_type": "QUARTERLY",
            "period_start": str(today),
            "period_end": str(today + timedelta(days=90)),
        }
        payload.update(overrides)
        return payload

    def test_a_new_goal_without_a_currency_takes_the_org_default(
        self, admin_client, org_a
    ):
        org_a.default_currency = "INR"
        org_a.save(update_fields=["default_currency"])

        response = admin_client.post(GOALS_URL, self._payload(), format="json")

        assert response.status_code == 201, response.json()
        assert SalesGoal.objects.get(org=org_a).currency == "INR"

    def test_a_new_goal_keeps_the_currency_it_was_given(self, admin_client, org_a):
        response = admin_client.post(
            GOALS_URL, self._payload(currency="EUR"), format="json"
        )

        assert response.status_code == 201, response.json()
        goal = SalesGoal.objects.get(org=org_a)
        assert goal.currency == "EUR"
        detail = admin_client.get(f"{GOALS_URL}{goal.id}/").json()
        assert detail["currency"] == "EUR"

    @pytest.mark.parametrize("bad", ["XYZ", "usd", "EURO", 5])
    def test_rejects_a_currency_that_is_not_a_choice(self, bad, admin_client, org_a):
        response = admin_client.post(
            GOALS_URL, self._payload(currency=bad), format="json"
        )

        assert response.status_code == 400
        assert "currency" in response.json()["errors"]
        assert not SalesGoal.objects.filter(org=org_a).exists()

    def test_rejects_an_invalid_currency_on_update(
        self, admin_client, org_a, admin_profile
    ):
        goal = _goal(org_a, admin_profile, currency="USD")

        response = admin_client.put(
            f"{GOALS_URL}{goal.id}/", {"currency": "XYZ"}, format="json"
        )

        assert response.status_code == 400
        goal.refresh_from_db()
        assert goal.currency == "USD"

    def test_changing_the_currency_clears_the_milestone_flags(
        self, admin_client, org_a, admin_profile
    ):
        """A new currency is a new bar, the same as a new target."""
        goal = _goal(
            org_a,
            admin_profile,
            currency="USD",
            milestone_50_notified=True,
            milestone_90_notified=True,
            milestone_100_notified=True,
        )

        response = admin_client.put(
            f"{GOALS_URL}{goal.id}/", {"currency": "EUR"}, format="json"
        )

        assert response.status_code == 200
        goal.refresh_from_db()
        assert goal.currency == "EUR"
        assert not goal.milestone_50_notified
        assert not goal.milestone_100_notified

    def test_renaming_keeps_the_milestone_flags(
        self, admin_client, org_a, admin_profile
    ):
        goal = _goal(org_a, admin_profile, currency="USD", milestone_50_notified=True)

        admin_client.put(
            f"{GOALS_URL}{goal.id}/",
            {"name": "Renamed", "currency": "USD"},
            format="json",
        )

        goal.refresh_from_db()
        assert goal.milestone_50_notified


@pytest.mark.django_db
class TestEveryProgressSurfaceUsesTheRule:
    def test_leaderboard_counts_only_the_goals_currency_and_names_it(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        _goal(org_a, admin_profile, currency="USD")
        _won(org_a, admin_user, admin_profile, 400, "USD")
        _won(org_a, admin_user, admin_profile, 600, "EUR")

        rows = admin_client.get(f"{GOALS_URL}leaderboard/").json()["leaderboard"]

        assert len(rows) == 1
        assert rows[0]["achieved"] == 400.0
        assert rows[0]["percent"] == 40
        assert rows[0]["currency"] == "USD"
        assert rows[0]["goal_type"] == "REVENUE"

    def test_leaderboard_still_hides_a_colleagues_goal_from_a_member(
        self, user_client, org_a, admin_user, admin_profile, user_profile
    ):
        _goal(org_a, admin_profile, currency="USD", name="Admin")
        _goal(org_a, user_profile, currency="EUR", name="Mine")

        rows = user_client.get(f"{GOALS_URL}leaderboard/").json()["leaderboard"]

        assert [r["goal_name"] for r in rows] == ["Mine"]
        assert rows[0]["currency"] == "EUR"

    def test_history_keeps_two_currencies_in_separate_rows(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        end = timezone.localdate() - timedelta(days=5)
        window = {"period_start": end - timedelta(days=29), "period_end": end}
        _goal(org_a, admin_profile, currency="USD", target="1000", **window)
        _goal(org_a, admin_profile, currency="EUR", target="2000", **window)
        _goal(
            org_a, admin_profile, "DEALS_CLOSED", currency="EUR", target="4", **window
        )
        _won(org_a, admin_user, admin_profile, 500, "USD", closed_on=end)
        _won(org_a, admin_user, admin_profile, 2000, "EUR", closed_on=end)

        rows = admin_client.get(f"{GOALS_URL}history/").json()["history"]

        by_unit = {(r["goal_type"], r["currency"]): r for r in rows}
        assert set(by_unit) == {
            ("REVENUE", "USD"),
            ("REVENUE", "EUR"),
            ("DEALS_CLOSED", None),
        }
        assert by_unit[("REVENUE", "USD")]["target"] == 1000.0
        assert by_unit[("REVENUE", "USD")]["achieved"] == 500.0
        assert by_unit[("REVENUE", "EUR")]["target"] == 2000.0
        assert by_unit[("REVENUE", "EUR")]["achieved"] == 2000.0
        assert by_unit[("REVENUE", "EUR")]["attained_count"] == 1
        assert by_unit[("DEALS_CLOSED", None)]["achieved"] == 2.0

    def test_dashboard_summary_carries_the_currency(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        _goal(org_a, admin_profile, currency="EUR")
        _won(org_a, admin_user, admin_profile, 250, "EUR")
        _won(org_a, admin_user, admin_profile, 900, "USD")

        summary = admin_client.get("/api/dashboard/").json()["goal_summary"]

        assert summary[0]["currency"] == "EUR"
        assert summary[0]["progress_value"] == 250.0

    @patch("opportunity.tasks._send_goal_milestone_email")
    def test_a_deal_in_another_currency_does_not_trip_a_milestone(
        self, mock_send, org_a, admin_user, admin_profile
    ):
        from opportunity.tasks import check_goal_milestones

        goal = _goal(org_a, admin_profile, currency="USD", target="100")
        _won(org_a, admin_user, admin_profile, 90, "EUR")

        check_goal_milestones()

        goal.refresh_from_db()
        assert not goal.milestone_50_notified
        assert not mock_send.called

    @patch("opportunity.tasks._send_goal_milestone_email")
    def test_a_deal_in_the_goals_currency_still_trips_a_milestone(
        self, mock_send, org_a, admin_user, admin_profile
    ):
        from opportunity.tasks import check_goal_milestones

        goal = _goal(org_a, admin_profile, currency="USD", target="100")
        _won(org_a, admin_user, admin_profile, 90, "USD")

        check_goal_milestones()

        goal.refresh_from_db()
        assert goal.milestone_50_notified
        assert goal.milestone_90_notified
        assert mock_send.called


@pytest.mark.django_db
class TestModelDefault:
    def test_a_goal_saved_without_a_currency_takes_the_org_default(
        self, org_a, admin_profile
    ):
        org_a.default_currency = "JPY"
        org_a.save(update_fields=["default_currency"])

        goal = _goal(org_a, admin_profile, currency=None)

        goal.refresh_from_db()
        assert goal.currency == "JPY"


@pytest.mark.django_db
class TestBackfillMigration:
    """`opportunity/0017_salesgoal_currency` gives existing goals a currency.

    Called directly, the way `common/tests/test_tag_slugs.py` exercises its
    data migration: running `migrate` backwards and forwards in a test would
    rebuild the whole graph for one function.
    """

    def _backfill(self):
        module = importlib.import_module(
            "opportunity.migrations.0017_salesgoal_currency"
        )
        module.backfill_goal_currency(
            global_apps, SimpleNamespace(connection=connection)
        )

    def test_an_existing_goal_gets_its_orgs_default_currency(
        self, org_a, org_b, admin_profile, profile_b
    ):
        org_a.default_currency = "EUR"
        org_a.save(update_fields=["default_currency"])
        a = _goal(org_a, admin_profile, currency="EUR")
        b = _goal(org_b, profile_b, currency="USD")
        kept = _goal(org_a, admin_profile, currency="GBP", name="Already set")
        # `.update()` skips `save()`, so this is a row as the column's
        # introduction left it.
        SalesGoal.objects.filter(id__in=[a.id, b.id]).update(currency=None)

        self._backfill()

        a.refresh_from_db()
        b.refresh_from_db()
        kept.refresh_from_db()
        assert a.currency == "EUR"
        assert b.currency == "USD"
        assert kept.currency == "GBP"

    def test_an_org_with_no_default_falls_back_to_usd(self, org_a, admin_profile):
        goal = _goal(org_a, admin_profile, currency="EUR")
        SalesGoal.objects.filter(id=goal.id).update(currency=None)
        Org.objects.filter(id=org_a.id).update(default_currency="")

        self._backfill()

        goal.refresh_from_db()
        assert goal.currency == "USD"
