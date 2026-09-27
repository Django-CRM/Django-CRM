"""The per-user task calendar feed (G14): management API and the public feed.

What these pin:

* only the token's hash is stored, and the URL is shown once;
* regenerating or disabling kills the old URL, and every kind of miss is the
  same 404 (malformed, unknown, disabled, regenerated, inactive member, user or
  org, removed membership);
* the feed shows the member's own tasks (created or assigned), for admins too,
  never more than `visible_tasks_qs` at fetch time, in their org only, open and
  dated, in the window, capped to the tasks nearest today;
* the URL is built from `DOMAIN_NAME`, never from the host that asked;
* an event carries the title, the priority and an app link, and nothing else;
* no personal access token and not the org API key can reach the management
  endpoint, while a signed-in session can.
"""

import datetime as dt
import hashlib
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account
from common.models import CalendarFeedToken, Org, PersonalAccessToken, Profile, User
from common.testing import clear_rls_context, rls_org
from common.views import calendar_feed_views
from common.views.calendar_feed_views import (
    CalendarFeedIPThrottle,
    CalendarFeedTokenThrottle,
    PublicCalendarFeedView,
)
from contacts.models import Contact
from tasks.models import Task

MANAGE = "/api/profile/calendar-feed/"
APP = "https://app.example.com"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def anon():
    return APIClient()


def _task(org, title, due, status="New", priority="Medium", creator=None, to=()):
    with rls_org(org):
        task = Task.objects.create(
            title=title, status=status, priority=priority, due_date=due, org=org
        )
        if creator is not None:
            Task.objects.filter(pk=task.pk).update(created_by=creator)
        for profile in to:
            task.assigned_to.add(profile)
    return task


def _enable(client):
    resp = client.post(MANAGE)
    assert resp.status_code == 201, resp.content
    return resp.json()["url"]


def _path(url):
    return urlparse(url).path


def _raw(url):
    return _path(url).rsplit("/", 1)[1].removesuffix(".ics")


def _today():
    return timezone.localdate()


def _feed(anon, url, **extra):
    resp = anon.get(_path(url), **extra)
    assert resp.status_code == 200, resp.content
    return resp.content.decode("utf-8").replace("\r\n ", "")


def _pat_client(profile):
    raw, _ = PersonalAccessToken.generate(profile=profile, name="cli")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _key_client(org):
    client = APIClient()
    client.credentials(HTTP_TOKEN=org.api_key)
    return client


class TestManagement:
    def test_disabled_by_default(self, user_client):
        body = user_client.get(MANAGE).json()
        assert body["enabled"] is False
        assert body["created_at"] is None
        assert "url" not in body

    def test_enable_shows_the_url_once_and_stores_only_the_hash(
        self, user_client, user_profile
    ):
        url = _enable(user_client)
        raw = _raw(url)
        assert url.startswith("https://api.example.com/api/public/calendar/")
        assert url.endswith(".ics")
        assert raw.startswith("bcrm_cal_") and len(raw) > 40

        row = CalendarFeedToken.objects.get(profile=user_profile)
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert row.org_id == user_profile.org_id
        assert raw not in repr(CalendarFeedToken.objects.values().get())

        again = user_client.get(MANAGE).json()
        assert again["enabled"] is True and again["created_at"]
        assert "url" not in again
        assert raw not in str(again)

    def test_regenerate_kills_the_old_url(self, user_client, user_profile, anon):
        old = _enable(user_client)
        new = _enable(user_client)
        assert old != new
        assert anon.get(_path(old)).status_code == 404
        assert anon.get(_path(new)).status_code == 200
        assert CalendarFeedToken.objects.filter(profile=user_profile).count() == 1

    def test_disable_kills_the_url_and_is_idempotent(self, user_client, anon):
        url = _enable(user_client)
        assert user_client.delete(MANAGE).json()["enabled"] is False
        assert user_client.delete(MANAGE).status_code == 200
        assert user_client.get(MANAGE).json()["enabled"] is False
        assert anon.get(_path(url)).status_code == 404

    def test_each_member_manages_only_their_own(
        self, user_client, admin_client, user_profile, admin_profile, anon
    ):
        mine = _enable(user_client)
        assert admin_client.get(MANAGE).json()["enabled"] is False
        admin_client.delete(MANAGE)
        assert anon.get(_path(mine)).status_code == 200
        assert CalendarFeedToken.objects.filter(profile=user_profile).exists()

    def test_anonymous_is_refused(self, anon):
        assert anon.get(MANAGE).status_code in (401, 403)
        assert anon.post(MANAGE).status_code in (401, 403)
        assert not CalendarFeedToken.objects.exists()


