"""The ticket list and the watching list read the SLA calendar once.

`CaseSerializer` reports six SLA fields per ticket, each walking the org's
business calendar, and each ticket looked the calendar up for itself: one
query per row for the calendar, and (before `add_business_hours` read the
prefetch) several more for its holidays. `SharedCalendarListSerializer` reads
each org's calendar once per list and hands it to every ticket. These are the
list endpoints both clients read; the detail endpoint serializes one ticket.

What is counted is the calendar's own queries, not the whole response: the
list still spends a few queries per row on `time_summary` and `child_count`,
which is a separate cost and not this one.
"""

from datetime import date, time, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from business_hours.models import BusinessCalendar, BusinessHoliday
from cases.models import Case, CaseWatcher
from cases.serializer import CaseSerializer
from conftest import rls_org


def _calendar(org):
    hours = {}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        hours[f"{day}_open"] = time(9, 0)
        hours[f"{day}_close"] = time(17, 0)
    cal = BusinessCalendar.objects.create(
        org=org, name="Default", timezone="UTC", is_default=True, **hours
    )
    BusinessHoliday.objects.create(
        calendar=cal, org=org, date=date(2020, 1, 1), name="New year"
    )
    return cal


def _case(org, creator, name):
    case = Case.objects.create(
        name=name,
        status="New",
        priority="Normal",
        org=org,
        created_by=creator,
        sla_first_response_hours=4,
        sla_resolution_hours=24,
    )
    Case.objects.filter(pk=case.pk).update(
        created_at=timezone.now() - timedelta(days=30)
    )
    return case


def _queries(client, url):
    """The response, and how many queries it spent on the calendar tables."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200, response.content
    calendar_queries = [
        q["sql"]
        for q in queries
        if '"business_calendar"' in q["sql"] or '"business_holiday"' in q["sql"]
    ]
    return response.json(), len(calendar_queries)


@pytest.fixture
def calendar(org_a):
    return _calendar(org_a)


def test_the_list_reads_the_calendar_once_for_one_row_or_five(
    admin_client, admin_user, org_a, calendar
):
    _case(org_a, admin_user, "First")
    _, one = _queries(admin_client, "/api/cases/")
    for n in range(4):
        _case(org_a, admin_user, f"More {n}")
    body, five = _queries(admin_client, "/api/cases/")

    assert len(body["cases"]) == 5
    assert five == one == 2  # the calendar, then its prefetched holidays
    assert all(row["is_sla_first_response_breached"] for row in body["cases"])


def test_the_watching_list_reads_the_calendar_once_for_one_row_or_five(
    user_client, admin_user, user_profile, org_a, calendar
):
    def watched(name):
        case = _case(org_a, admin_user, name)
        CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)

    watched("First")
    _, one = _queries(user_client, "/api/cases/watching/")
    for n in range(4):
        watched(f"More {n}")
    body, five = _queries(user_client, "/api/cases/watching/")

    assert len(body["cases"]) == 5
    assert five == one == 2


def test_each_org_in_a_list_gets_its_own_calendar(admin_user, org_a, org_b, calendar):
    ours = _case(org_a, admin_user, "Ours")
    with rls_org(org_b):
        theirs_calendar = _calendar(org_b)
        theirs = _case(org_b, admin_user, "Theirs")

    CaseSerializer([ours, theirs], many=True).data

    assert ours._sla_calendar() == calendar
    assert theirs._sla_calendar() == theirs_calendar
