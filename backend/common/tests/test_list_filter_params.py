"""
Query-parameter handling on the list endpoints: the date and number range
filters, and the repeatable ``status``.

Two things are under test.

**The 500 class.** ``?due_date__gte=banana`` used to take the endpoint down.
The text goes straight into a date lookup, Django raises
``django.core.exceptions.ValidationError`` while building the query, and DRF's
handler does not translate that class, so a stale bookmark or a hand-edited URL
answered 500. Fifteen parameters across nine views had it. This is the same
defect ``validate_uuid`` closed for id-valued filters, wearing a different type.

**Repeatable ``status``.** The dashboard's Overdue, Due Today and Hot Leads
counts are each a two-value status filter, so before this no list could be
opened on a number the dashboard had just printed.

Run with: pytest common/tests/test_list_filter_params.py -v
"""

import pytest
from django.db import connection
from rest_framework import status as http

from leads.models import Lead
from tasks.models import Task


def _set_rls(org):
    """Set the RLS session var so a direct ORM insert satisfies the org policy."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as c:
        c.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


# (url, parameter) for every range filter reachable from a list endpoint.
DATE_PARAMS = [
    ("/api/tasks/", "due_date__gte"),
    ("/api/tasks/", "due_date__lte"),
    ("/api/tasks/", "created_at__gte"),
    ("/api/tasks/", "created_at__lte"),
    ("/api/tasks/kanban/", "due_date__gte"),
    ("/api/tasks/kanban/", "due_date__lte"),
    ("/api/leads/", "created_at__gte"),
    ("/api/leads/", "created_at__lte"),
    ("/api/leads/", "close_date__gte"),
    ("/api/leads/", "close_date__lte"),
    ("/api/leads/", "next_follow_up"),
    ("/api/leads/kanban/", "created_at__gte"),
    ("/api/leads/kanban/", "created_at__lte"),
    ("/api/accounts/", "created_at__gte"),
    ("/api/accounts/", "created_at__lte"),
    ("/api/contacts/", "created_at__gte"),
    ("/api/contacts/", "created_at__lte"),
    ("/api/cases/", "created_at__gte"),
    ("/api/cases/", "created_at__lte"),
    ("/api/cases/kanban/", "created_at__gte"),
    ("/api/cases/kanban/", "created_at__lte"),
    ("/api/opportunities/", "created_at__gte"),
    ("/api/opportunities/", "created_at__lte"),
    ("/api/opportunities/", "closed_on__gte"),
    ("/api/opportunities/", "closed_on__lte"),
    ("/api/opportunities/kanban/", "closed_on__gte"),
    ("/api/opportunities/kanban/", "closed_on__lte"),
]

NUMBER_PARAMS = [
    ("/api/opportunities/", "amount__gte"),
    ("/api/opportunities/", "amount__lte"),
]


@pytest.mark.django_db
class TestAMalformedRangeFilterIsRefusedNotCrashed:
    @pytest.mark.parametrize("url,param", DATE_PARAMS)
    def test_a_word_where_a_date_belongs_is_a_400(
        self, admin_client, org_a, url, param
    ):
        resp = admin_client.get(url, {param: "banana"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST
        assert param in resp.data

    @pytest.mark.parametrize("url,param", DATE_PARAMS)
    def test_a_real_date_still_filters(self, admin_client, org_a, url, param):
        resp = admin_client.get(url, {param: "2026-08-01"})

        assert resp.status_code == http.HTTP_200_OK

    @pytest.mark.parametrize("url,param", DATE_PARAMS)
    def test_a_day_that_does_not_exist_is_a_400(self, admin_client, org_a, url, param):
        """A date-shaped value for a day that never happened.

        `parse_date` raises `ValueError` on it rather than answering None, so it
        went straight past the check that was only looking for None and took the
        endpoint down the same way "banana" used to.
        """
        resp = admin_client.get(url, {param: "2026-02-30"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST
        assert param in resp.data

    @pytest.mark.parametrize("url,param", DATE_PARAMS)
    def test_a_full_timestamp_still_filters(self, admin_client, org_a, url, param):
        """A date-valued filter also has to take a timestamp.

        `mobile/lib/providers/tickets_provider.dart` sends
        `createdAfter.toUtc().toIso8601String()`, so this is the live shape of
        the ticket list's date filter, not a hypothetical one. Neither lookup
        will parse it out of the query string: a DateField raises on anything
        that is not YYYY-MM-DD, and so does the `__date` lookup the datetime
        columns are filtered through. `date_param` resolves the calendar day
        before either sees it.
        """
        resp = admin_client.get(url, {param: "2026-08-01T12:00:00.000Z"})

        assert resp.status_code == http.HTTP_200_OK

    @pytest.mark.parametrize("url,param", NUMBER_PARAMS)
    def test_a_word_where_a_number_belongs_is_a_400(
        self, admin_client, org_a, url, param
    ):
        resp = admin_client.get(url, {param: "banana"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST
        assert param in resp.data

    @pytest.mark.parametrize("url,param", NUMBER_PARAMS)
    def test_infinity_is_not_a_number_here(self, admin_client, org_a, url, param):
        """`Decimal` parses "Infinity" happily; no column can be compared to it."""
        resp = admin_client.get(url, {param: "Infinity"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("url,param", NUMBER_PARAMS)
    def test_a_real_number_still_filters(self, admin_client, org_a, url, param):
        resp = admin_client.get(url, {param: "1000.50"})

        assert resp.status_code == http.HTTP_200_OK

    def test_a_blank_value_means_no_filter_rather_than_an_error(
        self, admin_client, org_a
    ):
        """An empty select submits "", which is "everything", not "malformed"."""
        resp = admin_client.get("/api/tasks/", {"due_date__gte": ""})

        assert resp.status_code == http.HTTP_200_OK

    def test_a_full_timestamp_is_accepted_on_a_datetime_column(
        self, admin_client, org_a
    ):
        """`created_at` is a datetime, and callers do send one."""
        resp = admin_client.get(
            "/api/leads/", {"created_at__gte": "2026-08-01T09:30:00Z"}
        )

        assert resp.status_code == http.HTTP_200_OK


@pytest.mark.django_db
class TestStatusAcceptsMoreThanOneValue:
    """`?status=New&status=In Progress` is "still open", which is what the
    dashboard's Overdue and Due Today badges count. Without it those badges had
    nowhere honest to link: any single-status list would show a different number
    than the badge that opened it."""

    def _task(self, org, user, title, status):
        return Task.objects.create(
            title=title,
            org=org,
            status=status,
            priority="Medium",
            created_by=user,
        )

    def test_two_statuses_return_the_union(self, admin_client, org_a, admin_user):
        _set_rls(org_a)
        self._task(org_a, admin_user, "Fresh", "New")
        self._task(org_a, admin_user, "Underway", "In Progress")
        self._task(org_a, admin_user, "Finished", "Completed")

        resp = admin_client.get("/api/tasks/?status=New&status=In+Progress")

        titles = {t["title"] for t in resp.data["tasks"]}
        assert titles == {"Fresh", "Underway"}

    def test_one_status_behaves_exactly_as_before(
        self, admin_client, org_a, admin_user
    ):
        _set_rls(org_a)
        self._task(org_a, admin_user, "Fresh", "New")
        self._task(org_a, admin_user, "Underway", "In Progress")

        resp = admin_client.get("/api/tasks/", {"status": "New"})

        titles = {t["title"] for t in resp.data["tasks"]}
        assert titles == {"Fresh"}

    def test_an_unknown_status_is_dropped_rather_than_refused(
        self, admin_client, org_a, admin_user
    ):
        """A stale bookmark should not 400. Dropping the only value leaves no
        filter, which is what `?status=Nope` did before this existed."""
        _set_rls(org_a)
        self._task(org_a, admin_user, "Fresh", "New")

        resp = admin_client.get("/api/tasks/", {"status": "Nope"})

        assert resp.status_code == http.HTTP_200_OK
        assert {t["title"] for t in resp.data["tasks"]} == {"Fresh"}

    def test_an_unknown_status_beside_a_real_one_does_not_widen_it(
        self, admin_client, org_a, admin_user
    ):
        _set_rls(org_a)
        self._task(org_a, admin_user, "Fresh", "New")
        self._task(org_a, admin_user, "Finished", "Completed")

        resp = admin_client.get("/api/tasks/?status=New&status=Nope")

        assert {t["title"] for t in resp.data["tasks"]} == {"Fresh"}

    def test_leads_take_two_statuses_too(self, admin_client, org_a, admin_user):
        """Hot Leads counts `assigned` or `in process`, which is narrower than
        the open-leads list's own "not converted, not closed"."""
        _set_rls(org_a)
        for first_name, lead_status in (
            ("Ada", "assigned"),
            ("Grace", "in process"),
            ("Alan", "recycled"),
        ):
            Lead.objects.create(
                first_name=first_name,
                last_name="Tester",
                org=org_a,
                status=lead_status,
                created_by=admin_user,
            )

        resp = admin_client.get("/api/leads/?status=assigned&status=in+process")

        names = {row["first_name"] for row in resp.data["open_leads"]["open_leads"]}
        assert names == {"Ada", "Grace"}


