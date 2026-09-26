"""Tests for the admin-only service-overview endpoint.

`GET /api/cases/analytics/service/` powers /v2/tickets/analytics. It differs
from the per-metric endpoints in two ways this file pins: it is a single call
returning the whole page shape, and it is *admin-only* (the per-metric
endpoints narrow a non-admin to their own cases; this one 403s them). The
window is "last N days" anchored to now, so fixtures use now-relative
timestamps rather than a fixed month.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from cases.models import Case

# A single reference instant for the whole fixture. Anchoring every timestamp
# to one `now` keeps the FRT deltas exact, deriving each from its own
# timezone.now() call drifts by microseconds, which would nudge a case that
# meets its SLA exactly (1h against a 1h target) just over the line.
_BASE = timezone.now()


def _ago(**kw):
    return _BASE - timedelta(**kw)


@pytest.fixture
def service_cases(org_a, admin_profile):
    """Three recent cases spanning met / breached / in-flight first responses.

    A (Urgent, Incident): responded in 1h against a 1h SLA → met; resolved 2h
       after open. Assigned to the admin.
    B (High, Question): responded in 8h against a 4h SLA → breach; still open.
       Assigned to the admin.
    C (Normal, Problem): no response yet, 1h old against an 8h SLA → in-flight
       (neither met nor missed). Unassigned.
    """
    a = Case.objects.create(
        org=org_a,
        name="A",
        status="Closed",
        priority="Urgent",
        case_type="Incident",
        sla_first_response_hours=1,
        sla_resolution_hours=2,
        closed_on=_BASE.date(),
    )
    b = Case.objects.create(
        org=org_a,
        name="B",
        status="Assigned",
        priority="High",
        case_type="Question",
        sla_first_response_hours=4,
        sla_resolution_hours=24,
    )
    c = Case.objects.create(
        org=org_a,
        name="C",
        status="New",
        priority="Normal",
        case_type="Problem",
        sla_first_response_hours=8,
        sla_resolution_hours=48,
    )
    Case.objects.filter(pk=a.pk).update(
        created_at=_ago(hours=3),
        first_response_at=_ago(hours=2),  # FRT = 1h, meets 1h SLA
        resolved_at=_ago(hours=1),  # MTTR = 2h
    )
    Case.objects.filter(pk=b.pk).update(
        created_at=_ago(hours=10),
        first_response_at=_ago(hours=2),  # FRT = 8h, breaches 4h SLA
    )
    Case.objects.filter(pk=c.pk).update(created_at=_ago(hours=1))
    a.assigned_to.add(admin_profile)
    b.assigned_to.add(admin_profile)
    return a, b, c


# --------------------------------------------------------------------------
# Authorization: the admin-only gate (both directions)


class TestServiceAuthorization:
    def test_unauthenticated_rejected(self, unauthenticated_client):
        resp = unauthenticated_client.get("/api/cases/analytics/service/")
        assert resp.status_code in (401, 403)

    def test_non_admin_forbidden(self, user_client, service_cases):
        """A USER-role member gets 403, never a personal slice."""
        resp = user_client.get("/api/cases/analytics/service/")
        assert resp.status_code == 403

    def test_admin_allowed(self, admin_client, service_cases):
        resp = admin_client.get("/api/cases/analytics/service/")
        assert resp.status_code == 200
        assert set(resp.data) == {
            "totals",
            "volume",
            "first_response",
            "by_type",
            "by_agent",
        }


# --------------------------------------------------------------------------
# Cross-org isolation


class TestServiceIsolation:
    def test_other_org_sees_nothing(self, org_b_client, service_cases):
        resp = org_b_client.get("/api/cases/analytics/service/")
        assert resp.status_code == 200
        assert resp.data["totals"]["opened"] == 0
        assert resp.data["totals"]["closed"] == 0
        assert resp.data["by_type"] == []
        assert resp.data["by_agent"] == []


# --------------------------------------------------------------------------
# Payload correctness


class TestServiceTotals:
    def test_totals(self, admin_client, service_cases):
        totals = admin_client.get("/api/cases/analytics/service/").data["totals"]
        assert totals["opened"] == 3  # A, B, C created in window
        assert totals["closed"] == 1  # A resolved
        assert totals["open_now"] == 2  # B, C not terminal (A is Closed)
        assert totals["median_resolution_hours"] == 2  # A: 2h
        assert totals["window_days"] == 14

    def test_window_days_param_clamped(self, admin_client, service_cases):
        totals = admin_client.get("/api/cases/analytics/service/?days=999").data[
            "totals"
        ]
        assert totals["window_days"] == 90  # clamped to max

    def test_volume_totals_match_counts(self, admin_client, service_cases):
        data = admin_client.get("/api/cases/analytics/service/").data
        assert sum(d["opened"] for d in data["volume"]) == 3
        assert sum(d["closed"] for d in data["volume"]) == 1
        assert len(data["volume"]) == 14  # one bucket per window day


class TestServiceFirstResponse:
    def test_all_priorities_worst_first(self, admin_client, service_cases):
        rows = admin_client.get("/api/cases/analytics/service/").data["first_response"]
        assert [r["priority"] for r in rows] == ["Urgent", "High", "Normal", "Low"]

    def test_met_missed_and_targets(self, admin_client, service_cases):
        rows = {
            r["priority"]: r
            for r in admin_client.get("/api/cases/analytics/service/").data[
                "first_response"
            ]
        }
        assert rows["Urgent"]["met"] == 1 and rows["Urgent"]["missed"] == 0
        assert rows["Urgent"]["target_minutes"] == 60
        assert rows["High"]["missed"] == 1 and rows["High"]["met"] == 0
        assert rows["High"]["target_minutes"] == 240
        # C is in-flight but not yet overdue: neither met nor missed.
        assert rows["Normal"]["met"] == 0 and rows["Normal"]["missed"] == 0
        assert rows["Normal"]["median_minutes"] is None
        # No Low cases → stable zero row, not dropped.
        assert rows["Low"]["met"] == 0 and rows["Low"]["missed"] == 0

    def test_targets_come_from_the_orgs_policy(self, admin_client, org_a, org_b):
        """The card says its targets come from the escalation policy, so they do.

        A policy's blank first-response target, an inactive policy, a priority
        with no policy and another org's policy all leave the built-in default.
        """
        from cases.models import EscalationPolicy
        from conftest import rls_org

        def policy(org, priority, **fields):
            with rls_org(org):
                EscalationPolicy.objects.create(
                    org=org,
                    priority=priority,
                    first_response_action="notify",
                    resolution_action="notify",
                    **fields,
                )

        policy(org_a, "Urgent", first_response_hours=2)
        policy(org_a, "High", first_response_hours=None, resolution_hours=10)
        policy(org_a, "Normal", first_response_hours=30, is_active=False)
        policy(org_b, "Low", first_response_hours=50)

        rows = {
            r["priority"]: r["target_minutes"]
            for r in admin_client.get("/api/cases/analytics/service/").data[
                "first_response"
            ]
        }
        assert rows == {"Urgent": 120, "High": 240, "Normal": 480, "Low": 1440}


class TestServiceByType:
    def test_case_type_mix(self, admin_client, service_cases):
        by_type = {
            t["case_type"]: t["count"]
            for t in admin_client.get("/api/cases/analytics/service/").data["by_type"]
        }
        assert by_type == {"Incident": 1, "Question": 1, "Problem": 1}

    def test_untyped_case_bucketed(self, admin_client, org_a, service_cases):
        c = Case.objects.create(
            org=org_a,
            name="D",
            status="New",
            priority="Low",
            sla_first_response_hours=24,
        )
        Case.objects.filter(pk=c.pk).update(created_at=_ago(hours=1))
        by_type = {
            t["case_type"]: t["count"]
            for t in admin_client.get("/api/cases/analytics/service/").data["by_type"]
        }
        assert by_type["Uncategorized"] == 1


class TestServiceByAgent:
    def test_agent_and_unassigned_rows(
        self, admin_client, service_cases, admin_profile
    ):
        by_agent = admin_client.get("/api/cases/analytics/service/").data["by_agent"]
        admin_row = next(r for r in by_agent if r["id"] == str(admin_profile.id))
        assert admin_row["open"] == 1  # B open (A closed)
        assert admin_row["closed_this_week"] == 1  # A resolved 1h ago
        assert admin_row["breached"] == 1  # B breached FRT
        assert admin_row["median_first_response_minutes"] == 270  # median(60, 480)

        unassigned = next(r for r in by_agent if r["id"] is None)
        assert unassigned["name"] == "Unassigned"
        assert unassigned["open"] == 1  # C
        assert unassigned["breached"] == 0

    def test_unassigned_row_hidden_when_empty(self, admin_client, org_a, admin_profile):
        c = Case.objects.create(
            org=org_a,
            name="Solo",
            status="New",
            priority="Normal",
            sla_first_response_hours=8,
        )
        Case.objects.filter(pk=c.pk).update(created_at=_ago(hours=1))
        c.assigned_to.add(admin_profile)
        by_agent = admin_client.get("/api/cases/analytics/service/").data["by_agent"]
        assert all(r["id"] is not None for r in by_agent)


# --------------------------------------------------------------------------
# Empty org: the shape holds so the SSR page never divides by an absent array


class TestServiceEmpty:
    def test_empty_org_stable_shape(self, admin_client):
        data = admin_client.get("/api/cases/analytics/service/").data
        assert data["totals"]["opened"] == 0
        assert data["totals"]["median_resolution_hours"] == 0
        assert len(data["volume"]) == 14
        assert len(data["first_response"]) == 4  # all priorities still present
        assert data["by_type"] == []
        assert data["by_agent"] == []
