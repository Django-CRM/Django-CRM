"""Tests for deal aging / rotten deals feature."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from opportunity.models import DealStage, Opportunity


@pytest.fixture
def opportunity(org_a, admin_user):
    """Create a basic opportunity for testing."""
    return Opportunity.objects.create(
        name="Test Deal",
        stage="PROSPECTING",
        org=org_a,
        created_by=admin_user,
    )


class TestStageChangedAtTracking:
    """Test that stage_changed_at is set correctly on create and stage changes."""

    def test_stage_changed_at_set_on_create(self, org_a, admin_user):
        before = timezone.now()
        opp = Opportunity.objects.create(
            name="New Deal",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
        )
        after = timezone.now()
        assert opp.stage_changed_at is not None
        assert before <= opp.stage_changed_at <= after

    def test_stage_changed_at_updates_on_stage_change(self, opportunity):
        # Simulate time passing
        opportunity.stage_changed_at = timezone.now() - timedelta(days=5)
        opportunity.save(update_fields=["stage_changed_at"])
        old_time = opportunity.stage_changed_at

        # Now change the stage
        opportunity.stage = "QUALIFICATION"
        opportunity.save()

        opportunity.refresh_from_db()
        assert opportunity.stage_changed_at > old_time

    def test_stage_changed_at_unchanged_on_non_stage_update(self, opportunity):
        # Set a known time
        known_time = timezone.now() - timedelta(days=3)
        opportunity.stage_changed_at = known_time
        opportunity.save(update_fields=["stage_changed_at"])

        # Update a non-stage field
        opportunity.name = "Updated Deal Name"
        opportunity.save()

        opportunity.refresh_from_db()
        # Should be unchanged (within a second tolerance)
        assert abs((opportunity.stage_changed_at - known_time).total_seconds()) < 1


class TestAgingStatus:
    """Test aging_status property returns correct green/yellow/red values."""

    def test_green_when_within_expected_days(self, opportunity):
        opportunity.stage_changed_at = timezone.now() - timedelta(days=5)
        opportunity.save(update_fields=["stage_changed_at"])
        assert opportunity.aging_status == "green"

    def test_yellow_when_past_expected_days(self, opportunity):
        # PROSPECTING default is 14 days, rotten at 14 * 1.5 = 21
        opportunity.stage_changed_at = timezone.now() - timedelta(days=15)
        opportunity.save(update_fields=["stage_changed_at"])
        assert opportunity.aging_status == "yellow"

    def test_red_when_past_rotten_threshold(self, opportunity):
        # PROSPECTING: rotten threshold = 14 * 1.5 = 21 days
        opportunity.stage_changed_at = timezone.now() - timedelta(days=22)
        opportunity.save(update_fields=["stage_changed_at"])
        assert opportunity.aging_status == "red"

    def test_closed_stages_always_green(self, opportunity):
        opportunity.stage = "CLOSED_WON"
        opportunity.closed_on = timezone.localdate()
        opportunity.amount = 1000
        opportunity.stage_changed_at = timezone.now() - timedelta(days=100)
        opportunity.save()
        assert opportunity.aging_status == "green"

    def test_closed_lost_always_green(self, opportunity):
        opportunity.stage = "CLOSED_LOST"
        opportunity.closed_on = timezone.localdate()
        opportunity.stage_changed_at = timezone.now() - timedelta(days=100)
        opportunity.save()
        assert opportunity.aging_status == "green"


def _configure(org, code, **days):
    DealStage.objects.filter(org=org, code=code).update(**days)


class TestPerOrgConfig:
    """Test that the stage's own rotting days override the seeded defaults."""

    def test_custom_expected_days(self, org_a, opportunity):
        # Set custom config: 5 days expected for PROSPECTING
        _configure(org_a, "PROSPECTING", expected_days=5)
        opportunity.stage_changed_at = timezone.now() - timedelta(days=6)
        opportunity.save(update_fields=["stage_changed_at"])

        # 6 days > 5 expected → yellow
        assert opportunity.aging_status == "yellow"

    def test_custom_warning_days(self, org_a, opportunity):
        # Set custom config with explicit warning days
        _configure(org_a, "PROSPECTING", expected_days=14, warning_days=10)
        opportunity.stage_changed_at = timezone.now() - timedelta(days=11)
        opportunity.save(update_fields=["stage_changed_at"])

        # 11 days > 10 warning → yellow
        assert opportunity.aging_status == "yellow"

    def test_custom_config_red_threshold(self, org_a, opportunity):
        # Set custom config: 5 days expected → rotten at 5 * 1.5 = 7.5
        _configure(org_a, "PROSPECTING", expected_days=5)
        opportunity.stage_changed_at = timezone.now() - timedelta(days=8)
        opportunity.save(update_fields=["stage_changed_at"])

        # 8 days >= 7.5 → red
        assert opportunity.aging_status == "red"

    def test_stage_without_expected_days_never_ages(self, org_a, opportunity):
        _configure(org_a, "PROSPECTING", expected_days=None)
        opportunity.stage_changed_at = timezone.now() - timedelta(days=400)
        opportunity.save(update_fields=["stage_changed_at"])
        assert opportunity.aging_status == "green"

    def test_stage_code_unique_per_pipeline(self, org_a, opportunity):
        stage = DealStage.objects.get(org=org_a, code="PROSPECTING")
        with pytest.raises(Exception):
            DealStage.objects.create(
                org=org_a, pipeline=stage.pipeline, code="PROSPECTING", label="Dup"
            )


