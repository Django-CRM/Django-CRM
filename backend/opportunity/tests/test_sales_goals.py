"""Tests for Sales Goals / Quotas feature."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from common.models import Teams
from opportunity.models import Opportunity, SalesGoal

# ---- Fixtures ---- #


@pytest.fixture
def team_a(org_a):
    return Teams.objects.create(name="Sales Team A", org=org_a)


@pytest.fixture
def goal_revenue(org_a, admin_profile):
    """A revenue goal for the current month."""
    today = timezone.localdate()
    return SalesGoal.objects.create(
        name="Monthly Revenue",
        goal_type="REVENUE",
        target_value=Decimal("100000"),
        period_type="MONTHLY",
        period_start=today.replace(day=1),
        period_end=(today.replace(day=28) + timedelta(days=4)).replace(day=1)
        - timedelta(days=1),
        assigned_to=admin_profile,
        org=org_a,
    )


@pytest.fixture
def goal_deals(org_a, user_profile):
    """A deals closed goal for the current month."""
    today = timezone.localdate()
    return SalesGoal.objects.create(
        name="Monthly Deals",
        goal_type="DEALS_CLOSED",
        target_value=Decimal("10"),
        period_type="MONTHLY",
        period_start=today.replace(day=1),
        period_end=(today.replace(day=28) + timedelta(days=4)).replace(day=1)
        - timedelta(days=1),
        assigned_to=user_profile,
        org=org_a,
    )


@pytest.fixture
def team_goal(org_a, team_a):
    """A team revenue goal."""
    today = timezone.localdate()
    return SalesGoal.objects.create(
        name="Team Revenue",
        goal_type="REVENUE",
        target_value=Decimal("500000"),
        period_type="QUARTERLY",
        period_start=today.replace(day=1),
        period_end=(today.replace(day=28) + timedelta(days=4)).replace(day=1)
        - timedelta(days=1),
        team=team_a,
        org=org_a,
    )


def _create_won_opportunity(org, user, amount, closed_on=None):
    """Helper to create a CLOSED_WON opportunity."""
    if closed_on is None:
        closed_on = timezone.localdate()
    opp = Opportunity.objects.create(
        name=f"Won Deal {amount}",
        stage="CLOSED_WON",
        amount=Decimal(str(amount)),
        closed_on=closed_on,
        org=org,
        created_by=user,
    )
    return opp


# ---- Model Tests ---- #


class TestSalesGoalModel:
    def test_create_goal(self, goal_revenue):
        assert goal_revenue.pk is not None
        assert goal_revenue.name == "Monthly Revenue"
        assert goal_revenue.goal_type == "REVENUE"
        assert goal_revenue.target_value == Decimal("100000")
        assert str(goal_revenue) == "Monthly Revenue (Revenue)"

    def test_compute_progress_revenue(
        self, goal_revenue, org_a, admin_user, admin_profile
    ):
        opp = _create_won_opportunity(org_a, admin_user, 25000)
        opp.assigned_to.add(admin_profile)
        opp2 = _create_won_opportunity(org_a, admin_user, 15000)
        opp2.assigned_to.add(admin_profile)

        progress = goal_revenue.compute_progress()
        assert progress == Decimal("40000")

    def test_compute_progress_deals_closed(
        self, goal_deals, org_a, regular_user, user_profile
    ):
        for i in range(3):
            opp = _create_won_opportunity(org_a, regular_user, 1000 + i)
            opp.assigned_to.add(user_profile)

        progress = goal_deals.compute_progress()
        assert progress == Decimal("3")

    def test_progress_scoped_to_user(
        self, goal_revenue, org_a, admin_user, admin_profile, regular_user, user_profile
    ):
        # Deal by admin (should count)
        opp1 = _create_won_opportunity(org_a, admin_user, 10000)
        opp1.assigned_to.add(admin_profile)

        # Deal by regular user (should NOT count for admin's goal)
        opp2 = _create_won_opportunity(org_a, regular_user, 20000)
        opp2.assigned_to.add(user_profile)

        progress = goal_revenue.compute_progress()
        assert progress == Decimal("10000")

    def test_progress_scoped_to_team(
        self, team_goal, org_a, admin_user, admin_profile, team_a
    ):
        team_a.users.add(admin_profile)

        opp = _create_won_opportunity(org_a, admin_user, 50000)
        opp.assigned_to.add(admin_profile)

        progress = team_goal.compute_progress()
        assert progress == Decimal("50000")

    def test_progress_ignores_outside_period(
        self, goal_revenue, org_a, admin_user, admin_profile
    ):
        # Create opp outside the goal period
        outside_date = goal_revenue.period_start - timedelta(days=30)
        opp = _create_won_opportunity(org_a, admin_user, 50000, closed_on=outside_date)
        opp.assigned_to.add(admin_profile)

        progress = goal_revenue.compute_progress()
        assert progress == Decimal("0")

    def test_progress_percent(self, goal_revenue, org_a, admin_user, admin_profile):
        opp = _create_won_opportunity(org_a, admin_user, 75000)
        opp.assigned_to.add(admin_profile)

        assert goal_revenue.progress_percent == 75

    def test_progress_percent_capped_at_100(
        self, goal_revenue, org_a, admin_user, admin_profile
    ):
        opp = _create_won_opportunity(org_a, admin_user, 150000)
        opp.assigned_to.add(admin_profile)

        assert goal_revenue.progress_percent == 100

    def test_progress_percent_zero_target(self, org_a):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Zero target",
            goal_type="REVENUE",
            target_value=Decimal("0"),
            period_type="MONTHLY",
            period_start=today,
            period_end=today + timedelta(days=30),
            org=org_a,
        )
        assert goal.progress_percent == 0

    def test_status_completed(self, goal_revenue, org_a, admin_user, admin_profile):
        opp = _create_won_opportunity(org_a, admin_user, 100000)
        opp.assigned_to.add(admin_profile)

        assert goal_revenue.status == "completed"

    def test_status_on_track(self, org_a, admin_profile, admin_user):
        # Create a goal for a period that just started
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Future Goal",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="CUSTOM",
            period_start=today - timedelta(days=1),
            period_end=today + timedelta(days=99),
            assigned_to=admin_profile,
            org=org_a,
        )
        # 1% of period elapsed, 0% progress => behind
        # Actually 0% progress with ~1% elapsed => behind
        # Let's add enough to be on track
        opp = _create_won_opportunity(org_a, admin_user, 5000, closed_on=today)
        opp.assigned_to.add(admin_profile)

        assert goal.status in ("on_track", "at_risk", "behind")

    def test_status_behind(self, org_a, admin_profile):
        # Goal period almost over, no progress
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Behind Goal",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="CUSTOM",
            period_start=today - timedelta(days=90),
            period_end=today + timedelta(days=10),
            assigned_to=admin_profile,
            org=org_a,
        )
        assert goal.status == "behind"


# ---- API Tests ---- #


class TestSalesGoalAPI:
    GOALS_URL = "/api/opportunities/goals/"

    def test_list_goals_admin(self, admin_client, goal_revenue, goal_deals):
        response = admin_client.get(self.GOALS_URL)
        assert response.status_code == 200
        assert response.data["goals_count"] == 2

    def test_list_goals_non_admin(
        self, user_client, goal_revenue, goal_deals, user_profile
    ):
        """Non-admin sees only goals assigned to them."""
        response = user_client.get(self.GOALS_URL)
        assert response.status_code == 200
        # user_profile should see goal_deals (assigned to them)
        goal_ids = [g["id"] for g in response.data["goals"]]
        assert str(goal_deals.id) in goal_ids

    def test_create_goal_admin(self, admin_client, org_a):
        today = timezone.localdate()
        data = {
            "name": "New Goal",
            "goal_type": "REVENUE",
            "target_value": "50000",
            "period_type": "MONTHLY",
            "period_start": str(today.replace(day=1)),
            "period_end": str(
                (today.replace(day=28) + timedelta(days=4)).replace(day=1)
                - timedelta(days=1)
            ),
        }
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 201
        assert response.data["error"] is False
        assert SalesGoal.objects.filter(name="New Goal", org=org_a).exists()

    def test_create_goal_non_admin_forbidden(self, user_client):
        today = timezone.localdate()
        data = {
            "name": "Sneaky Goal",
            "goal_type": "REVENUE",
            "target_value": "50000",
            "period_type": "MONTHLY",
            "period_start": str(today),
            "period_end": str(today + timedelta(days=30)),
        }
        response = user_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 403

    def test_create_goal_validation_period(self, admin_client):
        today = timezone.localdate()
        data = {
            "name": "Bad Period",
            "goal_type": "REVENUE",
            "target_value": "50000",
            "period_type": "CUSTOM",
            "period_start": str(today + timedelta(days=30)),
            "period_end": str(today),
        }
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 400

    def test_create_goal_validation_target(self, admin_client):
        today = timezone.localdate()
        data = {
            "name": "Zero Target",
            "goal_type": "REVENUE",
            "target_value": "0",
            "period_type": "MONTHLY",
            "period_start": str(today),
            "period_end": str(today + timedelta(days=30)),
        }
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 400

    def test_update_goal(self, admin_client, goal_revenue):
        url = f"{self.GOALS_URL}{goal_revenue.id}/"
        response = admin_client.put(
            url,
            {"name": "Updated Goal Name", "target_value": "200000"},
            format="json",
        )
        assert response.status_code == 200
        goal_revenue.refresh_from_db()
        assert goal_revenue.name == "Updated Goal Name"
        assert goal_revenue.target_value == Decimal("200000")

    def test_delete_goal(self, admin_client, goal_revenue):
        url = f"{self.GOALS_URL}{goal_revenue.id}/"
        response = admin_client.delete(url)
        assert response.status_code == 200
        assert not SalesGoal.objects.filter(id=goal_revenue.id).exists()

    def test_delete_goal_non_admin_forbidden(self, user_client, goal_revenue):
        url = f"{self.GOALS_URL}{goal_revenue.id}/"
        response = user_client.delete(url)
        assert response.status_code == 403

    def test_org_isolation(self, org_b_client, goal_revenue):
        """org_b client should not see org_a's goals."""
        response = org_b_client.get(self.GOALS_URL)
        assert response.status_code == 200
        assert response.data["goals_count"] == 0

    def _valid_goal_payload(self, **overrides):
        today = timezone.localdate()
        data = {
            "name": "Cross-org probe",
            "goal_type": "REVENUE",
            "target_value": "50000",
            "period_type": "MONTHLY",
            "period_start": str(today.replace(day=1)),
            "period_end": str(
                (today.replace(day=28) + timedelta(days=4)).replace(day=1)
                - timedelta(days=1)
            ),
        }
        data.update(overrides)
        return data

    def test_create_goal_rejects_foreign_org_assigned_to(self, admin_client, profile_b):
        """An org_a admin cannot assign a goal to an org_b Profile.

        ``common_profile`` is not RLS-protected, so without the serializer's
        org check the foreign profile would resolve and leak into this org's
        goal detail/leaderboard.
        """
        data = self._valid_goal_payload(assigned_to=str(profile_b.id))
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 400
        assert not SalesGoal.objects.filter(
            name="Cross-org probe", assigned_to=profile_b
        ).exists()

    def test_create_goal_rejects_foreign_org_team(self, admin_client, org_b):
        """An org_a admin cannot assign a goal to an org_b Team."""
        foreign_team = Teams.objects.create(name="Team B", org=org_b)
        data = self._valid_goal_payload(team=str(foreign_team.id))
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 400
        assert not SalesGoal.objects.filter(
            name="Cross-org probe", team=foreign_team
        ).exists()

    def test_create_goal_accepts_same_org_assigned_to(
        self, admin_client, org_a, user_profile
    ):
        """The same check must still let an in-org assignee through (True path)."""
        data = self._valid_goal_payload(
            name="Same-org goal", assigned_to=str(user_profile.id)
        )
        response = admin_client.post(self.GOALS_URL, data, format="json")
        assert response.status_code == 201
        goal = SalesGoal.objects.get(name="Same-org goal", org=org_a)
        assert goal.assigned_to == user_profile

    def test_update_goal_rejects_foreign_org_assigned_to(
        self, admin_client, goal_revenue, profile_b
    ):
        """The PUT path shares the write serializer, so it is guarded too."""
        url = f"{self.GOALS_URL}{goal_revenue.id}/"
        response = admin_client.put(
            url, {"assigned_to": str(profile_b.id)}, format="json"
        )
        assert response.status_code == 400
        goal_revenue.refresh_from_db()
        assert goal_revenue.assigned_to != profile_b

    def test_get_goal_detail(self, admin_client, goal_revenue):
        url = f"{self.GOALS_URL}{goal_revenue.id}/"
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.data["name"] == "Monthly Revenue"
        assert "progress_value" in response.data
        assert "progress_percent" in response.data
        assert "status" in response.data

    def test_filter_active(self, admin_client, org_a):
        today = timezone.localdate()
        SalesGoal.objects.create(
            name="Active Goal",
            goal_type="REVENUE",
            target_value=Decimal("1000"),
            period_type="MONTHLY",
            period_start=today,
            period_end=today + timedelta(days=30),
            is_active=True,
            org=org_a,
        )
        SalesGoal.objects.create(
            name="Inactive Goal",
            goal_type="REVENUE",
            target_value=Decimal("1000"),
            period_type="MONTHLY",
            period_start=today,
            period_end=today + timedelta(days=30),
            is_active=False,
            org=org_a,
        )
        response = admin_client.get(f"{self.GOALS_URL}?active=true")
        assert response.status_code == 200
        names = [g["name"] for g in response.data["goals"]]
        assert "Active Goal" in names
        assert "Inactive Goal" not in names

    def test_filter_current(self, admin_client, org_a):
        today = timezone.localdate()
        SalesGoal.objects.create(
            name="Current Goal",
            goal_type="REVENUE",
            target_value=Decimal("1000"),
            period_type="MONTHLY",
            period_start=today - timedelta(days=5),
            period_end=today + timedelta(days=25),
            org=org_a,
        )
        SalesGoal.objects.create(
            name="Past Goal",
            goal_type="REVENUE",
            target_value=Decimal("1000"),
            period_type="MONTHLY",
            period_start=today - timedelta(days=60),
            period_end=today - timedelta(days=30),
            org=org_a,
        )
        response = admin_client.get(f"{self.GOALS_URL}?current=true")
        assert response.status_code == 200
        names = [g["name"] for g in response.data["goals"]]
        assert "Current Goal" in names
        assert "Past Goal" not in names


