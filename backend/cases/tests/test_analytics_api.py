"""API-layer tests for /api/cases/analytics/* endpoints.

Pure analytics math is covered by `test_analytics.py`; here we verify the
view layer: URL wiring, filter parsing, RLS isolation, drilldown payloads,
and CSV streaming.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from cases.models import Case


def _utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=dt_timezone.utc)


@pytest.fixture
def cases_in_window(org_a):
    """Two cases with first_response_at and resolved_at on 2026-05-01."""
    c1 = Case.objects.create(
        org=org_a,
        name="A",
        status="Closed",
        priority="Urgent",
        sla_first_response_hours=1,
        sla_resolution_hours=2,
        closed_on="2026-05-01",
    )
    c2 = Case.objects.create(
        org=org_a,
        name="B",
        status="Closed",
        priority="Normal",
        sla_first_response_hours=4,
        sla_resolution_hours=24,
        closed_on="2026-05-01",
    )
    Case.objects.filter(pk=c1.pk).update(
        created_at=_utc(2026, 5, 1, 8),
        first_response_at=_utc(2026, 5, 1, 11),  # 3h, breaches 1h SLA
        resolved_at=_utc(2026, 5, 1, 14),  # 6h, breaches 2h SLA
    )
    Case.objects.filter(pk=c2.pk).update(
        created_at=_utc(2026, 5, 1, 8),
        first_response_at=_utc(2026, 5, 1, 9),
        resolved_at=_utc(2026, 5, 1, 16),
    )
    return c1, c2


# --------------------------------------------------------------------------
# Auth + filter parsing


class TestAnalyticsAuth:
    def test_frt_unauthenticated(self, unauthenticated_client):
        resp = unauthenticated_client.get("/api/cases/analytics/frt/")
        assert resp.status_code in (401, 403)

    def test_frt_authenticated_empty_org(self, admin_client):
        resp = admin_client.get(
            "/api/cases/analytics/frt/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 0
        assert resp.data["median_hours"] is None


class TestAnalyticsFiltering:
    def test_frt_returns_cases_in_window(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/frt/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert resp.data["breach_count"] == 1  # c1 only

    def test_frt_filter_by_priority(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/frt/?from=2026-05-01&to=2026-05-02&priority=Urgent"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_frt_default_window_excludes_old(self, admin_client, org_a):
        # Backdate a case far past the default 30-day window.
        c = Case.objects.create(
            org=org_a,
            name="Ancient",
            status="New",
            priority="Normal",
            sla_first_response_hours=4,
        )
        Case.objects.filter(pk=c.pk).update(
            created_at=_utc(2020, 1, 1, 8),
            first_response_at=_utc(2020, 1, 1, 9),
        )
        resp = admin_client.get("/api/cases/analytics/frt/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0


# --------------------------------------------------------------------------
# Each metric endpoint smoke


class TestEndpointSmokes:
    def test_mttr(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/mttr/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 2
        assert "Urgent" in resp.data["by_priority"]

    def test_backlog(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/backlog/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        # ?to=2026-05-02 is interpreted as end-of-day on the 2nd, so the window
        # spans 2 calendar days (5-01 and 5-02). Both fixture cases opened-and-
        # closed on 5-01 → both day buckets show open_count=0 in steady state.
        assert len(resp.data["series"]) == 2
        assert resp.data["series"][0]["open_count"] == 0
        assert resp.data["series"][1]["open_count"] == 0

    def test_agents(self, admin_client, cases_in_window, admin_profile):
        c, _ = cases_in_window
        c.assigned_to.add(admin_profile)
        resp = admin_client.get(
            "/api/cases/analytics/agents/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        emails = [r["email"] for r in resp.data["results"]]
        assert "admin@test.com" in emails

    def test_sla(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/sla/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["total"] == 2
        assert resp.data["frt_breach_count"] == 1
        assert resp.data["resolution_breach_count"] == 1


# --------------------------------------------------------------------------
# Cross-org isolation


class TestRlsIsolation:
    def test_org_b_does_not_see_org_a_metrics(self, org_b_client, cases_in_window):
        resp = org_b_client.get(
            "/api/cases/analytics/frt/?from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 0


# --------------------------------------------------------------------------
# Drilldown


class TestDrilldown:
    def test_drilldown_frt(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/drilldown/?metric=frt&from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 2

    def test_drilldown_frt_breach_bucket(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/drilldown/?metric=frt&bucket=breach&from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_drilldown_backlog_requires_bucket(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/drilldown/?metric=backlog&from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 400

    def test_drilldown_unknown_metric(self, admin_client):
        resp = admin_client.get("/api/cases/analytics/drilldown/?metric=garbage")
        assert resp.status_code == 400


# --------------------------------------------------------------------------
# CSV export


class TestCsvExport:
    def test_csv_streams(self, admin_client, cases_in_window):
        resp = admin_client.get(
            "/api/cases/analytics/export/?metric=frt&fmt=csv&from=2026-05-01&to=2026-05-02"
        )
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv; charset=utf-8"
        body = b"".join(resp.streaming_content).decode("utf-8-sig")
        lines = body.strip().split("\n")
        # Header + 2 rows.
        assert len(lines) == 3
        assert lines[0].split(",")[0] == "ID"

    def test_csv_format_validation(self, admin_client):
        resp = admin_client.get("/api/cases/analytics/export/?metric=frt&fmt=xlsx")
        assert resp.status_code == 400
