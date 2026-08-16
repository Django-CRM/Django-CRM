"""Tests for the time reports: ``/api/time-entries/report/`` and its CSV.

Covers the grouping and the money in ``cases/time_reports.py``, the window and
filter parsing in ``_report_entries``, and the two rules that decide what a
caller may see: an agent reports on their own time, an admin on the org's.
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from crum import impersonate
from django.utils import timezone

from accounts.models import Account
from cases.models import Case, TimeEntry

REPORT = "/api/time-entries/report/"
EXPORT = "/api/time-entries/report/export/"


def _case(org, creator, name="Sample case", account=None):
    with impersonate(creator):
        return Case.objects.create(
            name=name, status="New", priority="Normal", org=org, account=account
        )


def _logged(case, profile, minutes, *, days_ago=0, billable=False, rate=None, **kwargs):
    """A stopped entry of `minutes`, ending `days_ago` days back."""
    ended = timezone.now() - timedelta(days=days_ago)
    return TimeEntry.objects.create(
        org=case.org,
        case=case,
        profile=profile,
        started_at=ended - timedelta(minutes=minutes),
        ended_at=ended,
        billable=billable,
        hourly_rate=Decimal(rate) if rate is not None else None,
        **kwargs,
    )


@pytest.mark.django_db
class TestGrouping:
    def test_groups_by_agent_with_the_billable_split_and_its_value(
        self, admin_client, admin_user, admin_profile, user_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 60, billable=True, rate="90")
        _logged(case, admin_profile, 30)
        _logged(case, user_profile, 45, billable=True, rate="60")

        response = admin_client.get(REPORT, {"group_by": "agent"})

        assert response.status_code == 200
        body = response.json()
        assert body["group_by"] == "agent"
        # Ordered by time logged, so the 90 minutes comes first.
        assert [r["total_minutes"] for r in body["rows"]] == [90, 45]
        first = body["rows"][0]
        assert first["billable_minutes"] == 60
        assert first["billable_value"] == "90.00"
        assert first["entry_count"] == 2
        assert body["totals"] == {
            "total_minutes": 135,
            "billable_minutes": 105,
            # 90 for the hour, plus 45 minutes at 60/hr.
            "billable_value": "135.00",
            "entry_count": 3,
        }

    def test_groups_by_ticket(self, admin_client, admin_user, admin_profile, org_a):
        first = _case(org_a, admin_user, name="Printer down")
        second = _case(org_a, admin_user, name="Password reset")
        _logged(first, admin_profile, 25)
        _logged(second, admin_profile, 50)

        rows = admin_client.get(REPORT, {"group_by": "ticket"}).json()["rows"]

        assert [(r["name"], r["total_minutes"]) for r in rows] == [
            ("Password reset", 50),
            ("Printer down", 25),
        ]

    def test_groups_by_account_and_names_the_unattributed_bucket(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="Reyes & Co", org=org_a)
        _logged(_case(org_a, admin_user, account=account), admin_profile, 40)
        # A ticket with no account still logged time, and time nobody can bill
        # is the thing this report exists to surface, so it is named, not
        # dropped.
        _logged(_case(org_a, admin_user, name="Internal"), admin_profile, 20)

        rows = admin_client.get(REPORT, {"group_by": "account"}).json()["rows"]

        assert [(r["name"], r["key"] is None) for r in rows] == [
            ("Reyes & Co", False),
            ("No account", True),
        ]

    def test_a_running_timer_is_not_counted_until_it_is_stopped(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 30)
        TimeEntry.objects.create(
            org=org_a, case=case, profile=admin_profile, started_at=timezone.now()
        )

        body = admin_client.get(REPORT).json()

        assert body["totals"]["total_minutes"] == 30
        assert body["totals"]["entry_count"] == 1

    def test_names_the_currencies_in_range_so_a_mixed_total_can_be_flagged(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 60, billable=True, rate="10", currency="USD")
        _logged(case, admin_profile, 60, billable=True, rate="10", currency="EUR")

        body = admin_client.get(REPORT).json()

        assert body["currencies"] == ["EUR", "USD"]


@pytest.mark.django_db
class TestWindowAndFilters:
    def test_defaults_to_the_last_30_days(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 15, days_ago=2)
        _logged(case, admin_profile, 99, days_ago=45)

        body = admin_client.get(REPORT).json()

        assert body["totals"]["total_minutes"] == 15
        assert body["start"] == (timezone.localdate() - timedelta(days=29)).isoformat()
        assert body["end"] == timezone.localdate().isoformat()

    def test_an_explicit_window_is_inclusive_at_both_ends(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 20, days_ago=3)
        today = timezone.localdate()

        inside = admin_client.get(
            REPORT,
            {
                "start": (today - timedelta(days=3)).isoformat(),
                "end": (today - timedelta(days=3)).isoformat(),
            },
        ).json()
        outside = admin_client.get(
            REPORT,
            {
                "start": (today - timedelta(days=2)).isoformat(),
                "end": today.isoformat(),
            },
        ).json()

        assert inside["totals"]["total_minutes"] == 20
        assert outside["totals"]["total_minutes"] == 0

    def test_filters_by_account_and_by_billable(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="Reyes & Co", org=org_a)
        billed = _case(org_a, admin_user, account=account)
        _logged(billed, admin_profile, 60, billable=True, rate="50")
        _logged(billed, admin_profile, 15)
        _logged(_case(org_a, admin_user, name="Internal"), admin_profile, 30)

        by_account = admin_client.get(REPORT, {"account": str(account.id)}).json()
        billable_only = admin_client.get(
            REPORT, {"account": str(account.id), "billable": "true"}
        ).json()

        assert by_account["totals"]["total_minutes"] == 75
        assert billable_only["totals"]["total_minutes"] == 60

    def test_rejects_a_malformed_date_a_backwards_window_and_a_bad_flag(
        self, admin_client
    ):
        assert (
            admin_client.get(
                REPORT, {"start": "16-08-2026", "end": "2026-08-16"}
            ).status_code
            == 400
        )
        assert (
            admin_client.get(
                REPORT, {"start": "2026-08-16", "end": "2026-08-01"}
            ).status_code
            == 400
        )
        assert admin_client.get(REPORT, {"billable": "yes"}).status_code == 400
        assert admin_client.get(REPORT, {"group_by": "planet"}).status_code == 400

    def test_unauthenticated_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(REPORT).status_code in (401, 403)


@pytest.mark.django_db
class TestWhoMaySeeWhat:
    def test_an_agent_reports_on_their_own_time_only(
        self, user_client, admin_user, admin_profile, user_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 60)
        _logged(case, user_profile, 20)

        body = user_client.get(REPORT).json()

        assert body["totals"]["total_minutes"] == 20
        assert [r["total_minutes"] for r in body["rows"]] == [20]

    def test_an_agent_asking_for_someone_elses_time_is_refused_not_emptied(
        self, user_client, admin_profile
    ):
        response = user_client.get(REPORT, {"profile": str(admin_profile.id)})

        # An empty report would read as "that person logged nothing".
        assert response.status_code == 403

    def test_an_admin_may_report_on_one_agent(
        self, admin_client, admin_user, admin_profile, user_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 60)
        _logged(case, user_profile, 20)

        body = admin_client.get(REPORT, {"profile": str(user_profile.id)}).json()

        assert body["totals"]["total_minutes"] == 20

    def test_another_org_is_not_in_the_report(
        self, admin_client, admin_profile, user_b, profile_b, org_a, org_b
    ):
        _logged(_case(org_a, admin_profile.user), admin_profile, 10)
        _logged(_case(org_b, user_b, name="Theirs"), profile_b, 500)

        body = admin_client.get(REPORT).json()

        assert body["totals"]["total_minutes"] == 10


@pytest.mark.django_db
class TestCsvExport:
    def _rows(self, response):
        text = b"".join(response.streaming_content).decode()
        return list(csv.reader(io.StringIO(text)))

    def test_serves_one_row_per_entry_with_the_billing_columns(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="Reyes & Co", org=org_a)
        case = _case(org_a, admin_user, name="Printer down", account=account)
        _logged(
            case,
            admin_profile,
            45,
            billable=True,
            rate="80",
            description="Traced the failed import",
        )

        response = admin_client.get(EXPORT)

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "attachment; filename=" in response["Content-Disposition"]

        header, row = self._rows(response)
        assert header[:5] == ["Date", "Agent", "Ticket", "Account", "Description"]
        assert row[2] == "Printer down"
        assert row[3] == "Reyes & Co"
        assert row[4] == "Traced the failed import"
        assert row[5] == "45"
        assert row[6] == "0.75"
        assert row[7] == "yes"
        assert row[10] == "60.00"

    def test_a_non_billable_entry_has_no_value_rather_than_a_zero(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        _logged(_case(org_a, admin_user), admin_profile, 30)

        _header, row = self._rows(admin_client.get(EXPORT))

        assert row[7] == "no"
        assert row[8] == ""
        assert row[10] == ""

    def test_carries_the_same_window_and_the_same_visibility_as_the_report(
        self, user_client, admin_user, admin_profile, user_profile, org_a
    ):
        case = _case(org_a, admin_user)
        _logged(case, admin_profile, 60, description="Not yours")
        _logged(case, user_profile, 20, description="Yours")
        _logged(case, user_profile, 15, days_ago=90, description="Too old")

        rows = self._rows(user_client.get(EXPORT))

        assert [r[4] for r in rows[1:]] == ["Yours"]

    def test_an_agent_cannot_export_another_agents_time(
        self, user_client, admin_profile
    ):
        assert (
            user_client.get(EXPORT, {"profile": str(admin_profile.id)}).status_code
            == 403
        )


@pytest.mark.django_db
class TestTheDownloadIsReachable:
    """A client that says what it wants gets it.

    Content negotiation runs before the handler, so a view with no CSV
    renderer answers `Accept: text/csv` with 406 and never runs. Both export
    proxies in the SvelteKit app send exactly that header, so both downloads
    were broken from the browser while `curl` with no Accept header worked.
    """

    def test_the_time_export_accepts_a_csv_request(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        _logged(_case(org_a, admin_user), admin_profile, 30)

        response = admin_client.get(EXPORT, headers={"accept": "text/csv"})

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"

    def test_the_analytics_export_accepts_one_too(self, admin_client):
        response = admin_client.get(
            "/api/cases/analytics/export/",
            {"metric": "frt"},
            headers={"accept": "text/csv"},
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