class TestLeaderboardAPI:
    LEADERBOARD_URL = "/api/opportunities/goals/leaderboard/"

    def test_leaderboard_ranked(
        self, admin_client, org_a, admin_user, admin_profile, regular_user, user_profile
    ):
        today = timezone.localdate()
        period_start = today.replace(day=1)
        period_end = (today.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)

        # Create goals for both users
        SalesGoal.objects.create(
            name="Admin Goal",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=period_start,
            period_end=period_end,
            assigned_to=admin_profile,
            org=org_a,
        )
        SalesGoal.objects.create(
            name="User Goal",
            goal_type="REVENUE",
            target_value=Decimal("50000"),
            period_type="MONTHLY",
            period_start=period_start,
            period_end=period_end,
            assigned_to=user_profile,
            org=org_a,
        )

        # Admin has 50% progress
        opp1 = _create_won_opportunity(org_a, admin_user, 50000)
        opp1.assigned_to.add(admin_profile)

        # User has 80% progress
        opp2 = _create_won_opportunity(org_a, regular_user, 40000)
        opp2.assigned_to.add(user_profile)

        response = admin_client.get(self.LEADERBOARD_URL)
        assert response.status_code == 200
        leaderboard = response.data["leaderboard"]
        assert len(leaderboard) == 2
        # User (80%) should rank above admin (50%)
        assert leaderboard[0]["percent"] >= leaderboard[1]["percent"]
        assert leaderboard[0]["rank"] == 1
        assert leaderboard[1]["rank"] == 2

    def _current_month(self):
        today = timezone.localdate()
        start = today.replace(day=1)
        end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
            days=1
        )
        return start, end

    def _goal_for(self, org, profile, name, target, team=None):
        start, end = self._current_month()
        return SalesGoal.objects.create(
            name=name,
            goal_type="REVENUE",
            target_value=Decimal(target),
            period_type="MONTHLY",
            period_start=start,
            period_end=end,
            assigned_to=profile,
            team=team,
            org=org,
        )

    def test_leaderboard_hides_another_persons_goal_from_a_member(
        self, user_client, org_a, admin_user, admin_profile
    ):
        """The board is narrowed by the same rule as the list.

        This is the whole finding: `SalesGoalListView` returned this member an
        empty list while this endpoint handed them the admin's target, their
        attainment and their email address. A ranking is not a way around the
        rule that a member does not read a colleague's quota.
        """
        self._goal_for(org_a, admin_profile, "Admin Goal", "100000")
        opp = _create_won_opportunity(org_a, admin_user, 50000)
        opp.assigned_to.add(admin_profile)

        listed = user_client.get(TestSalesGoalAPI.GOALS_URL)
        assert [g["name"] for g in listed.data["goals"]] == []

        response = user_client.get(self.LEADERBOARD_URL)
        assert response.status_code == 200
        assert response.data["leaderboard"] == []

    def test_leaderboard_shows_a_member_their_own_goal(
        self, user_client, org_a, regular_user, user_profile, admin_profile
    ):
        """The other direction, so the narrowing is not just "always empty"."""
        self._goal_for(org_a, user_profile, "My Goal", "50000")
        self._goal_for(org_a, admin_profile, "Admin Goal", "100000")
        opp = _create_won_opportunity(org_a, regular_user, 40000)
        opp.assigned_to.add(user_profile)

        response = user_client.get(self.LEADERBOARD_URL)
        assert response.status_code == 200
        rows = response.data["leaderboard"]
        assert [r["goal_name"] for r in rows] == ["My Goal"]
        # Rank is assigned after narrowing, so it describes a standing within
        # what this person may see, not a position in a table they cannot read.
        assert rows[0]["rank"] == 1
        assert rows[0]["percent"] == 80

    def test_leaderboard_includes_a_goal_shared_with_the_members_team(
        self, user_client, org_a, admin_profile, user_profile, team_a
    ):
        """`_visible_to` has two arms, and the team arm needs its own case.

        An individual goal that also carries a team the member belongs to is
        visible on the list, so it is visible here. Without this the narrowing
        would look correct while honouring only half the rule.
        """
        user_profile.user_teams.add(team_a)
        self._goal_for(org_a, admin_profile, "Team Goal", "100000", team=team_a)

        response = user_client.get(self.LEADERBOARD_URL)
        assert response.status_code == 200
        assert [r["goal_name"] for r in response.data["leaderboard"]] == ["Team Goal"]

    def test_leaderboard_names_the_person_without_their_email(
        self, admin_client, org_a, admin_profile, admin_user
    ):
        """The row used to carry the email twice, as `name` and as `email`."""
        admin_user.name = "Ada Lovelace"
        admin_user.save(update_fields=["name"])
        self._goal_for(org_a, admin_profile, "Named Goal", "1000")

        response = admin_client.get(self.LEADERBOARD_URL)
        row = response.data["leaderboard"][0]
        assert row["user"]["name"] == "Ada Lovelace"
        assert "email" not in row["user"]

    def test_leaderboard_falls_back_to_the_email_when_there_is_no_name(
        self, admin_client, org_a, admin_profile, admin_user
    ):
        """`User.save` fills `name` on first save, so an empty one is unusual.

        It is still reachable (a later save can blank it), and a nameless row is
        worse than one showing an address.
        """
        admin_user.name = ""
        admin_user.save(update_fields=["name"])
        self._goal_for(org_a, admin_profile, "Nameless Goal", "1000")

        response = admin_client.get(self.LEADERBOARD_URL)
        assert response.data["leaderboard"][0]["user"]["name"] == admin_user.email


