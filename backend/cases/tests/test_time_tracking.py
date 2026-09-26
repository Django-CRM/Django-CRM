"""Tests for Tier 3 time-tracking.

Covers:

* model constraints (``end_after_start``, ``one_active_timer_per_profile``)
* the case-scoped + entry-scoped + timesheet endpoints
* the ``InvoiceFromTimeEntriesView`` cross-app glue
* the ``TIME_LOGGED`` audit signal
* the ``auto_stop_stale_timers`` Celery task
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from crum import impersonate
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import Account
from cases.models import Case, TimeEntry
from cases.tasks import auto_stop_stale_timers as _auto_stop_stale_timers
from common.models import Activity
from conftest import restore_rls_context
from invoices.models import Invoice, InvoiceLineItem


# The task clears `app.current_org` when it finishes, correctly: it walks every
# org on a connection with no middleware, and leaving the last tenant's id
# behind would hand that context to whatever borrows the connection next. Every
# test below reads its own rows straight afterwards, so the module calls the
# task through this shim rather than repeating a restore at 2 call sites.
def auto_stop_stale_timers(*args, **kwargs):
    try:
        return _auto_stop_stale_timers(*args, **kwargs)
    finally:
        restore_rls_context()


def _make_case(org, creator, name="Sample case", account=None):
    with impersonate(creator):
        return Case.objects.create(
            name=name,
            status="New",
            priority="Normal",
            org=org,
            account=account,
        )


def _entry(case, profile, *, started_at=None, ended_at=None, **kwargs):
    """Create a TimeEntry with safe defaults for tests.

    If neither timestamp is supplied the entry is "running" from now.
    If only ``ended_at`` is supplied we pick a ``started_at`` 30 minutes
    earlier so the ``end_after_start`` check constraint is satisfied.
    """
    if started_at is None:
        if ended_at is None:
            started_at = timezone.now()
        else:
            started_at = ended_at - timedelta(minutes=30)
    return TimeEntry.objects.create(
        org=case.org,
        case=case,
        profile=profile,
        started_at=started_at,
        ended_at=ended_at,
        **kwargs,
    )


@pytest.mark.django_db
class TestModelConstraints:
    def test_one_active_timer_per_profile(self, admin_user, admin_profile, org_a):
        case = _make_case(org_a, admin_user)
        TimeEntry.objects.create(
            org=org_a, case=case, profile=admin_profile, started_at=timezone.now()
        )
        with pytest.raises(IntegrityError):
            TimeEntry.objects.create(
                org=org_a,
                case=case,
                profile=admin_profile,
                started_at=timezone.now(),
            )

    def test_two_running_timers_for_different_profiles_ok(
        self,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        TimeEntry.objects.create(
            org=org_a, case=case, profile=admin_profile, started_at=timezone.now()
        )
        # Different profile, should NOT collide.
        TimeEntry.objects.create(
            org=org_a, case=case, profile=user_profile, started_at=timezone.now()
        )
        assert TimeEntry.objects.filter(case=case, ended_at__isnull=True).count() == 2

    def test_save_recomputes_duration_minutes(self, admin_user, admin_profile, org_a):
        case = _make_case(org_a, admin_user)
        start = timezone.now() - timedelta(minutes=45)
        end = timezone.now() - timedelta(minutes=15)
        entry = TimeEntry.objects.create(
            org=org_a,
            case=case,
            profile=admin_profile,
            started_at=start,
            ended_at=end,
        )
        # 30-minute window.
        assert 29 <= entry.duration_minutes <= 31


@pytest.mark.django_db
class TestListEndpoint:
    def test_admin_sees_all_org_entries(
        self,
        admin_client,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        _entry(case, admin_profile, ended_at=timezone.now())
        _entry(case, user_profile, ended_at=timezone.now())
        resp = admin_client.get(f"/api/cases/{case.id}/time-entries/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_non_admin_sees_only_own_entries(
        self,
        user_client,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        _entry(case, admin_profile, ended_at=timezone.now())
        _entry(case, user_profile, ended_at=timezone.now())
        resp = user_client.get(f"/api/cases/{case.id}/time-entries/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["profile"]["id"] == str(user_profile.id)


@pytest.mark.django_db
class TestStartStop:
    def test_start_creates_running_entry(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        resp = admin_client.post(
            f"/api/cases/{case.id}/time-entries/start/",
            {"description": "Investigating"},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["ended_at"] is None
        assert body["description"] == "Investigating"

    def test_start_with_existing_running_returns_409(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        _entry(case, admin_profile)  # running
        resp = admin_client.post(
            f"/api/cases/{case.id}/time-entries/start/", {}, format="json"
        )
        assert resp.status_code == 409
        assert "running" in resp.json()["detail"].lower()

    def test_stop_finalizes_duration(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=20),
        )
        resp = admin_client.post(
            f"/api/time-entries/{entry.id}/stop/", {}, format="json"
        )
        assert resp.status_code == 200, resp.content
        entry.refresh_from_db()
        assert entry.ended_at is not None
        assert entry.duration_minutes >= 19

    def test_stop_already_stopped_returns_400(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=1),
            ended_at=timezone.now(),
        )
        resp = admin_client.post(
            f"/api/time-entries/{entry.id}/stop/", {}, format="json"
        )
        assert resp.status_code == 400

    def test_stop_for_other_profile_forbidden(
        self,
        user_client,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        # admin_profile owns the timer; regular_user (non-admin) tries to stop it.
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=5),
        )
        resp = user_client.post(
            f"/api/time-entries/{entry.id}/stop/", {}, format="json"
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestManualEntry:
    def test_creates_with_full_window_and_emits_activity(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        start = timezone.now() - timedelta(hours=2)
        end = timezone.now() - timedelta(hours=1)
        resp = admin_client.post(
            f"/api/cases/{case.id}/time-entries/",
            {
                "started_at": start.isoformat(),
                "ended_at": end.isoformat(),
                "description": "Backfilled",
                "billable": True,
                "hourly_rate": "120.00",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert (
            Activity.objects.filter(
                entity_type="Case", entity_id=case.id, action="TIME_LOGGED"
            ).count()
            == 1
        )

    def test_end_before_start_rejected(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user)
        start = timezone.now()
        end = start - timedelta(minutes=10)
        resp = admin_client.post(
            f"/api/cases/{case.id}/time-entries/",
            {"started_at": start.isoformat(), "ended_at": end.isoformat()},
            format="json",
        )
        assert resp.status_code == 400

    def test_negative_rate_rejected(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user)
        start = timezone.now() - timedelta(hours=1)
        end = timezone.now()
        resp = admin_client.post(
            f"/api/cases/{case.id}/time-entries/",
            {
                "started_at": start.isoformat(),
                "ended_at": end.isoformat(),
                "hourly_rate": "-1.00",
            },
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestDetail:
    def test_owner_can_edit(self, admin_client, admin_user, admin_profile, org_a):
        case = _make_case(org_a, admin_user)
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=1),
            ended_at=timezone.now(),
            description="raw",
        )
        resp = admin_client.put(
            f"/api/time-entries/{entry.id}/",
            {"description": "edited", "billable": True},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        entry.refresh_from_db()
        assert entry.description == "edited"
        assert entry.billable is True

    def test_non_owner_non_admin_forbidden(
        self,
        user_client,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=15),
            ended_at=timezone.now(),
        )
        resp = user_client.put(
            f"/api/time-entries/{entry.id}/",
            {"description": "stolen"},
            format="json",
        )
        assert resp.status_code == 403

    def test_delete_invoiced_entry_rejected(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        account = Account.objects.create(name="ACME", org=org_a)
        invoice = Invoice.objects.create(
            invoice_title="Test",
            account=account,
            currency="USD",
            status="Draft",
            org=org_a,
        )
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=1),
            ended_at=timezone.now(),
            billable=True,
            hourly_rate=Decimal("100.00"),
        )
        TimeEntry.objects.filter(id=entry.id).update(invoice=invoice)
        resp = admin_client.delete(f"/api/time-entries/{entry.id}/")
        assert resp.status_code == 400


@pytest.mark.django_db
class TestTimesheetEndpoint:
    def test_default_window_returns_seven_days(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=30),
            ended_at=timezone.now(),
        )
        resp = admin_client.get("/api/time-entries/timesheet/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert len(body["days"]) == 7
        assert body["total_minutes"] >= 29

    def test_other_profile_blocked_for_non_admin(
        self,
        user_client,
        admin_profile,
    ):
        resp = user_client.get(
            f"/api/time-entries/timesheet/?profile={admin_profile.id}"
        )
        assert resp.status_code == 403

    def test_entry_expands_case_and_names_profile(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        case = _make_case(org_a, admin_user, name="SSO login loop")
        _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=45),
            ended_at=timezone.now(),
            billable=True,
            hourly_rate=Decimal("145.00"),
        )
        body = admin_client.get("/api/time-entries/timesheet/").json()

        # The envelope names whose week this is, for the page header.
        assert body["profile"] == {
            "id": str(admin_profile.id),
            "name": admin_profile.user.name,
        }

        entries = [e for d in body["days"] for e in d["entries"]]
        assert len(entries) == 1
        entry = entries[0]
        # case is expanded to {id, name}, not the bare id the serializer emits,
        # so the page can show the ticket name and link to it.
        assert entry["case"] == {"id": str(case.id), "name": "SSO login loop"}
        # An unbilled entry carries invoice: null, so the page never links to an
        # invoice that isn't there.
        assert entry["invoice"] is None
        assert entry["is_running"] is False

    def test_billed_entry_links_its_invoice(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        case = _make_case(org_a, admin_user)
        account = Account.objects.create(name="ACME", org=org_a)
        invoice = Invoice.objects.create(
            invoice_title="Time for ACME",
            account=account,
            currency="USD",
            status="Draft",
            org=org_a,
        )
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=1),
            ended_at=timezone.now(),
            billable=True,
            hourly_rate=Decimal("100.00"),
        )
        TimeEntry.objects.filter(id=entry.id).update(invoice=invoice)

        body = admin_client.get("/api/time-entries/timesheet/").json()
        entries = [e for d in body["days"] for e in d["entries"]]
        assert len(entries) == 1
        # A billed entry expands to {id, invoice_number} so the row links to the
        # invoice instead of offering to bill the hour a second time.
        assert entries[0]["invoice"] == {
            "id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
        }


@pytest.mark.django_db
class TestTimeSummaryEndpoint:
    def test_summary_shape(
        self,
        admin_client,
        admin_user,
        admin_profile,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user)
        _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=60),
            ended_at=timezone.now() - timedelta(minutes=30),
            billable=True,
        )
        _entry(
            case,
            user_profile,
            started_at=timezone.now() - timedelta(minutes=20),
            ended_at=timezone.now(),
        )
        resp = admin_client.get(f"/api/cases/{case.id}/time-summary/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["total_minutes"] >= 49
        assert body["billable_minutes"] >= 29
        assert len(body["by_profile"]) == 2


@pytest.mark.django_db
class TestInvoiceFromTimeEntries:
    def test_creates_draft_invoice(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="ACME", org=org_a)
        case = _make_case(org_a, admin_user, account=account)
        e1 = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=2),
            ended_at=timezone.now() - timedelta(hours=1),
            billable=True,
            hourly_rate=Decimal("100.00"),
            currency="USD",
        )
        e2 = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=4),
            ended_at=timezone.now() - timedelta(hours=3, minutes=30),
            billable=True,
            hourly_rate=Decimal("100.00"),
            currency="USD",
        )
        resp = admin_client.post(
            "/api/invoices/from-time-entries/",
            {"account_id": str(account.id), "entry_ids": [str(e1.id), str(e2.id)]},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["currency"] == "USD"
        assert body["line_count"] == 2
        invoice = Invoice.objects.get(id=body["invoice_id"])
        assert invoice.status == "Draft"
        assert InvoiceLineItem.objects.filter(invoice=invoice).count() == 2
        e1.refresh_from_db()
        e2.refresh_from_db()
        assert e1.invoice_id == invoice.id
        assert e2.invoice_id == invoice.id

    def test_mixed_currency_rejected(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="ACME", org=org_a)
        case = _make_case(org_a, admin_user, account=account)
        e1 = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=2),
            ended_at=timezone.now() - timedelta(hours=1),
            billable=True,
            hourly_rate=Decimal("100"),
            currency="USD",
        )
        e2 = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=4),
            ended_at=timezone.now() - timedelta(hours=3),
            billable=True,
            hourly_rate=Decimal("90"),
            currency="EUR",
        )
        resp = admin_client.post(
            "/api/invoices/from-time-entries/",
            {"account_id": str(account.id), "entry_ids": [str(e1.id), str(e2.id)]},
            format="json",
        )
        assert resp.status_code == 400
        assert "currenc" in resp.json()["message"].lower()

    def test_non_billable_rejected(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        account = Account.objects.create(name="ACME", org=org_a)
        case = _make_case(org_a, admin_user, account=account)
        e1 = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(hours=2),
            ended_at=timezone.now() - timedelta(hours=1),
            billable=False,
            hourly_rate=Decimal("100"),
        )
        resp = admin_client.post(
            "/api/invoices/from-time-entries/",
            {"account_id": str(account.id), "entry_ids": [str(e1.id)]},
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestSignalAndAutoStop:
    def test_running_entry_does_not_emit_time_logged(
        self, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        _entry(case, admin_profile)  # running
        assert (
            Activity.objects.filter(entity_id=case.id, action="TIME_LOGGED").count()
            == 0
        )

    def test_stop_emits_time_logged(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user)
        entry = _entry(
            case,
            admin_profile,
            started_at=timezone.now() - timedelta(minutes=10),
        )
        admin_client.post(f"/api/time-entries/{entry.id}/stop/", {}, format="json")
        assert (
            Activity.objects.filter(entity_id=case.id, action="TIME_LOGGED").count()
            == 1
        )

    def test_auto_stop_kills_stale_timer(self, admin_user, admin_profile, org_a):
        case = _make_case(org_a, admin_user)
        old_start = timezone.now() - timedelta(hours=15)
        entry = TimeEntry.objects.create(
            org=org_a, case=case, profile=admin_profile, started_at=old_start
        )
        stopped = auto_stop_stale_timers(threshold_hours=12)
        assert stopped == 1
        entry.refresh_from_db()
        assert entry.ended_at is not None
        assert entry.auto_stopped is True

    def test_auto_stop_leaves_recent_timer(self, admin_user, admin_profile, org_a):
        case = _make_case(org_a, admin_user)
        recent_start = timezone.now() - timedelta(hours=2)
        entry = TimeEntry.objects.create(
            org=org_a, case=case, profile=admin_profile, started_at=recent_start
        )
        stopped = auto_stop_stale_timers(threshold_hours=12)
        assert stopped == 0
        entry.refresh_from_db()
        assert entry.ended_at is None
        assert entry.auto_stopped is False