@pytest.mark.django_db
class TestFollowUpsDueOnADay:
    def test_a_lead_is_found_by_its_follow_up_date(
        self, admin_client, org_a, admin_user
    ):
        _set_rls(org_a)
        Lead.objects.create(
            first_name="Calls",
            last_name="Today",
            org=org_a,
            status="assigned",
            next_follow_up="2026-08-07",
            created_by=admin_user,
        )
        Lead.objects.create(
            first_name="Calls",
            last_name="Tomorrow",
            org=org_a,
            status="assigned",
            next_follow_up="2026-08-08",
            created_by=admin_user,
        )

        resp = admin_client.get("/api/leads/", {"next_follow_up": "2026-08-07"})

        names = {row["last_name"] for row in resp.data["open_leads"]["open_leads"]}
        assert names == {"Today"}

    def test_a_lead_with_no_follow_up_set_is_not_matched(
        self, admin_client, org_a, admin_user
    ):
        _set_rls(org_a)
        Lead.objects.create(
            first_name="No",
            last_name="Followup",
            org=org_a,
            status="assigned",
            created_by=admin_user,
        )

        resp = admin_client.get("/api/leads/", {"next_follow_up": "2026-08-07"})

        assert resp.data["open_leads"]["open_leads"] == []


# Every analytics endpoint takes its window through the same `_parse_dt`, so
# one bad value has one behaviour across all seven. The drilldown and the export
# want a metric before they will look at anything else, so they carry one.
WINDOW_URLS = [
    ("/api/cases/analytics/frt/", {}),
    ("/api/cases/analytics/mttr/", {}),
    ("/api/cases/analytics/backlog/", {}),
    ("/api/cases/analytics/agents/", {}),
    ("/api/cases/analytics/sla/", {}),
    ("/api/cases/analytics/drilldown/", {"metric": "frt"}),
    ("/api/cases/analytics/export/", {"metric": "frt"}),
]