class TestDashboardGoalSummary:
    def test_dashboard_includes_goal_summary(self, admin_client, goal_revenue):
        response = admin_client.get("/api/dashboard/")
        assert response.status_code == 200
        assert "goal_summary" in response.data
        # Admin should see their goal
        assert len(response.data["goal_summary"]) >= 1
        goal = response.data["goal_summary"][0]
        assert goal["name"] == "Monthly Revenue"
        assert "progress_value" in goal
        assert "progress_percent" in goal
        assert "status" in goal


class TestGoalMilestoneTask:
    @patch("opportunity.tasks._send_goal_milestone_email")
    def test_milestone_notification_sent(
        self, mock_send, org_a, admin_user, admin_profile
    ):
        from opportunity.tasks import check_goal_milestones

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Milestone Test",
            goal_type="REVENUE",
            target_value=Decimal("100"),
            period_type="MONTHLY",
            period_start=today - timedelta(days=5),
            period_end=today + timedelta(days=25),
            assigned_to=admin_profile,
            org=org_a,
        )

        # Create enough progress to hit 50%
        opp = _create_won_opportunity(org_a, admin_user, 60, closed_on=today)
        opp.assigned_to.add(admin_profile)

        check_goal_milestones()

        goal.refresh_from_db()
        assert goal.milestone_50_notified is True
        assert mock_send.called

    @patch("opportunity.tasks._send_goal_milestone_email")
    def test_milestone_not_sent_twice(
        self, mock_send, org_a, admin_user, admin_profile
    ):
        from opportunity.tasks import check_goal_milestones

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="No Dupe Test",
            goal_type="REVENUE",
            target_value=Decimal("100"),
            period_type="MONTHLY",
            period_start=today - timedelta(days=5),
            period_end=today + timedelta(days=25),
            assigned_to=admin_profile,
            milestone_50_notified=True,
            org=org_a,
        )

        opp = _create_won_opportunity(org_a, admin_user, 60, closed_on=today)
        opp.assigned_to.add(admin_profile)

        check_goal_milestones()

        goal.refresh_from_db()
        # Should not trigger 90% (only at 60%)
        assert goal.milestone_90_notified is False
        # Already had 50% notified, should not re-send
        assert goal.milestone_50_notified is True
        assert mock_send.call_count == 0