class TestCredentialsCannotManageTheFeed:
    """The URL is a credential, so no credential may read or mint it.

    Each refusal is paired with the signed-in session succeeding, so the gate is
    proved to open as well as to close.
    """

    def test_session_can(self, admin_client):
        assert admin_client.get(MANAGE).status_code == 200
        assert admin_client.post(MANAGE).status_code == 201
        assert admin_client.delete(MANAGE).status_code == 200

    def test_personal_access_token_cannot(self, admin_profile):
        client = _pat_client(admin_profile)
        assert client.get(MANAGE).status_code == 403
        assert client.post(MANAGE).status_code == 403
        assert not CalendarFeedToken.objects.exists()

    def test_personal_access_token_cannot_disable(self, admin_client, admin_profile):
        _enable(admin_client)
        assert _pat_client(admin_profile).delete(MANAGE).status_code == 403
        assert CalendarFeedToken.objects.filter(profile=admin_profile).exists()

    def test_org_api_key_cannot(self, org_a, admin_profile):
        client = _key_client(org_a)
        assert client.get(MANAGE).status_code == 403
        assert client.post(MANAGE).status_code == 403
        assert not CalendarFeedToken.objects.exists()


class TestEveryMissIsTheSame404:
    def _misses(self, user_client):
        first = _enable(user_client)
        second = _enable(user_client)  # `first` is now a regenerated URL
        return [
            "/api/public/calendar/x.ics",
            "/api/public/calendar/bcrm_cal_" + "A" * 43 + ".ics",
            _path(first),
        ], second

    def test_malformed_unknown_and_regenerated(self, user_client, anon):
        misses, _ = self._misses(user_client)
        responses = [anon.get(path) for path in misses]
        assert {r.status_code for r in responses} == {404}
        assert len({r.content for r in responses}) == 1

    def test_disabled_answers_the_same_body(self, user_client, anon):
        unknown = anon.get("/api/public/calendar/nope.ics")
        url = _enable(user_client)
        user_client.delete(MANAGE)
        disabled = anon.get(_path(url))
        assert (disabled.status_code, disabled.content) == (404, unknown.content)

    @pytest.mark.parametrize("what", ["profile", "user", "org", "membership"])
    def test_an_inactive_or_removed_member_is_the_same_404(
        self, what, user_client, user_profile, anon
    ):
        url = _enable(user_client)
        assert anon.get(_path(url)).status_code == 200
        unknown = anon.get("/api/public/calendar/nope.ics")
        if what == "profile":
            Profile.objects.filter(pk=user_profile.pk).update(is_active=False)
        elif what == "user":
            User.objects.filter(pk=user_profile.user_id).update(is_active=False)
        elif what == "org":
            Org.objects.filter(pk=user_profile.org_id).update(is_active=False)
        else:
            user_profile.delete()
        resp = anon.get(_path(url))
        assert (resp.status_code, resp.content) == (404, unknown.content)

    def test_reactivation_brings_the_same_url_back(
        self, user_client, user_profile, anon
    ):
        """The check is live on every fetch rather than a one-way kill switch."""
        url = _enable(user_client)
        Profile.objects.filter(pk=user_profile.pk).update(is_active=False)
        assert anon.get(_path(url)).status_code == 404
        Profile.objects.filter(pk=user_profile.pk).update(is_active=True)
        assert anon.get(_path(url)).status_code == 200