class TestDaysInCurrentStage:
    """Test the days_in_current_stage property."""

    def test_returns_zero_when_no_stage_changed_at(self, org_a, admin_user):
        opp = Opportunity(
            name="No Date Deal",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
            stage_changed_at=None,
        )
        assert opp.days_in_current_stage == 0

    def test_returns_correct_days(self, opportunity):
        opportunity.stage_changed_at = timezone.now() - timedelta(days=7)
        opportunity.save(update_fields=["stage_changed_at"])
        assert opportunity.days_in_current_stage == 7


class TestRottenFilterAPI:
    """Test the ?rotten=true API filter."""

    def test_rotten_filter_returns_stale_deals(self, admin_client, org_a, admin_user):
        # Create a rotten deal (PROSPECTING > 21 days)
        Opportunity.objects.create(
            name="Rotten Deal",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
            stage_changed_at=timezone.now() - timedelta(days=25),
        )
        # Create a fresh deal
        Opportunity.objects.create(
            name="Fresh Deal",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
            stage_changed_at=timezone.now() - timedelta(days=2),
        )

        response = admin_client.get("/api/opportunities/?rotten=true")
        assert response.status_code == 200
        opp_names = [o["name"] for o in response.data["opportunities"]]
        assert "Rotten Deal" in opp_names
        assert "Fresh Deal" not in opp_names

    def test_rotten_filter_excludes_closed_deals(self, admin_client, org_a, admin_user):
        # Closed deal should never appear in rotten filter
        Opportunity.objects.create(
            name="Old Won Deal",
            stage="CLOSED_WON",
            org=org_a,
            created_by=admin_user,
            closed_on=timezone.localdate(),
            amount=1000,
            stage_changed_at=timezone.now() - timedelta(days=100),
        )

        response = admin_client.get("/api/opportunities/?rotten=true")
        assert response.status_code == 200
        opp_names = [o["name"] for o in response.data["opportunities"]]
        assert "Old Won Deal" not in opp_names


class TestAgingConfigAPI:
    """Test the aging config CRUD endpoints."""

    def test_get_aging_config_returns_defaults(self, admin_client):
        response = admin_client.get("/api/opportunities/aging-config/")
        assert response.status_code == 200
        stages = [item["stage"] for item in response.data]
        assert "PROSPECTING" in stages
        assert "QUALIFICATION" in stages
        assert "CLOSED_WON" not in stages
        assert "CLOSED_LOST" not in stages

    def test_put_aging_config_updates(self, admin_client, org_a):
        data = [
            {"stage": "PROSPECTING", "expected_days": 7, "warning_days": 5},
            {"stage": "QUALIFICATION", "expected_days": 10},
        ]
        response = admin_client.put(
            "/api/opportunities/aging-config/",
            data=data,
            format="json",
        )
        assert response.status_code == 200
        assert response.data["error"] is False

        # Persisted on the default pipeline's stage, the one store of it.
        stage = DealStage.objects.get(
            org=org_a, pipeline__is_default=True, code="PROSPECTING"
        )
        assert stage.expected_days == 7
        assert stage.warning_days == 5
        assert response.data["configs"][0]["id"] == str(stage.id)

    def test_put_aging_config_non_admin_forbidden(self, user_client):
        data = [{"stage": "PROSPECTING", "expected_days": 7}]
        response = user_client.put(
            "/api/opportunities/aging-config/",
            data=data,
            format="json",
        )
        assert response.status_code == 403


class TestStaleDealsTask:
    """Test the Celery task for detecting stale deals."""

    @patch("opportunity.tasks.send_stale_deals_alert")
    def test_task_detects_stale_deals(
        self, mock_alert, org_a, admin_user, admin_profile
    ):
        # Create a rotten deal assigned to admin
        rotten = Opportunity.objects.create(
            name="Very Stale",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
            stage_changed_at=timezone.now() - timedelta(days=30),
        )
        rotten.assigned_to.add(admin_profile)

        from opportunity.tasks import check_stale_opportunities

        check_stale_opportunities()

        assert mock_alert.called
        call_args = mock_alert.call_args
        org_arg = call_args[0][0]
        stale_list = call_args[0][1]
        assert org_arg == org_a
        assert len(stale_list) >= 1
        stale = {
            opp.name: (label, days, expected)
            for opp, label, days, expected in stale_list
        }
        assert stale["Very Stale"] == ("Prospecting", 30, 14)

    @patch("opportunity.tasks.send_stale_deals_alert")
    def test_task_ignores_fresh_deals(self, mock_alert, org_a, admin_user):
        Opportunity.objects.create(
            name="Fresh Deal",
            stage="PROSPECTING",
            org=org_a,
            created_by=admin_user,
            stage_changed_at=timezone.now() - timedelta(days=2),
        )

        from opportunity.tasks import check_stale_opportunities

        check_stale_opportunities()

        # Alert should not be called since no deals are stale
        mock_alert.assert_not_called()