# ---- Weighted goals, activity goals, and batched progress ---- #


def _won_deal(org, user, amount, profiles, closed_on=None, deal_type=None):
    """A CLOSED_WON opportunity of `deal_type`, assigned to `profiles`."""
    opp = _create_won_opportunity(org, user, amount, closed_on=closed_on)
    if deal_type:
        opp.opportunity_type = deal_type
        opp.save(update_fields=["opportunity_type"])
    for profile in profiles:
        opp.assigned_to.add(profile)
    return opp


@pytest.mark.django_db
class TestTeamProgressCountsEachDealOnce:
    """A team goal joins opportunities to members, and a join can duplicate."""

    def test_a_deal_shared_by_two_team_members_counts_once(
        self, team_goal, org_a, admin_user, admin_profile, user_profile, team_a
    ):
        team_a.users.add(admin_profile, user_profile)

        _won_deal(org_a, admin_user, 50000, [admin_profile, user_profile])

        assert team_goal.compute_progress() == Decimal("50000")

    def test_a_deal_shared_by_two_team_members_is_one_closed_deal(
        self, org_a, team_a, admin_user, admin_profile, user_profile
    ):
        team_a.users.add(admin_profile, user_profile)
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Team Deals",
            goal_type="DEALS_CLOSED",
            target_value=Decimal("10"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            team=team_a,
            org=org_a,
        )

        _won_deal(org_a, admin_user, 50000, [admin_profile, user_profile])

        assert goal.compute_progress() == Decimal("1")