class TestWhatTheFeedShows:
    @pytest.fixture(autouse=True)
    def _app_url(self, settings):
        settings.FRONTEND_URL = APP

    def test_member_sees_created_and_assigned_only(
        self, org_a, user_client, user_profile, regular_user, admin_user, anon
    ):
        soon = _today() + dt.timedelta(days=3)
        _task(org_a, "Mine by creation", soon, creator=regular_user)
        _task(org_a, "Mine by assignment", soon, creator=admin_user, to=[user_profile])
        _task(org_a, "Someone else's", soon, creator=admin_user)
        body = _feed(anon, _enable(user_client))
        assert "SUMMARY:Mine by creation" in body
        assert "SUMMARY:Mine by assignment" in body
        assert "Someone else's" not in body

    def test_admin_sees_only_their_own_tasks(
        self, org_a, admin_client, admin_user, admin_profile, regular_user, anon
    ):
        """An admin may open every task, but the feed is theirs, not the org's."""
        _task(org_a, "Admin made", _today(), creator=admin_user)
        _task(org_a, "Admin handed", _today(), creator=regular_user, to=[admin_profile])
        _task(org_a, "A colleague's", _today(), creator=regular_user)
        body = _feed(anon, _enable(admin_client))
        assert "SUMMARY:Admin made" in body
        assert "SUMMARY:Admin handed" in body
        assert "A colleague's" not in body

    def test_a_task_assigned_twice_appears_once(
        self, org_a, admin_client, admin_user, admin_profile, user_profile, anon
    ):
        _task(
            org_a,
            "Shared",
            _today(),
            creator=admin_user,
            to=[admin_profile, user_profile],
        )
        assert _feed(anon, _enable(admin_client)).count("SUMMARY:Shared") == 1

    def test_demotion_changes_nothing_and_unassignment_applies_next_fetch(
        self, org_a, admin_client, admin_profile, regular_user, anon
    ):
        task = _task(
            org_a, "Handed over", _today(), creator=regular_user, to=[admin_profile]
        )
        _task(org_a, "Not yours", _today(), creator=regular_user)
        url = _enable(admin_client)
        admin_profile.role = "USER"
        admin_profile.save()
        body = _feed(anon, url)
        assert "Handed over" in body and "Not yours" not in body
        with rls_org(org_a):
            task.assigned_to.remove(admin_profile)
        assert "Handed over" not in _feed(anon, url)

    def test_another_orgs_tasks_never_appear(
        self, org_a, org_b, admin_client, admin_user, regular_user, anon
    ):
        """Same user, a membership in each org: each feed is its own org."""
        other = Profile.objects.create(
            user=regular_user, org=org_b, role="ADMIN", is_active=True
        )
        _task(org_a, "Org A task", _today(), creator=admin_user)
        _task(org_b, "Org B task", _today(), to=[other])
        body_a = _feed(anon, _enable(admin_client))
        assert "Org A task" in body_a and "Org B task" not in body_a

        raw, _ = CalendarFeedToken.issue(other)
        body_b = _feed(anon, f"/api/public/calendar/{raw}.ics")
        assert "Org B task" in body_b and "Org A task" not in body_b

    def test_completed_and_undated_are_left_out(
        self, org_a, admin_client, admin_user, anon
    ):
        _task(org_a, "Open one", _today(), creator=admin_user)
        _task(org_a, "Started one", _today(), status="In Progress", creator=admin_user)
        _task(org_a, "Done one", _today(), status="Completed", creator=admin_user)
        _task(org_a, "Someday", None, creator=admin_user)
        body = _feed(anon, _enable(admin_client))
        assert "Open one" in body and "Started one" in body
        assert "Done one" not in body and "Someday" not in body

    def test_window_is_org_local_today(
        self, org_a, admin_client, admin_user, anon, monkeypatch
    ):
        """12:00 UTC on 27 Sep is already 28 Sep in Kiritimati (UTC+14).

        A window computed from the server's UTC day would drop the last day at
        the far end, which is what the `+365` row checks.
        """
        org_a.timezone = "Pacific/Kiritimati"
        org_a.save()
        url = _enable(admin_client)
        fixed = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)
        monkeypatch.setattr(timezone, "now", lambda: fixed)
        local_today = fixed.astimezone(ZoneInfo("Pacific/Kiritimati")).date()
        assert local_today == dt.date(2026, 9, 28)
        for days, title in [
            (-91, "Too old"),
            (-90, "Oldest kept"),
            (365, "Furthest kept"),
            (366, "Too far"),
        ]:
            _task(
                org_a, title, local_today + dt.timedelta(days=days), creator=admin_user
            )
        body = _feed(anon, url)
        assert "Oldest kept" in body and "Furthest kept" in body
        assert "Too old" not in body and "Too far" not in body

    def test_capped_and_ordered_by_due_date(
        self, org_a, admin_client, admin_user, anon, monkeypatch
    ):
        monkeypatch.setattr(calendar_feed_views, "MAX_EVENTS", 3)
        for n in (5, 1, 4, 2, 3):
            _task(
                org_a, f"Day {n}", _today() + dt.timedelta(days=n), creator=admin_user
            )
        body = _feed(anon, _enable(admin_client))
        assert body.count("BEGIN:VEVENT") == 3
        assert body.index("Day 1") < body.index("Day 2") < body.index("Day 3")
        assert "Day 4" not in body and "Day 5" not in body

    def test_the_cap_keeps_upcoming_before_overdue(
        self, org_a, admin_client, admin_user, anon, monkeypatch
    ):
        """Old overdue work must not push today and next week off the calendar."""
        monkeypatch.setattr(calendar_feed_views, "MAX_EVENTS", 4)
        for n in (80, 60, 40, 20, 2, 1):
            _task(
                org_a, f"Late {n}", _today() - dt.timedelta(days=n), creator=admin_user
            )
        for n in (0, 7):
            _task(
                org_a, f"Due {n}", _today() + dt.timedelta(days=n), creator=admin_user
            )
        body = _feed(anon, _enable(admin_client))
        assert body.count("BEGIN:VEVENT") == 4
        # Upcoming soonest first, then overdue most recent first.
        assert "Due 0" in body and "Due 7" in body
        assert "Late 1" in body and "Late 2" in body
        for dropped in ("Late 20", "Late 40", "Late 60", "Late 80"):
            assert dropped not in body

    def test_a_full_cap_of_upcoming_leaves_no_room_for_overdue(
        self, org_a, admin_client, admin_user, anon, monkeypatch
    ):
        monkeypatch.setattr(calendar_feed_views, "MAX_EVENTS", 2)
        _task(org_a, "Late 1", _today() - dt.timedelta(days=1), creator=admin_user)
        for n in (0, 1, 2):
            _task(
                org_a, f"Due {n}", _today() + dt.timedelta(days=n), creator=admin_user
            )
        body = _feed(anon, _enable(admin_client))
        assert body.count("BEGIN:VEVENT") == 2
        assert "Due 0" in body and "Due 1" in body
        assert "Due 2" not in body and "Late 1" not in body

    def test_the_url_ignores_the_host_that_asked(self, user_client, settings):
        """The web app asks from its server, as `http://backend:8000` in Docker.

        The URL a member pastes into a calendar app has to name the public
        origin, whatever host and scheme the request arrived on.
        """
        settings.ALLOWED_HOSTS = ["*"]
        settings.DOMAIN_NAME = "https://api.crm.example.org"
        resp = user_client.post(MANAGE, HTTP_HOST="backend:8000")
        assert resp.status_code == 201, resp.content
        url = resp.json()["url"]
        assert url.startswith("https://api.crm.example.org/api/public/calendar/")
        assert "backend" not in url

    def test_event_carries_title_priority_and_link_and_nothing_else(
        self, org_a, admin_client, admin_user, anon
    ):
        account = Account.objects.create(name="Umbrella Holdings", org=org_a)
        contact = Contact.objects.create(
            first_name="Wilhelmina", last_name="Quarrie", org=org_a
        )
        task = _task(
            org_a, "Quarterly review", _today(), priority="High", creator=admin_user
        )
        with rls_org(org_a):
            Task.objects.filter(pk=task.pk).update(
                description="Secret notes about pricing", account=account
            )
            task.contacts.add(contact)
        body = _feed(anon, _enable(admin_client))
        link = f"{APP}/tasks/{task.id}"
        assert "SUMMARY:Quarterly review\r\n" in body
        assert f"DESCRIPTION:Priority: High\\n{link}\r\n" in body
        assert f"URL:{link}\r\n" in body
        assert f"UID:task-{task.id}@bottlecrm\r\n" in body
        today = _today()
        assert f"DTSTART;VALUE=DATE:{today:%Y%m%d}\r\n" in body
        assert f"DTEND;VALUE=DATE:{today + dt.timedelta(days=1):%Y%m%d}\r\n" in body
        for leaked in ("Secret notes", "Umbrella", "Wilhelmina", "Quarrie"):
            assert leaked not in body

    def test_headers(self, admin_client, anon):
        url = _enable(admin_client)
        resp = anon.get(_path(url), HTTP_ACCEPT="text/calendar")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/calendar; charset=utf-8"
        assert resp["Cache-Control"] == "private, max-age=300"
        assert resp["X-Robots-Tag"] == "noindex"

    def test_a_calendar_accept_header_still_gets_the_json_404(self, anon):
        resp = anon.get("/api/public/calendar/nope.ics", HTTP_ACCEPT="text/calendar")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Not found"}

    def test_a_staff_credential_sent_along_changes_nothing(
        self, org_a, user_client, admin_user, anon
    ):
        """The feed answers for the token's member, never for the caller."""
        _task(org_a, "Admin only", _today(), creator=admin_user)
        url = _enable(user_client)
        staff = APIClient()
        staff.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-jwt")
        assert "Admin only" not in _feed(staff, url)

    def test_last_used_is_written_at_most_hourly(self, user_client, anon):
        url = _enable(user_client)
        _feed(anon, url)
        first = CalendarFeedToken.objects.get().last_used_at
        assert first is not None
        _feed(anon, url)
        assert CalendarFeedToken.objects.get().last_used_at == first
        CalendarFeedToken.objects.update(last_used_at=first - dt.timedelta(hours=2))
        _feed(anon, url)
        assert CalendarFeedToken.objects.get().last_used_at > first


