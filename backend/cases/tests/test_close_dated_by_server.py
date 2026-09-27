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
from rest_framework import status

from cases.approvals import Approval, ApprovalRule
from cases.models import Case

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