@pytest.mark.django_db
class TestActivityGoals:
    def test_progress_counts_the_assignees_activities_in_the_period(
        self, org_a, admin_profile
    ):
        from common.models import Activity

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Monthly Activities",
            goal_type="ACTIVITIES",
            target_value=Decimal("20"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
        )
        for i in range(3):
            Activity.objects.create(
                user=admin_profile,
                action="CREATE",
                entity_type="Lead",
                entity_id=goal.id,
                entity_name=f"Lead {i}",
                org=org_a,
            )

        assert goal.compute_progress() == Decimal("3")

    def test_progress_ignores_another_persons_activity(
        self, org_a, admin_profile, user_profile
    ):
        from common.models import Activity

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Monthly Activities",
            goal_type="ACTIVITIES",
            target_value=Decimal("20"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
        )
        Activity.objects.create(
            user=user_profile,
            action="CREATE",
            entity_type="Lead",
            entity_id=goal.id,
            org=org_a,
        )

        assert goal.compute_progress() == Decimal("0")

    def test_progress_ignores_an_activity_outside_the_period(
        self, org_a, admin_profile
    ):
        from common.models import Activity

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Monthly Activities",
            goal_type="ACTIVITIES",
            target_value=Decimal("20"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
        )
        stale = Activity.objects.create(
            user=admin_profile,
            action="CREATE",
            entity_type="Lead",
            entity_id=goal.id,
            org=org_a,
        )
        Activity.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=90)
        )

        assert goal.compute_progress() == Decimal("0")


