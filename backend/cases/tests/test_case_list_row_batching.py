"""A page of tickets costs the same queries for one row or five.

`CaseSerializer` reports, per ticket, its SLA state (the org's business
calendar), `child_count` and `time_summary`. Each row used to ask for all
three itself, so `/cases/` and `/cases/watching/` grew by several queries a
row; the watching list also loaded each row's account, people, teams and tags
one row at a time. `CaseRowsListSerializer` answers the page in a fixed
number of queries and the watching list loads related rows as `/cases/` does.

The values must be the ones a single ticket gives: stopped time only (a
running timer is left out), the billable split, the heaviest person first, and
a child count that includes children the viewer cannot open, which is what
`/tree/` shows as redacted nodes and what both clients count when they warn
what a cascading close takes.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from cases.models import Case, CaseWatcher, TimeEntry


def _ticket(org, creator, name, **kw):
    return Case.objects.create(
        name=name, status="New", priority="Normal", org=org, created_by=creator, **kw
    )


def _time(case, profile, minutes, *, billable, hours_ago, running=False):
    start = timezone.now() - timedelta(hours=hours_ago)
    return TimeEntry.objects.create(
        org=case.org,
        case=case,
        profile=profile,
        started_at=start,
        ended_at=None if running else start + timedelta(minutes=minutes),
        billable=billable,
    )


@pytest.fixture
def seed(org_a, admin_user, admin_profile, user_profile):
    """Adds tickets the `user_client` caller watches, each with a child that
    caller cannot open and time from two people."""
    made = []

    def _seed(n):
        for _ in range(n):
            i = len(made)
            ticket = _ticket(org_a, admin_user, f"Ticket {i}")
            CaseWatcher.objects.create(case=ticket, profile=user_profile, org=org_a)
            _ticket(org_a, admin_user, f"Child {i}", parent=ticket)
            _time(ticket, admin_profile, 60, billable=True, hours_ago=5)
            _time(ticket, user_profile, 30, billable=False, hours_ago=3)
            made.append(ticket)
        return made

    return _seed


def _get(client, url):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200, response.content
    return response.json()["cases"], len(queries)


def test_the_list_costs_the_same_for_one_row_or_five(admin_client, seed):
    seed(1)
    rows, one = _get(admin_client, "/api/cases/?limit=50")
    assert len(rows) == 2  # the ticket and its child
    seed(4)
    rows, five = _get(admin_client, "/api/cases/?limit=50")
    assert len(rows) == 10
    assert five == one


def test_the_watching_list_costs_the_same_for_one_row_or_five(user_client, seed):
    seed(1)
    rows, one = _get(user_client, "/api/cases/watching/")
    assert len(rows) == 1
    seed(4)
    rows, five = _get(user_client, "/api/cases/watching/")
    assert len(rows) == 5
    assert five == one


def test_a_row_says_what_the_ticket_says_alone(
    user_client, seed, admin_profile, user_profile
):
    (ticket,) = seed(1)
    # A running timer is not counted, on the list or alone.
    _time(ticket, admin_profile, 0, billable=True, hours_ago=1, running=True)

    (row,) = _get(user_client, "/api/cases/watching/")[0]
    detail = user_client.get(f"/api/cases/{ticket.id}/")
    assert detail.status_code == 200
    alone = detail.json()["cases_obj"]

    assert row["child_count"] == alone["child_count"] == 1
    assert row["time_summary"] == alone["time_summary"]
    summary = row["time_summary"]
    assert summary["total_minutes"] == 90
    assert summary["billable_minutes"] == 60
    assert [(p["profile_id"], p["minutes"]) for p in summary["by_profile"]] == [
        (str(admin_profile.id), 60),
        (str(user_profile.id), 30),
    ]
    stopped = TimeEntry.objects.filter(case=ticket, ended_at__isnull=False)
    latest = max(e.started_at for e in stopped)
    assert summary["last_entry_at"].startswith(latest.isoformat()[:19])


def test_a_ticket_with_no_children_or_time_reads_as_empty(admin_client, seed):
    seed(1)
    rows, _ = _get(admin_client, "/api/cases/?limit=50")
    child = next(r for r in rows if r["name"].startswith("Child"))
    assert child["child_count"] == 0
    assert child["time_summary"] == {
        "total_minutes": 0,
        "billable_minutes": 0,
        "last_entry_at": None,
        "by_profile": [],
    }