@pytest.mark.django_db
class TestAMalformedAnalyticsWindowIsRefusedNotIgnored:
    """`?from=banana` used to be answered 200.

    Unparseable text became `None`, which is also what "not sent" looks like, so
    the endpoint fell back to its default last-30-days window and returned
    figures for a period nobody asked about. A client with a broken date format
    had no way to find that out, and the numbers looked plausible. This is the
    same class as the 500s above, one step further along: not a crash, an
    answer to a different question.
    """

    @pytest.mark.parametrize("url,extra", WINDOW_URLS)
    @pytest.mark.parametrize("param", ["from", "to"])
    def test_a_word_where_a_date_belongs_is_a_400(
        self, admin_client, org_a, url, extra, param
    ):
        resp = admin_client.get(url, {**extra, param: "banana"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST
        assert param in resp.data

    @pytest.mark.parametrize("url,extra", WINDOW_URLS)
    def test_a_real_window_still_answers(self, admin_client, org_a, url, extra):
        resp = admin_client.get(
            url, {**extra, "from": "2026-08-01", "to": "2026-08-07"}
        )

        assert resp.status_code == http.HTTP_200_OK

    def test_a_blank_window_means_the_default_one(self, admin_client, org_a):
        """An unset date picker submits "", which is "no window given"."""
        resp = admin_client.get("/api/cases/analytics/frt/", {"from": "", "to": ""})

        assert resp.status_code == http.HTTP_200_OK

    def test_a_full_timestamp_is_still_accepted(self, admin_client, org_a):
        """The window takes an instant as well as a day, and always has."""
        resp = admin_client.get(
            "/api/cases/analytics/frt/", {"from": "2026-08-01T09:30:00Z"}
        )

        assert resp.status_code == http.HTTP_200_OK

    def test_a_day_that_does_not_exist_is_a_400(self, admin_client, org_a):
        resp = admin_client.get("/api/cases/analytics/frt/", {"from": "2026-02-31"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestTheServiceWindowLength:
    """`?days=` on the service dashboard, which had the same silent fallback."""

    url = "/api/cases/analytics/service/"

    def test_a_word_where_a_count_belongs_is_a_400(self, admin_client, org_a):
        resp = admin_client.get(self.url, {"days": "banana"})

        assert resp.status_code == http.HTTP_400_BAD_REQUEST
        assert "days" in resp.data

    def test_a_number_out_of_range_is_still_clamped(self, admin_client, org_a):
        """Out-of-range is a legible request ("as much as you have"), so it is
        held to the maximum rather than refused. Unreadable is not."""
        resp = admin_client.get(self.url, {"days": "999"})

        assert resp.status_code == http.HTTP_200_OK
        assert resp.data["totals"]["window_days"] == 90

    def test_no_days_at_all_is_the_default_window(self, admin_client, org_a):
        resp = admin_client.get(self.url)

        assert resp.status_code == http.HTTP_200_OK
        assert resp.data["totals"]["window_days"] == 14