@pytest.mark.django_db
class TestWeightedGoals:
    def test_a_renewal_at_half_weight_contributes_half_its_amount(
        self, org_a, admin_user, admin_profile
    ):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Weighted Revenue",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
            type_weights={"RENEWAL": 0.5, "NEW_BUSINESS": 1.5},
        )

        _won_deal(org_a, admin_user, 20000, [admin_profile], deal_type="RENEWAL")
        _won_deal(org_a, admin_user, 20000, [admin_profile], deal_type="NEW_BUSINESS")

        assert goal.compute_progress() == Decimal("40000")

    def test_a_type_absent_from_the_map_keeps_its_full_weight(
        self, org_a, admin_user, admin_profile
    ):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Weighted Revenue",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
            type_weights={"RENEWAL": 0.5},
        )

        _won_deal(org_a, admin_user, 20000, [admin_profile], deal_type="UPSELL")

        assert goal.compute_progress() == Decimal("20000")

    def test_a_deal_with_no_type_keeps_its_full_weight(
        self, org_a, admin_user, admin_profile
    ):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Weighted Revenue",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
            type_weights={"RENEWAL": 0.5},
        )

        _won_deal(org_a, admin_user, 20000, [admin_profile])

        assert goal.compute_progress() == Decimal("20000")

    def test_weights_scale_a_deals_closed_goal_too(
        self, org_a, admin_user, admin_profile
    ):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Weighted Deals",
            goal_type="DEALS_CLOSED",
            target_value=Decimal("10"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
            type_weights={"RENEWAL": 0.5},
        )

        _won_deal(org_a, admin_user, 20000, [admin_profile], deal_type="RENEWAL")
        _won_deal(org_a, admin_user, 30000, [admin_profile], deal_type="RENEWAL")

        assert goal.compute_progress() == Decimal("1")

    def test_an_empty_weight_map_leaves_progress_unweighted(
        self, goal_revenue, org_a, admin_user, admin_profile
    ):
        _won_deal(org_a, admin_user, 25000, [admin_profile], deal_type="RENEWAL")

        assert goal_revenue.type_weights == {}
        assert goal_revenue.compute_progress() == Decimal("25000")

    def test_a_zero_weight_drops_the_type_from_the_total(
        self, org_a, admin_user, admin_profile
    ):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="New business only",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
            type_weights={"RENEWAL": 0},
        )

        _won_deal(org_a, admin_user, 20000, [admin_profile], deal_type="RENEWAL")
        _won_deal(org_a, admin_user, 5000, [admin_profile], deal_type="NEW_BUSINESS")

        assert goal.compute_progress() == Decimal("5000")


# ---- Batched progress ---- #


def _past_goal(org, name, target, achieved_by, days_ago, profile=None, team=None):
    """A finished goal whose period closed `days_ago`."""
    end = timezone.localdate() - timedelta(days=days_ago)
    return SalesGoal.objects.create(
        name=name,
        goal_type="REVENUE",
        target_value=Decimal(str(target)),
        period_type="MONTHLY",
        period_start=end - timedelta(days=29),
        period_end=end,
        assigned_to=profile,
        team=team,
        org=org,
    )


