"""A close that sends no `closed_on` is dated by the server, in the org's day.

Closing a ticket needs a date, and each client used to compute "today" itself:
the web from the org's zone through `Intl`, the phone by looking the zone up in
`/api/org/timezones/`. That list leaves out legacy names such as `US/Eastern`,
which `validate_iana_timezone` accepts and which an org may well be stored
under, so the phone refused to close any of that org's tickets. The server
already knows the org's day (`GetProfileAndOrg` activates its timezone), so
`cases.approvals.closing_date` dates the close there, for every close path.

The clock is frozen at instants where the UTC day and the org's day differ, so
a close dated in UTC fails these instead of passing for most of the day.
"""

import datetime
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status

from cases.approvals import Approval, ApprovalRule
from cases.models import Case
from common.org_time import activate_org_timezone
from common.packs.applier import apply_pack
from common.packs.loader import get_pack

pytestmark = pytest.mark.django_db

# 23:30 UTC on 6 August is 05:00 on 7 August in Asia/Kolkata.
KOLKATA_AHEAD = datetime.datetime(2026, 8, 6, 23, 30, tzinfo=datetime.timezone.utc)
# 02:00 UTC on 7 August is 22:00 on 6 August in US/Eastern (EDT).
EASTERN_BEHIND = datetime.datetime(2026, 8, 7, 2, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def frozen():
    """Freeze `timezone.now()`, which `timezone.localdate()` reads."""

    def freeze(instant):
        return patch("django.utils.timezone.now", return_value=instant)

    return freeze


@pytest.fixture
def mailer():
    with patch("cases.updates.send_email_to_assigned_user") as task:
        yield task


def _in_zone(org, name):
    org.timezone = name
    org.save(update_fields=["timezone"])


def _detail(case):
    return f"/api/cases/{case.pk}/"


class TestPatchClose:
    def test_dated_in_the_org_day_not_utc(self, admin_client, case_a, org_a, frozen):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"status": "Closed"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.status == "Closed"
        assert case_a.closed_on == datetime.date(2026, 8, 7)

    def test_a_legacy_zone_name_is_dated_too(self, admin_client, case_a, org_a, frozen):
        # The name the phone's zone list does not carry. Behind UTC here, so
        # a UTC day would be the 7th.
        _in_zone(org_a, "US/Eastern")
        with frozen(EASTERN_BEHIND):
            resp = admin_client.patch(
                _detail(case_a), {"status": "Closed"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 6)

    def test_an_explicit_null_is_dated_like_an_absent_one(
        self, admin_client, case_a, org_a, frozen
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"status": "Closed", "closed_on": None}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 7)

    def test_a_date_the_caller_sends_wins(self, admin_client, case_a, org_a, frozen):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a),
                {"status": "Closed", "closed_on": "2026-08-01"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 1)

    def test_a_malformed_date_is_a_400_and_writes_nothing(self, admin_client, case_a):
        resp = admin_client.patch(
            _detail(case_a),
            {"status": "Closed", "closed_on": "not-a-date"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "closed_on" in resp.json()["errors"]
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("New", None)

    def test_the_approval_gate_still_refuses_and_writes_nothing(
        self, admin_client, case_a, org_a
    ):
        ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        resp = admin_client.patch(_detail(case_a), {"status": "Closed"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.json()["errors"]["status"] == [
            "An approval is required before this case can be closed "
            "(rule: Close needs sign-off)."
        ]
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on, case_a.resolved_at) == (
            "New",
            None,
            None,
        )

    def test_an_approved_close_is_dated(
        self, admin_client, case_a, org_a, admin_profile, frozen
    ):
        rule = ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        Approval.objects.create(
            org=org_a,
            case=case_a,
            rule=rule,
            requested_by=admin_profile,
            state="approved",
        )
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"status": "Closed"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 7)

    def test_an_edit_to_a_closed_ticket_keeps_its_date(
        self, admin_client, case_a, org_a, frozen
    ):
        """Only the transition into Closed is dated."""
        Case.objects.filter(pk=case_a.pk).update(
            status="Closed", closed_on=datetime.date(2026, 7, 1)
        )
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"priority": "Low"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 7, 1)

    def test_leaving_closed_still_clears_the_date(self, admin_client, case_a):
        admin_client.patch(_detail(case_a), {"status": "Closed"}, format="json")
        resp = admin_client.patch(_detail(case_a), {"status": "New"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        case_a.refresh_from_db()
        assert case_a.closed_on is None


class TestPutAndCreate:
    def test_put_close_without_a_date(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.put(
                _detail(case_a),
                {"name": case_a.name, "status": "Closed", "priority": "High"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 7),
        )

    def test_put_on_a_closed_ticket_without_a_date_keeps_it(
        self, admin_client, case_a, org_a, mailer
    ):
        # PUT used to pass `closed_on=None` for an absent key, which left a
        # Closed ticket with no date at all.
        Case.objects.filter(pk=case_a.pk).update(
            status="Closed", closed_on=datetime.date(2026, 7, 1)
        )
        resp = admin_client.put(
            _detail(case_a),
            {"name": case_a.name, "status": "Closed", "priority": "Low"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 7, 1)

    def test_create_as_closed_without_a_date(self, admin_client, org_a, frozen, mailer):
        _in_zone(org_a, "US/Eastern")
        with frozen(EASTERN_BEHIND):
            resp = admin_client.post(
                "/api/cases/",
                {"name": "Filed closed", "status": "Closed", "priority": "Low"},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert Case.objects.get(name="Filed closed").closed_on == datetime.date(
            2026, 8, 6
        )


class TestOtherClosePaths:
    def test_kanban_move_is_dated_in_the_org_day(
        self, admin_client, case_a, org_a, frozen
    ):
        _in_zone(org_a, "US/Eastern")
        with frozen(EASTERN_BEHIND):
            resp = admin_client.patch(
                f"/api/cases/{case_a.pk}/move/", {"status": "Closed"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 6),
        )

    def test_kanban_move_still_takes_the_approval_gate(
        self, admin_client, case_a, org_a
    ):
        ApprovalRule.objects.create(
            org=org_a, name="Close needs sign-off", trigger_event="pre_close"
        )
        resp = admin_client.patch(
            f"/api/cases/{case_a.pk}/move/", {"status": "Closed"}, format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert list(resp.json()["errors"]) == ["status"]
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("New", None)

    def test_bulk_close_without_a_date(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.post(
                "/api/cases/bulk/update/",
                {"ids": [str(case_a.pk)], "fields": {"status": "Closed"}},
                content_type="application/json",
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["results"] == [{"id": str(case_a.pk), "status": "updated"}]
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 7),
        )

    def test_bulk_close_with_a_picked_date_keeps_it(self, admin_client, case_a, mailer):
        resp = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": "2026-08-01"},
            },
            content_type="application/json",
        )
        assert resp.json()["updated"] == 1
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 1)

    def test_bulk_close_with_a_malformed_date_is_invalid(
        self, admin_client, case_a, mailer
    ):
        resp = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": "not-a-date"},
            },
            content_type="application/json",
        )
        assert resp.json()["results"][0]["status"] == "invalid"
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("New", None)

    def test_macro_close_is_dated_in_the_org_day(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        from macros.models import Macro

        macro = Macro.objects.create(
            org=org_a, title="Wrap up", body="", scope="org", set_status="Closed"
        )
        _in_zone(org_a, "US/Eastern")
        with frozen(EASTERN_BEHIND):
            resp = admin_client.post(
                f"/api/macros/{macro.pk}/apply/",
                {"case_id": str(case_a.pk)},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 6),
        )

    def test_close_with_children_is_dated_in_the_org_day(
        self, admin_client, case_a, org_a, frozen
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.post(
                f"/api/cases/{case_a.pk}/close-with-children/",
                {"cascade": False},
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 7),
        )


STORED = datetime.date(2026, 7, 1)


def _closed(case, closed_on=STORED):
    """Put ``case`` in Closed straight in the table, bypassing the signal, as a
    ticket closed before the date rule existed would be."""
    Case.objects.filter(pk=case.pk).update(status="Closed", closed_on=closed_on)


class TestExplicitNullNeverLeavesAClosedTicketUndated:
    """A write whose resulting status is Closed always leaves a `closed_on`.

    `null` (or no key) on a ticket already Closed used to pass through
    untouched, because `closing_date` only dated the transition into Closed:
    `PATCH {"closed_on": null}` left a Closed ticket with no date. It now keeps
    the stored date, and only when there is none is it dated today.
    """

    def test_patch_null_keeps_the_stored_date(self, admin_client, case_a):
        _closed(case_a)
        resp = admin_client.patch(_detail(case_a), {"closed_on": None}, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("Closed", STORED)

    def test_patch_null_with_status_closed_keeps_the_stored_date(
        self, admin_client, case_a
    ):
        _closed(case_a)
        resp = admin_client.patch(
            _detail(case_a), {"status": "Closed", "closed_on": None}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == STORED

    def test_patch_on_a_closed_ticket_with_no_date_dates_it_today(
        self, admin_client, case_a, org_a, frozen
    ):
        _closed(case_a, closed_on=None)
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"closed_on": None}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 7)

    def test_put_null_keeps_the_stored_date(self, admin_client, case_a, mailer):
        _closed(case_a)
        resp = admin_client.put(
            _detail(case_a),
            {
                "name": case_a.name,
                "status": "Closed",
                "priority": "Low",
                "closed_on": None,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == STORED

    def test_put_null_on_a_transition_is_dated_today(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.put(
                _detail(case_a),
                {
                    "name": case_a.name,
                    "status": "Closed",
                    "priority": "Low",
                    "closed_on": None,
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 7),
        )

    def test_bulk_null_keeps_the_stored_date(self, admin_client, case_a, mailer):
        _closed(case_a)
        resp = admin_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(case_a.pk)],
                "fields": {"status": "Closed", "closed_on": None},
            },
            content_type="application/json",
        )
        assert resp.json()["updated"] == 1, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == STORED

    def test_bulk_null_on_a_transition_is_dated_today(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.post(
                "/api/cases/bulk/update/",
                {
                    "ids": [str(case_a.pk)],
                    "fields": {"status": "Closed", "closed_on": None},
                },
                content_type="application/json",
            )
        assert resp.json()["updated"] == 1, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == (
            "Closed",
            datetime.date(2026, 8, 7),
        )

    def _macro_close(self, client, org, case):
        from macros.models import Macro

        macro = Macro.objects.create(
            org=org, title="Wrap up", body="", scope="org", set_status="Closed"
        )
        return client.post(
            f"/api/macros/{macro.pk}/apply/", {"case_id": str(case.pk)}, format="json"
        )

    def test_macro_close_on_a_closed_ticket_keeps_the_stored_date(
        self, admin_client, case_a, org_a, mailer
    ):
        _closed(case_a)
        resp = self._macro_close(admin_client, org_a, case_a)
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == STORED

    def test_macro_close_on_an_undated_closed_ticket_dates_it_today(
        self, admin_client, case_a, org_a, frozen, mailer
    ):
        _closed(case_a, closed_on=None)
        _in_zone(org_a, "US/Eastern")
        with frozen(EASTERN_BEHIND):
            resp = self._macro_close(admin_client, org_a, case_a)
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 6)

    def test_a_null_on_an_open_ticket_stays_null(self, admin_client, case_a):
        resp = admin_client.patch(_detail(case_a), {"closed_on": None}, format="json")
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("New", None)

    def test_leaving_closed_with_a_date_still_clears_it(self, admin_client, case_a):
        _closed(case_a)
        resp = admin_client.patch(
            _detail(case_a),
            {"status": "New", "closed_on": "2026-08-01"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert (case_a.status, case_a.closed_on) == ("New", None)


class TestWritersThatGoStraightToTheModel:
    """The pre-save signal applies `closing_date` to every save, so a writer
    that skips the serializer (CSV import, the packs applier) cannot leave a
    Closed ticket undated either."""

    def test_create_closed_without_a_date(self, org_a, frozen):
        _in_zone(org_a, "Asia/Kolkata")
        activate_org_timezone(org_a)
        try:
            with frozen(KOLKATA_AHEAD):
                case = Case.objects.create(
                    name="Imported closed", status="Closed", priority="Low", org=org_a
                )
        finally:
            timezone.deactivate()
        assert case.closed_on == datetime.date(2026, 8, 7)
        case.refresh_from_db()
        assert case.closed_on == datetime.date(2026, 8, 7)

    def test_save_with_none_on_a_closed_ticket_keeps_the_stored_date(self, case_a):
        _closed(case_a)
        case_a.refresh_from_db()
        case_a.closed_on = None
        case_a.save()
        case_a.refresh_from_db()
        assert case_a.closed_on == STORED

    def test_csv_import_of_a_closed_row_without_a_date(
        self, admin_client, org_a, frozen
    ):
        body = b"name,status,priority\nImported closed,Closed,Low\n"
        _in_zone(org_a, "Asia/Kolkata")
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.post(
                "/api/cases/import/commit/",
                {"file": SimpleUploadedFile("t.csv", body, "text/csv")},
                format="multipart",
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case = Case.objects.get(name="Imported closed")
        assert (case.status, case.closed_on) == ("Closed", datetime.date(2026, 8, 7))

    def test_pack_sample_tickets_that_are_closed_are_dated(self, org_a, admin_profile):
        apply_pack(org_a, get_pack("real-estate"), admin_profile)
        closed = Case.objects.filter(org=org_a, is_sample=True, status="Closed")
        assert closed.exists()
        assert not closed.filter(closed_on__isnull=True).exists()


class TestAnOpenTicketCarriesNoCloseDate:
    """An open ticket never holds a `closed_on`, however it was sent, so the
    next close is dated then rather than inheriting a stale date."""

    def test_date_sent_with_an_open_status_is_dropped(self, admin_client, case_a):
        resp = admin_client.patch(
            _detail(case_a),
            {"status": "New", "closed_on": "2020-01-01"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.status == "New"
        assert case_a.closed_on is None

    def test_next_close_is_dated_today_not_the_stale_date(
        self, admin_client, case_a, org_a, frozen
    ):
        _in_zone(org_a, "Asia/Kolkata")
        admin_client.patch(
            _detail(case_a), {"status": "New", "closed_on": "2020-01-01"}, format="json"
        )
        with frozen(KOLKATA_AHEAD):
            resp = admin_client.patch(
                _detail(case_a), {"status": "Closed"}, format="json"
            )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 7)

    def test_rejected_keeps_a_date_it_was_given(self, admin_client, case_a):
        resp = admin_client.patch(
            _detail(case_a),
            {"status": "Rejected", "closed_on": "2026-08-01"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK, resp.content
        case_a.refresh_from_db()
        assert case_a.closed_on == datetime.date(2026, 8, 1)
