"""Per-org SLA targets: resolution, re-derivation on priority change, and the
policy API that exposes them.

Before this, every org on the platform was locked to the hardcoded numbers in
`cases/workflow.py` because `sla_first_response_hours` / `sla_resolution_hours`
were absent from `CaseCreateSerializer.Meta.fields`, so no client could write
them. Targets now hang off the `EscalationPolicy` row that already exists per
(org, priority), and the workflow defaults are the fallback for an org that has
not configured one.

Resolution lives in `cases.signals.case_pre_save_sla_targets` and
`cases.workflow.resolve_sla_targets`. Note that `EscalationPolicy`'s own
docstring points at `docs/cases/tier1/escalation.md`, which does not exist in
this tree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from cases.models import Case, EscalationPolicy
from conftest import rls_org

POLICIES_URL = "/api/cases/escalation-policies/"


def _make_policy(org, **overrides):
    defaults = {
        "priority": "Urgent",
        "first_response_action": "notify",
        "resolution_action": "notify",
        "is_active": True,
    }
    defaults.update(overrides)
    # The insert-check policy compares against `app.current_org`, so writing
    # this row means being that tenant for the length of the write.
    with rls_org(org):
        return EscalationPolicy.objects.create(org=org, **defaults)


def _make_case(org, created_by, priority="Urgent", **overrides):
    fields = {
        "name": f"{priority} case",
        "status": "New",
        "priority": priority,
        "org": org,
        "created_by": created_by,
    }
    fields.update(overrides)
    return Case.objects.create(**fields)


@pytest.mark.django_db
class TestTargetResolutionOnCreate:
    def test_new_case_uses_org_policy_hours(self, admin_user, org_a):
        """A configured policy beats the workflow default for that priority."""
        _make_policy(
            org_a, priority="Urgent", first_response_hours=2, resolution_hours=8
        )

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 2
        assert case.sla_resolution_hours == 8

    def test_falls_back_to_workflow_defaults_without_policy(self, admin_user, org_a):
        """No policy row at all leaves the pre-feature behavior intact."""
        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_inactive_policy_ignored(self, admin_user, org_a):
        """`is_active=False` already means "do not escalate"; it now also means
        "do not use these targets", so one switch disables the whole row."""
        _make_policy(
            org_a,
            priority="Urgent",
            first_response_hours=2,
            resolution_hours=8,
            is_active=False,
        )

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_policy_without_hours_falls_back(self, admin_user, org_a):
        """Every existing policy row has NULL hours after the migration, so an
        org that only configured escalation actions must keep its old targets."""
        _make_policy(org_a, priority="Urgent")

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_half_configured_policy_falls_back_per_field(self, admin_user, org_a):
        """The two targets are independent; setting one must not blank the other."""
        _make_policy(org_a, priority="Urgent", first_response_hours=2)

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 2
        assert case.sla_resolution_hours == 4

    def test_policy_for_other_priority_ignored(self, admin_user, org_a):
        _make_policy(org_a, priority="Low", first_response_hours=2, resolution_hours=8)

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_policy_from_other_org_ignored(self, admin_user, org_a, org_b):
        _make_policy(
            org_b, priority="Urgent", first_response_hours=2, resolution_hours=8
        )

        case = _make_case(org_a, admin_user, priority="Urgent")

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_explicit_hours_equal_to_old_sentinel_are_preserved(
        self, admin_user, org_a
    ):
        """The bug this replaces: resolution used to detect "unset" by comparing
        against the field default (`== 4` / `== 24`), so a caller who genuinely
        wanted a 4-hour first response on an Urgent case had it silently
        rewritten to 1. Explicit values now survive because unset is NULL.
        """
        case = _make_case(
            org_a,
            admin_user,
            priority="Urgent",
            sla_first_response_hours=4,
            sla_resolution_hours=24,
        )

        assert case.sla_first_response_hours == 4
        assert case.sla_resolution_hours == 24


@pytest.mark.django_db
class TestRederivationOnPriorityChange:
    def test_priority_change_rederives_targets(self, admin_user, org_a):
        """Raising priority is how a lead says "this needs attention sooner".
        Freezing the target at insert made that a no-op for the deadline.
        """
        case = _make_case(org_a, admin_user, priority="Low")
        assert case.sla_first_response_hours == 24

        case.priority = "Urgent"
        case.save()
        case.refresh_from_db()

        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_priority_change_respects_org_policy(self, admin_user, org_a):
        _make_policy(
            org_a, priority="Urgent", first_response_hours=2, resolution_hours=8
        )
        case = _make_case(org_a, admin_user, priority="Low")

        case.priority = "Urgent"
        case.save()
        case.refresh_from_db()

        assert case.sla_first_response_hours == 2
        assert case.sla_resolution_hours == 8

    def test_non_priority_update_leaves_targets_alone(self, admin_user, org_a):
        """A rename must not quietly reset a target, including one that was set
        by hand and does not match what the policy would resolve to."""
        case = _make_case(
            org_a, admin_user, priority="Urgent", sla_first_response_hours=9
        )

        case.name = "Renamed"
        case.save()
        case.refresh_from_db()

        assert case.sla_first_response_hours == 9

    def test_lowering_priority_relaxes_targets(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")

        case.priority = "Low"
        case.save()
        case.refresh_from_db()

        assert case.sla_first_response_hours == 24
        assert case.sla_resolution_hours == 72


def _age_case(case, hours):
    """Backdate `created_at`, which is what every SLA deadline walks from."""
    Case.objects.filter(pk=case.pk).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )
    case.refresh_from_db()
    return case


@pytest.mark.django_db
class TestAtRisk:
    """The at-risk band, the missing middle of the issue's green/yellow/red.

    Breached-only means the UI tells an agent about an SLA exactly when it is
    too late to act on it.
    """

    def test_not_at_risk_early_in_the_window(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")  # 24h first response

        assert case.is_sla_first_response_at_risk is False

    def test_at_risk_once_past_the_threshold(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 20)  # 4h of a 24h target left, under the 25% band

        assert case.is_sla_first_response_at_risk is True
        assert case.is_sla_first_response_breached is False

    def test_not_at_risk_once_breached(self, admin_user, org_a):
        """Breached and at-risk are exclusive; a case that is both would light
        up two indicators at once."""
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 30)

        assert case.is_sla_first_response_breached is True
        assert case.is_sla_first_response_at_risk is False

    def test_not_at_risk_once_first_response_recorded(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 20)
        case.first_response_at = timezone.now()
        case.save()

        assert case.is_sla_first_response_at_risk is False

    def test_resolution_at_risk_tracks_its_own_target(self, admin_user, org_a):
        """72h resolution on Low, so 20h in is at risk for first response but
        nowhere near the resolution band."""
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 20)

        assert case.is_sla_first_response_at_risk is True
        assert case.is_sla_resolution_at_risk is False

    def test_resolution_at_risk_near_its_deadline(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 60)  # 12h of a 72h target left

        assert case.is_sla_resolution_at_risk is True
        assert case.is_sla_resolution_breached is False

    def test_not_at_risk_once_resolved(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 60)
        case.resolved_at = timezone.now()
        case.save()

        assert case.is_sla_resolution_at_risk is False

    def test_tighter_org_target_moves_the_band(self, admin_user, org_a):
        """The band is a fraction of the configured target, not a fixed number
        of hours, so shortening the promise moves it."""
        _make_policy(org_a, priority="Low", first_response_hours=4)
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 3.5)

        assert case.sla_first_response_hours == 4
        assert case.is_sla_first_response_at_risk is True

    def test_at_risk_exposed_on_kanban_cards(self, admin_client, admin_user, org_a):
        """The board is where an agent picks the next case, so the amber band
        has to reach the card and not just the detail page."""
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 20)

        response = admin_client.get("/api/cases/kanban/")

        assert response.status_code == 200
        card = next(
            c
            for column in response.data["columns"]
            for c in column["cases"]
            if c["id"] == str(case.id)
        )
        assert card["is_sla_at_risk"] is True
        assert card["is_sla_breached"] is False

    def test_at_risk_exposed_on_the_api(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Low")
        _age_case(case, 20)

        response = admin_client.get(f"/api/cases/{case.id}/")

        assert response.status_code == 200
        assert response.data["cases_obj"]["is_sla_first_response_at_risk"] is True
        assert response.data["cases_obj"]["is_sla_resolution_at_risk"] is False


@pytest.fixture
def weekday_calendar(org_a):
    """Mon-Fri 9-5 UTC, the shape `business_hours/tests/conftest.py` uses."""
    from datetime import time

    from business_hours.models import BusinessCalendar

    with rls_org(org_a):
        return BusinessCalendar.objects.create(
            org=org_a,
            name="Default",
            timezone="UTC",
            is_default=True,
            **{
                f"{day}_{edge}": time(9 if edge == "open" else 17, 0)
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
                for edge in ("open", "close")
            },
        )


@pytest.mark.django_db
class TestAtRiskAcrossClosedTime:
    """The band has to be measured in business time, not wall-clock time.

    Found in the browser rather than in a unit test: against the seeded Mon-Fri
    calendar the amber state was unreachable on a Sunday, and a ticket opened
    late on Friday went amber over the weekend having consumed ten minutes of
    its four-hour target. Both come from the same mistake, comparing the time
    left against `deadline - created_at`, a span that counts two closed days as
    if an agent could have worked them.
    """

    def _friday_case(self, org, created_by):
        case = _make_case(org, created_by, priority="High")  # 4h first response
        # Friday 16:50 UTC, ten minutes before close.
        Case.objects.filter(pk=case.pk).update(
            created_at=datetime(2026, 8, 14, 16, 50, tzinfo=UTC)
        )
        case.refresh_from_db()
        return case

    def test_not_at_risk_over_the_weekend_it_could_not_work(
        self, admin_user, org_a, weekday_calendar
    ):
        case = self._friday_case(org_a, admin_user)

        # Sunday evening. Ten minutes of the four-hour target has been used, so
        # nearly all of it is still there on Monday morning.
        with patch(
            "cases.models.timezone.now",
            return_value=datetime(2026, 8, 16, 20, 0, tzinfo=UTC),
        ):
            assert case.is_sla_first_response_breached is False
            assert case.is_sla_first_response_at_risk is False

    def test_at_risk_once_the_business_time_is_nearly_gone(
        self, admin_user, org_a, weekday_calendar
    ):
        case = self._friday_case(org_a, admin_user)

        # Monday noon: ten minutes on Friday plus three hours today, so fifty
        # minutes of the four hours remain.
        with patch(
            "cases.models.timezone.now",
            return_value=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        ):
            assert case.is_sla_first_response_at_risk is True
            assert case.is_sla_first_response_breached is False

    def test_not_at_risk_at_the_start_of_the_working_day(
        self, admin_user, org_a, weekday_calendar
    ):
        case = self._friday_case(org_a, admin_user)

        # Monday 09:30, half an hour in, with three hours twenty still to go.
        with patch(
            "cases.models.timezone.now",
            return_value=datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        ):
            assert case.is_sla_first_response_at_risk is False

    def test_breached_after_the_deadline_passes(
        self, admin_user, org_a, weekday_calendar
    ):
        case = self._friday_case(org_a, admin_user)

        # Monday 13:00, past the 12:50 deadline.
        with patch(
            "cases.models.timezone.now",
            return_value=datetime(2026, 8, 17, 13, 0, tzinfo=UTC),
        ):
            assert case.is_sla_first_response_breached is True
            assert case.is_sla_first_response_at_risk is False


@pytest.mark.django_db
class TestPolicyTargetsAPI:
    def test_admin_can_set_target_hours(self, admin_client, org_a):
        response = admin_client.post(
            POLICIES_URL,
            {
                "priority": "High",
                "first_response_action": "notify",
                "resolution_action": "notify",
                "first_response_hours": 3,
                "resolution_hours": 12,
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        policy = EscalationPolicy.objects.get(org=org_a, priority="High")
        assert policy.first_response_hours == 3
        assert policy.resolution_hours == 12

    def test_hours_are_optional(self, admin_client, org_a):
        response = admin_client.post(
            POLICIES_URL,
            {
                "priority": "High",
                "first_response_action": "notify",
                "resolution_action": "notify",
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        policy = EscalationPolicy.objects.get(org=org_a, priority="High")
        assert policy.first_response_hours is None
        assert policy.resolution_hours is None

    @pytest.mark.parametrize("field", ["first_response_hours", "resolution_hours"])
    def test_zero_hours_rejected(self, admin_client, org_a, field):
        """A zero-hour target puts the deadline on `created_at`, so every case
        opens already breached and the 5-minute scan escalates the queue."""
        response = admin_client.post(
            POLICIES_URL,
            {
                "priority": "High",
                "first_response_action": "notify",
                "resolution_action": "notify",
                field: 0,
            },
            format="json",
        )

        assert response.status_code == 400
        assert field in response.data["errors"]

    @pytest.mark.parametrize("field", ["first_response_hours", "resolution_hours"])
    def test_absurd_hours_rejected(self, admin_client, org_a, field):
        """The business-hours walker gives up after 5 years of calendar days,
        so a target it can never reach would silently return a junk deadline."""
        response = admin_client.post(
            POLICIES_URL,
            {
                "priority": "High",
                "first_response_action": "notify",
                "resolution_action": "notify",
                field: 100000,
            },
            format="json",
        )

        assert response.status_code == 400
        assert field in response.data["errors"]

    def test_hours_returned_on_list(self, admin_client, org_a):
        _make_policy(
            org_a, priority="Urgent", first_response_hours=2, resolution_hours=8
        )

        response = admin_client.get(POLICIES_URL)

        assert response.status_code == 200
        row = next(p for p in response.data["policies"] if p["priority"] == "Urgent")
        assert row["first_response_hours"] == 2
        assert row["resolution_hours"] == 8

    def test_admin_can_update_hours(self, admin_client, org_a):
        policy = _make_policy(org_a, priority="Urgent", first_response_hours=2)

        response = admin_client.put(
            f"{POLICIES_URL}{policy.id}/",
            {"first_response_hours": 6, "resolution_hours": 18},
            format="json",
        )

        assert response.status_code == 200, response.data
        policy.refresh_from_db()
        assert policy.first_response_hours == 6
        assert policy.resolution_hours == 18

    def test_clearing_hours_restores_the_default(self, admin_client, admin_user, org_a):
        """Blanking a target is the documented way back to the built-in
        promise, so it has to reach new cases and not just the policy row."""
        policy = _make_policy(
            org_a, priority="Urgent", first_response_hours=2, resolution_hours=8
        )

        response = admin_client.put(
            f"{POLICIES_URL}{policy.id}/",
            {"first_response_hours": None, "resolution_hours": None},
            format="json",
        )

        assert response.status_code == 200, response.data
        case = _make_case(org_a, admin_user, priority="Urgent")
        assert case.sla_first_response_hours == 1
        assert case.sla_resolution_hours == 4

    def test_update_rejects_zero_hours(self, admin_client, org_a):
        policy = _make_policy(org_a, priority="Urgent", first_response_hours=2)

        response = admin_client.put(
            f"{POLICIES_URL}{policy.id}/",
            {"first_response_hours": 0},
            format="json",
        )

        assert response.status_code == 400
        assert "first_response_hours" in response.data["errors"]
        policy.refresh_from_db()
        assert policy.first_response_hours == 2

    def test_non_admin_cannot_set_hours(self, user_client, org_a):
        response = user_client.post(
            POLICIES_URL,
            {
                "priority": "High",
                "first_response_action": "notify",
                "resolution_action": "notify",
                "first_response_hours": 3,
            },
            format="json",
        )

        assert response.status_code == 403
        assert not EscalationPolicy.objects.filter(org=org_a, priority="High").exists()