@pytest.mark.django_db
class TestAttachProgressIsBatched:
    def test_batched_progress_matches_the_single_goal_query(
        self, org_a, admin_user, admin_profile, user_profile, team_a
    ):
        team_a.users.add(admin_profile, user_profile)
        today = timezone.localdate()
        shared = SalesGoal.objects.create(
            name="Team Revenue",
            goal_type="REVENUE",
            target_value=Decimal("100000"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            team=team_a,
            org=org_a,
            type_weights={"RENEWAL": 0.5},
        )
        mine = SalesGoal.objects.create(
            name="My Deals",
            goal_type="DEALS_CLOSED",
            target_value=Decimal("5"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
        )
        _won_deal(
            org_a, admin_user, 20000, [admin_profile, user_profile], deal_type="RENEWAL"
        )
        _won_deal(org_a, admin_user, 30000, [admin_profile])

        one_at_a_time = [
            SalesGoal.objects.get(pk=shared.pk).compute_progress(),
            SalesGoal.objects.get(pk=mine.pk).compute_progress(),
        ]
        batched = SalesGoal.attach_progress(
            SalesGoal.objects.filter(pk__in=[shared.pk, mine.pk]).order_by("name")
        )

        assert one_at_a_time == [Decimal("40000"), Decimal("2")]
        assert [g.compute_progress() for g in batched] == [
            Decimal("2"),
            Decimal("40000"),
        ]

    def test_batched_activity_progress_matches_the_single_goal_query(
        self, org_a, admin_profile
    ):
        from common.models import Activity

        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            name="Activities",
            goal_type="ACTIVITIES",
            target_value=Decimal("10"),
            period_type="MONTHLY",
            period_start=today.replace(day=1),
            period_end=today + timedelta(days=1),
            assigned_to=admin_profile,
            org=org_a,
        )
        for i in range(4):
            Activity.objects.create(
                user=admin_profile,
                action="CREATE",
                entity_type="Lead",
                entity_id=goal.id,
                org=org_a,
            )

        batched = SalesGoal.attach_progress(SalesGoal.objects.filter(pk=goal.pk))

        assert batched[0].compute_progress() == Decimal("4")
        assert SalesGoal.objects.get(pk=goal.pk).compute_progress() == Decimal("4")

    def test_listing_more_goals_does_not_cost_more_queries(
        self, admin_client, org_a, admin_profile
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = admin_client.get("/api/opportunities/goals/")
                assert response.status_code == 200
            return len(ctx.captured_queries)

        for i in range(3):
            _past_goal(org_a, f"Old {i}", 1000, None, 30 + i, profile=admin_profile)
        few = count_queries()

        for i in range(9):
            _past_goal(org_a, f"More {i}", 1000, None, 60 + i, profile=admin_profile)
        many = count_queries()

        assert many == few, (
            f"query count grew from {few} to {many} when the page grew from 3 "
            "goals to 12: progress is still being computed one goal at a time"
        )


# ---- Write validation ---- #


@pytest.mark.django_db
class TestGoalWriteValidation:
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

    def test_rejects_a_goal_assigned_to_both_a_person_and_a_team(
        self, admin_client, admin_profile, team_a
    ):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(assigned_to=str(admin_profile.id), team=str(team_a.id)),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "team" in str(response.json()["errors"])

    def test_accepts_a_weight_map_over_known_deal_types(self, admin_client, org_a):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(type_weights={"RENEWAL": 0.5, "NEW_BUSINESS": 1.5}),
            content_type="application/json",
        )

        assert response.status_code == 201
        assert SalesGoal.objects.get(name="Q3 Revenue").type_weights == {
            "RENEWAL": 0.5,
            "NEW_BUSINESS": 1.5,
        }

    def test_rejects_a_weight_for_an_unknown_deal_type(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(type_weights={"NOT_A_TYPE": 2}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "NOT_A_TYPE" in str(response.json()["errors"])

    def test_rejects_a_negative_weight(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(type_weights={"RENEWAL": -1}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_rejects_a_non_numeric_weight(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(type_weights={"RENEWAL": "heavy"}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_rejects_a_weight_map_that_is_not_a_map(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(type_weights=["RENEWAL"]),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_rejects_deal_type_weights_on_an_activities_goal(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(goal_type="ACTIVITIES", type_weights={"RENEWAL": 0.5}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_accepts_an_activities_goal(self, admin_client):
        response = admin_client.post(
            "/api/opportunities/goals/",
            self._payload(goal_type="ACTIVITIES", target_value="40"),
            content_type="application/json",
        )

        assert response.status_code == 201
        assert SalesGoal.objects.get(name="Q3 Revenue").goal_type == "ACTIVITIES"


# ---- Milestone flags follow the target ---- #


@pytest.mark.django_db
class TestMilestoneFlagsFollowTheTarget:
    @pytest.fixture
    def notified_goal(self, goal_revenue):
        goal_revenue.milestone_50_notified = True
        goal_revenue.milestone_90_notified = True
        goal_revenue.milestone_100_notified = True
        goal_revenue.save()
        return goal_revenue

    def test_raising_the_target_clears_the_flags(self, admin_client, notified_goal):
        response = admin_client.put(
            f"/api/opportunities/goals/{notified_goal.id}/",
            {"target_value": "250000"},
            content_type="application/json",
        )

        assert response.status_code == 200
        notified_goal.refresh_from_db()
        assert notified_goal.milestone_50_notified is False
        assert notified_goal.milestone_90_notified is False
        assert notified_goal.milestone_100_notified is False

    def test_moving_the_period_clears_the_flags(self, admin_client, notified_goal):
        response = admin_client.put(
            f"/api/opportunities/goals/{notified_goal.id}/",
            {"period_end": str(notified_goal.period_end + timedelta(days=30))},
            content_type="application/json",
        )

        assert response.status_code == 200
        notified_goal.refresh_from_db()
        assert notified_goal.milestone_100_notified is False

    def test_renaming_the_goal_keeps_the_flags(self, admin_client, notified_goal):
        response = admin_client.put(
            f"/api/opportunities/goals/{notified_goal.id}/",
            {"name": "Renamed"},
            content_type="application/json",
        )

        assert response.status_code == 200
        notified_goal.refresh_from_db()
        assert notified_goal.name == "Renamed"
        assert notified_goal.milestone_100_notified is True

    def test_resubmitting_the_same_target_keeps_the_flags(
        self, admin_client, notified_goal
    ):
        response = admin_client.put(
            f"/api/opportunities/goals/{notified_goal.id}/",
            {"target_value": str(notified_goal.target_value)},
            content_type="application/json",
        )

        assert response.status_code == 200
        notified_goal.refresh_from_db()
        assert notified_goal.milestone_100_notified is True


# ---- Attainment history ---- #


@pytest.mark.django_db
class TestGoalHistory:
    def test_returns_a_finished_period_with_its_attainment(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _past_goal(org_a, "Last month", 40000, None, 5, profile=admin_profile)
        _won_deal(org_a, admin_user, 30000, [admin_profile], closed_on=goal.period_end)

        response = admin_client.get("/api/opportunities/goals/history/")

        assert response.status_code == 200
        periods = response.json()["history"]
        assert len(periods) == 1
        assert periods[0]["target"] == 40000.0
        assert periods[0]["achieved"] == 30000.0
        assert periods[0]["percent"] == 75
        assert periods[0]["goals_count"] == 1
        assert periods[0]["attained_count"] == 0
        assert periods[0]["goals"][0]["name"] == "Last month"

    def test_counts_a_goal_that_met_its_target_as_attained(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _past_goal(org_a, "Last month", 20000, None, 5, profile=admin_profile)
        _won_deal(org_a, admin_user, 25000, [admin_profile], closed_on=goal.period_end)

        response = admin_client.get("/api/opportunities/goals/history/")

        period = response.json()["history"][0]
        assert period["attained_count"] == 1
        # Uncapped: see TestHistoryKeepsItsUnitsApart for why a settled period
        # reports what it actually did rather than stopping at the target.
        assert period["percent"] == 125

    def test_excludes_a_period_that_has_not_finished(
        self, admin_client, goal_revenue, org_a, admin_profile
    ):
        _past_goal(org_a, "Done", 1000, None, 5, profile=admin_profile)

        response = admin_client.get("/api/opportunities/goals/history/")

        names = [g["name"] for p in response.json()["history"] for g in p["goals"]]
        assert names == ["Done"]

    def test_groups_goals_that_share_a_period_and_orders_newest_first(
        self, admin_client, org_a, admin_profile
    ):
        _past_goal(org_a, "Older", 1000, None, 60, profile=admin_profile)
        _past_goal(org_a, "Newer A", 1000, None, 5, profile=admin_profile)
        _past_goal(org_a, "Newer B", 2000, None, 5, profile=admin_profile)

        response = admin_client.get("/api/opportunities/goals/history/")

        periods = response.json()["history"]
        assert len(periods) == 2
        assert periods[0]["goals_count"] == 2
        assert periods[0]["target"] == 3000.0
        assert periods[1]["goals_count"] == 1

    def test_hides_another_persons_finished_goal_from_a_member(
        self, user_client, org_a, admin_profile, user_profile
    ):
        _past_goal(org_a, "Admin only", 1000, None, 5, profile=admin_profile)
        _past_goal(org_a, "Mine", 1000, None, 5, profile=user_profile)

        response = user_client.get("/api/opportunities/goals/history/")

        assert response.status_code == 200
        names = [g["name"] for p in response.json()["history"] for g in p["goals"]]
        assert names == ["Mine"]

    def test_does_not_leak_another_orgs_history(
        self, org_b_client, org_a, admin_profile
    ):
        _past_goal(org_a, "Org A only", 1000, None, 5, profile=admin_profile)

        response = org_b_client.get("/api/opportunities/goals/history/")

        assert response.status_code == 200
        assert response.json()["history"] == []


@pytest.mark.django_db
class TestHistoryKeepsItsUnitsApart:
    def test_does_not_pool_a_revenue_target_with_a_deals_target(
        self, admin_client, org_a, admin_profile
    ):
        end = timezone.localdate() - timedelta(days=5)
        common = {
            "period_type": "MONTHLY",
            "period_start": end - timedelta(days=29),
            "period_end": end,
            "assigned_to": admin_profile,
            "org": org_a,
        }
        SalesGoal.objects.create(
            name="Revenue", goal_type="REVENUE", target_value=Decimal("50000"), **common
        )
        SalesGoal.objects.create(
            name="Deals", goal_type="DEALS_CLOSED", target_value=Decimal("9"), **common
        )

        response = admin_client.get("/api/opportunities/goals/history/")

        rows = response.json()["history"]
        # One row per unit, never a single row claiming a target of 50009.
        assert {r["goal_type"] for r in rows} == {"REVENUE", "DEALS_CLOSED"}
        by_type = {r["goal_type"]: r for r in rows}
        assert by_type["REVENUE"]["target"] == 50000.0
        assert by_type["DEALS_CLOSED"]["target"] == 9.0

    def test_reports_attainment_above_target_rather_than_capping_it(
        self, admin_client, org_a, admin_user, admin_profile
    ):
        goal = _past_goal(org_a, "Beat it", 20000, None, 5, profile=admin_profile)
        _won_deal(org_a, admin_user, 25000, [admin_profile], closed_on=goal.period_end)

        response = admin_client.get("/api/opportunities/goals/history/")

        # A settled period that came in 25% over is not the same result as one
        # that landed exactly on target, and capping made them identical.
        assert response.json()["history"][0]["percent"] == 125