class TestThrottle:
    def test_the_feed_is_throttled_and_takes_no_credential(self):
        assert PublicCalendarFeedView.throttle_classes == [
            CalendarFeedIPThrottle,
            CalendarFeedTokenThrottle,
        ]
        assert PublicCalendarFeedView.authentication_classes == []

    def test_per_token_limit(self, user_client, admin_client, anon, monkeypatch):
        monkeypatch.setattr(
            CalendarFeedTokenThrottle,
            "THROTTLE_RATES",
            {"calendar_feed_token": "2/hour"},
        )
        url = _enable(user_client)
        codes = [
            anon.get(_path(url), REMOTE_ADDR=f"198.51.100.{n}").status_code
            for n in range(1, 4)
        ]
        assert codes == [200, 200, 429]
        # Another member's feed has its own bucket.
        assert anon.get(_path(_enable(admin_client))).status_code == 200

    def test_per_address_limit(self, user_client, anon, monkeypatch):
        monkeypatch.setattr(
            CalendarFeedIPThrottle, "THROTTLE_RATES", {"calendar_feed_ip": "2/hour"}
        )
        url = _enable(user_client)
        codes = [anon.get("/api/public/calendar/nope.ics").status_code]
        codes.append(anon.get(_path(url)).status_code)
        codes.append(anon.get(_path(url)).status_code)
        assert codes == [404, 200, 429]
        other = anon.get(_path(url), REMOTE_ADDR="198.51.100.9")
        assert other.status_code == 200

    def test_the_raw_token_is_not_the_cache_key(self, user_client, anon):
        raw = _raw(_enable(user_client))
        request = type("R", (), {})()
        view = type("V", (), {"kwargs": {"token": raw}})()
        key = CalendarFeedTokenThrottle().get_cache_key(request, view)
        assert raw not in key


@pytest.mark.postgres_only
def test_feed_reads_under_the_tokens_org_context(org_a, admin_client, admin_user, anon):
    """Under a non-superuser role an empty context returns zero tasks, so a
    listed task proves the view set the context from the token's org."""
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    _task(org_a, "Visible under RLS", _today(), creator=admin_user)
    url = _enable(admin_client)
    clear_rls_context()
    assert "Visible under RLS" in _feed(anon, url)
