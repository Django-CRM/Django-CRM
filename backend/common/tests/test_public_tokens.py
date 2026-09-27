"""Public-link tokens stay out of the application log and out of Sentry.

The calendar feed, a satisfaction survey, and an invoice's or estimate's portal
link each carry their credential as a path segment under `/api/public/`.
Django's `django.request` logger writes `Not Found: <path>` and
`Too Many Requests: <path>`, and a Sentry event records the path in several
places, so both are scrubbed by `common.public_tokens`.
"""

import logging

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from common.models import CalendarFeedToken
from common.public_tokens import redact, scrub_public_tokens
from common.views.calendar_feed_views import CalendarFeedTokenThrottle

TOKEN = "SECRETsecretSECRETsecret0123456789"

# Every public route shape whose path segment is a credential.
TOKEN_PATHS = [
    f"/api/public/calendar/{TOKEN}.ics",
    f"/api/public/csat/{TOKEN}/",
    f"/api/public/invoice/{TOKEN}/",
    f"/api/public/invoice/{TOKEN}/pdf/",
    f"/api/public/estimate/{TOKEN}/",
    f"/api/public/estimate/{TOKEN}/pdf/",
    f"/api/public/estimate/{TOKEN}/accept/",
    f"/api/public/estimate/{TOKEN}/decline/",
]


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


class TestSentryScrubber:
    @pytest.mark.parametrize("path", TOKEN_PATHS)
    def test_redacts_every_place_a_url_can_appear(self, path):
        event = {
            "request": {"url": f"https://api.example.com{path}", "method": "GET"},
            "transaction": path,
            "breadcrumbs": {"values": [{"message": f"Not Found: {path}"}]},
            "spans": [{"description": f"GET {path}", "data": {"url": path}}],
            "extra": {"tuple": (path,)},
        }
        scrubbed = scrub_public_tokens(event, {})
        assert TOKEN not in repr(scrubbed)
        prefix = path[: path.index(TOKEN)]
        assert scrubbed["request"]["url"].startswith(
            f"https://api.example.com{prefix}[Filtered]"
        )
        assert scrubbed["request"]["method"] == "GET"

    def test_keeps_what_follows_the_token(self):
        assert (
            redact(f"/api/public/estimate/{TOKEN}/accept/?x=1")
            == "/api/public/estimate/[Filtered]/accept/?x=1"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/api/tasks/?x=1",
            "/api/public/help/acme/",  # an org's public help-center slug
            "/api/public/forms/9d3e/5a1b/embed/",  # ids, not credentials
            "/api/invoices/abc/",
        ],
    )
    def test_leaves_other_paths_alone(self, path):
        event = {"request": {"url": f"https://api.example.com{path}"}}
        assert scrub_public_tokens(event, {}) == event


@pytest.mark.django_db
class TestApplicationLog:
    def _messages(self, caplog):
        return [record.getMessage() for record in caplog.records]

    @pytest.mark.parametrize("path", TOKEN_PATHS)
    def test_a_refused_request_names_no_token(self, caplog, path):
        """Unknown token: a 404, a 400 for the survey, a 405 for a GET to a
        POST-only route. Each is logged with its path, which is the point."""
        caplog.set_level(logging.WARNING, logger="django.request")
        response = APIClient().get(path)
        assert response.status_code in (400, 404, 405)
        messages = self._messages(caplog)
        prefix = path[: path.index(TOKEN)]
        assert any(f": {prefix}[Filtered]" in m for m in messages), messages
        assert not any(TOKEN in m for m in messages)

    def test_a_disabled_feed_names_no_token(self, admin_profile, caplog):
        raw, _ = CalendarFeedToken.issue(admin_profile)
        CalendarFeedToken.objects.all().delete()  # the member turns the feed off
        caplog.set_level(logging.WARNING, logger="django.request")
        assert APIClient().get(f"/api/public/calendar/{raw}.ics").status_code == 404
        messages = self._messages(caplog)
        assert "Not Found: /api/public/calendar/[Filtered]" in messages
        assert not any(raw in m for m in messages)

    def test_a_throttled_live_feed_names_no_token(
        self, admin_profile, caplog, monkeypatch
    ):
        monkeypatch.setattr(
            CalendarFeedTokenThrottle,
            "THROTTLE_RATES",
            {"calendar_feed_token": "1/hour"},
        )
        raw, _ = CalendarFeedToken.issue(admin_profile)
        client = APIClient()
        assert client.get(f"/api/public/calendar/{raw}.ics").status_code == 200
        caplog.set_level(logging.WARNING, logger="django.request")
        assert client.get(f"/api/public/calendar/{raw}.ics").status_code == 429
        messages = self._messages(caplog)
        assert "Too Many Requests: /api/public/calendar/[Filtered]" in messages
        assert not any(raw in m for m in messages)

    def test_other_paths_are_logged_as_they_were(self, caplog):
        caplog.set_level(logging.WARNING, logger="django.request")
        APIClient().get("/api/public/help/no-such-org/")
        assert "Not Found: /api/public/help/no-such-org/" in self._messages(caplog)
