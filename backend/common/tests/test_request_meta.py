"""`client_ip` has to return an IP or nothing, never a string that merely
looks like one, and never an address the caller chose.

`WebFormSubmission.submitted_ip` is a GenericIPAddressField, which maps to a
Postgres `inet` column. Django does not run field validators on `save()`, so an
unvalidated header value surfaces as a 500 from the database rather than as a
clean rejection. That is the whole reason this helper validates.
"""

import hmac
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.test import RequestFactory

from common.request_meta import client_ip, referer, user_agent


def _request(**meta):
    return RequestFactory().post("/api/public/forms/x/y/submit/", **meta)


class TestClientIp:
    """Only the entries our own proxies appended are believed. See
    `common.request_meta.client_ip`."""

    def test_with_no_proxy_configured_the_header_is_ignored(self):
        request = _request(
            HTTP_X_FORWARDED_FOR="203.0.113.50, 70.41.3.18", REMOTE_ADDR="10.0.0.1"
        )
        assert client_ip(request) == "10.0.0.1"

    def test_behind_one_proxy_the_entry_it_appended_is_used(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        request = _request(
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.50", REMOTE_ADDR="127.0.0.1"
        )
        assert client_ip(request) == "203.0.113.50"

    def test_behind_two_proxies_the_outer_ones_entry_is_used(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}
        request = _request(
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.50, 10.0.0.2",
            REMOTE_ADDR="127.0.0.1",
        )
        assert client_ip(request) == "203.0.113.50"

    def test_a_short_header_uses_its_leftmost_entry(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}
        request = _request(HTTP_X_FORWARDED_FOR="203.0.113.50", REMOTE_ADDR="10.0.0.2")
        assert client_ip(request) == "203.0.113.50"

    def test_behind_a_proxy_with_no_header_the_peer_is_used(self, settings):
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        assert client_ip(_request(REMOTE_ADDR="10.0.0.1")) == "10.0.0.1"

    def test_accepts_ipv6(self):
        assert client_ip(_request(REMOTE_ADDR="2001:db8::1")) == "2001:db8::1"

    def test_a_junk_trusted_entry_is_none_not_a_fallback(self, settings):
        """Falling back to another entry would let the caller's text through."""
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        request = _request(
            HTTP_X_FORWARDED_FOR="203.0.113.50, not-an-ip", REMOTE_ADDR="10.0.0.1"
        )
        assert client_ip(request) is None

    def test_returns_none_when_nothing_validates(self):
        request = _request(HTTP_X_FORWARDED_FOR="drop table students", REMOTE_ADDR="")
        assert client_ip(request) is None

    def test_returns_none_when_there_is_no_header_at_all(self):
        request = RequestFactory().post("/x/")
        request.META.pop("REMOTE_ADDR", None)
        assert client_ip(request) is None

    def test_a_sql_injection_payload_never_reaches_the_caller(self, settings):
        """The return value is written straight into an `inet` column, so a
        non-IP getting through here is a 500 at best."""
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        request = _request(
            HTTP_X_FORWARDED_FOR="1.1.1.1'; DROP TABLE lead; --", REMOTE_ADDR=""
        )
        assert client_ip(request) is None


class TestReferer:
    def test_returns_the_header(self):
        request = _request(HTTP_REFERER="https://example.com/contact")
        assert referer(request) == "https://example.com/contact"

    def test_truncates_to_the_column_width(self):
        request = _request(HTTP_REFERER="https://example.com/" + "a" * 1000)
        assert len(referer(request)) == 512

    def test_missing_header_is_an_empty_string_not_none(self):
        assert referer(_request()) == ""


SECRET = "r" * 48
RELAYED = {
    "HTTP_X_BOTTLECRM_RELAY_SECRET": SECRET,
    "HTTP_X_BOTTLECRM_CLIENT_IP": "203.0.113.50",
    "REMOTE_ADDR": "10.0.0.1",
}


class TestRelayedClientIp:
    """A SvelteKit relay holding `RELAY_SECRET` is believed about its visitor;
    nothing else is. See `common.request_meta._relayed_ip`."""

    def test_the_secret_and_a_valid_ip_give_the_visitor(self, settings):
        settings.RELAY_SECRET = SECRET
        assert client_ip(_request(**RELAYED)) == "203.0.113.50"

    def test_accepts_an_ipv6_visitor(self, settings):
        settings.RELAY_SECRET = SECRET
        request = _request(**{**RELAYED, "HTTP_X_BOTTLECRM_CLIENT_IP": "2001:db8::7"})
        assert client_ip(request) == "2001:db8::7"

    def test_a_wrong_secret_ignores_the_visitor_header(self, settings):
        settings.RELAY_SECRET = SECRET
        request = _request(
            **{**RELAYED, "HTTP_X_BOTTLECRM_RELAY_SECRET": SECRET[:-1] + "x"}
        )
        assert client_ip(request) == "10.0.0.1"

    def test_a_missing_secret_ignores_the_visitor_header(self, settings):
        settings.RELAY_SECRET = SECRET
        request = _request(
            HTTP_X_BOTTLECRM_CLIENT_IP="203.0.113.50", REMOTE_ADDR="10.0.0.1"
        )
        assert client_ip(request) == "10.0.0.1"

    def test_unset_secret_turns_the_relay_off(self, settings):
        settings.RELAY_SECRET = ""
        request = _request(**{**RELAYED, "HTTP_X_BOTTLECRM_RELAY_SECRET": ""})
        assert client_ip(request) == "10.0.0.1"

    def test_a_short_secret_never_matches_even_when_sent(self, settings):
        """Under 32 characters is off, so a guessable value is not a key."""
        short = "r" * 31
        settings.RELAY_SECRET = short
        request = _request(**{**RELAYED, "HTTP_X_BOTTLECRM_RELAY_SECRET": short})
        assert client_ip(request) == "10.0.0.1"

    def test_a_secret_at_the_floor_matches(self, settings):
        floor = "r" * 32
        settings.RELAY_SECRET = floor
        request = _request(**{**RELAYED, "HTTP_X_BOTTLECRM_RELAY_SECRET": floor})
        assert client_ip(request) == "203.0.113.50"

    def test_an_invalid_visitor_ip_falls_back_to_the_socket_rule(self, settings):
        settings.RELAY_SECRET = SECRET
        request = _request(
            **{**RELAYED, "HTTP_X_BOTTLECRM_CLIENT_IP": "1.1.1.1'; DROP TABLE lead"}
        )
        assert client_ip(request) == "10.0.0.1"

    def test_an_invalid_visitor_ip_falls_back_to_num_proxies(self, settings):
        settings.RELAY_SECRET = SECRET
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        request = _request(
            **{
                **RELAYED,
                "HTTP_X_BOTTLECRM_CLIENT_IP": "not-an-ip",
                "HTTP_X_FORWARDED_FOR": "6.6.6.6, 198.51.100.4",
                "REMOTE_ADDR": "127.0.0.1",
            }
        )
        assert client_ip(request) == "198.51.100.4"

    def test_a_forged_forwarded_for_is_still_ignored(self, settings):
        """Without the secret, neither the relay header nor XFF picks the IP."""
        settings.RELAY_SECRET = SECRET
        request = _request(
            HTTP_X_FORWARDED_FOR="6.6.6.6",
            HTTP_X_BOTTLECRM_CLIENT_IP="6.6.6.7",
            REMOTE_ADDR="10.0.0.1",
        )
        assert client_ip(request) == "10.0.0.1"

    def test_the_comparison_is_constant_time(self, settings):
        settings.RELAY_SECRET = SECRET
        with mock.patch(
            "common.request_meta.hmac.compare_digest", wraps=hmac.compare_digest
        ) as compare:
            client_ip(_request(**RELAYED))
        compare.assert_called_once_with(SECRET.encode(), SECRET.encode())


BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/142.0 Safari/537.36"


class TestRelayedUserAgent:
    """The relay's own user agent is its HTTP client (`axios/1.20.0`, `node`),
    which is what every web sign-in row recorded before. A relay holding
    `RELAY_SECRET` is believed about its visitor's; nothing else is."""

    def _relayed(self, **extra):
        return _request(
            **{
                **RELAYED,
                "HTTP_USER_AGENT": "axios/1.20.0",
                "HTTP_X_BOTTLECRM_USER_AGENT": BROWSER,
                **extra,
            }
        )

    def test_the_secret_gives_the_visitor_user_agent(self, settings):
        settings.RELAY_SECRET = SECRET
        assert user_agent(self._relayed()) == BROWSER

    def test_a_wrong_secret_gives_the_callers_own(self, settings):
        settings.RELAY_SECRET = SECRET
        request = self._relayed(HTTP_X_BOTTLECRM_RELAY_SECRET=SECRET[:-1] + "x")
        assert user_agent(request) == "axios/1.20.0"

    def test_without_the_secret_the_header_is_ignored(self, settings):
        settings.RELAY_SECRET = SECRET
        request = _request(
            HTTP_USER_AGENT="curl/8.0", HTTP_X_BOTTLECRM_USER_AGENT=BROWSER
        )
        assert user_agent(request) == "curl/8.0"

    def test_unset_secret_turns_the_relay_off(self, settings):
        settings.RELAY_SECRET = ""
        request = self._relayed(HTTP_X_BOTTLECRM_RELAY_SECRET="")
        assert user_agent(request) == "axios/1.20.0"

    def test_a_trusted_relay_that_names_no_user_agent_gives_its_own(self, settings):
        """A relay from before the header existed still sends the secret."""
        settings.RELAY_SECRET = SECRET
        request = _request(**{**RELAYED, "HTTP_USER_AGENT": "axios/1.20.0"})
        assert user_agent(request) == "axios/1.20.0"

    def test_a_visitor_who_sent_none_is_recorded_as_none(self, settings):
        settings.RELAY_SECRET = SECRET
        assert user_agent(self._relayed(HTTP_X_BOTTLECRM_USER_AGENT="")) == ""

    def test_no_user_agent_at_all_is_an_empty_string(self, settings):
        settings.RELAY_SECRET = ""
        assert user_agent(_request()) == ""


def _import_settings(relay_secret):
    """Import ``crm.settings`` in a subprocess, as test_signing_key_strength
    does: the guard runs at import time."""
    env = {k: v for k, v in os.environ.items() if k not in ("RELAY_SECRET", "ENV_TYPE")}
    env["RELAY_SECRET"] = relay_secret
    env["ENV_TYPE"] = "dev"
    return subprocess.run(
        [sys.executable, "-c", "import crm.settings"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )


class TestRelaySecretSetting:
    def test_a_short_secret_refuses_to_start(self):
        result = _import_settings("r" * 31)
        assert result.returncode != 0
        assert "at least 32" in result.stderr
        assert "r" * 31 not in result.stderr

    def test_a_32_character_secret_is_accepted(self):
        result = _import_settings("r" * 32)
        assert result.returncode == 0, result.stderr

    def test_unset_is_accepted(self):
        result = _import_settings("")
        assert result.returncode == 0, result.stderr


def test_sentry_scrubs_the_relay_secret_header():
    """Production sends every request header to Sentry (send_default_pii)."""
    env = {
        "AWS_BUCKET_NAME": "test-bucket",
        "AWS_ACCESS_KEY_ID": "test-key-id",
        "AWS_SECRET_ACCESS_KEY": "test-secret-value",
        "AWS_SES_REGION_NAME": "ap-south-1",
        "AWS_SES_REGION_ENDPOINT": "email.ap-south-1.amazonaws.com",
        "SENTRY_DSN": "",
    }
    with mock.patch.dict(os.environ, env), mock.patch("sentry_sdk.init") as init:
        importlib.reload(importlib.import_module("crm.server_settings"))
    scrubber = init.call_args.kwargs["event_scrubber"]
    event = {
        "request": {
            "headers": {
                "X-Bottlecrm-Relay-Secret": SECRET,
                "X-Bottlecrm-Client-Ip": "203.0.113.50",
            }
        }
    }

    scrubber.scrub_event(event)

    assert event["request"]["headers"]["X-Bottlecrm-Relay-Secret"] != SECRET
    assert event["request"]["headers"]["X-Bottlecrm-Client-Ip"] == "203.0.113.50"
