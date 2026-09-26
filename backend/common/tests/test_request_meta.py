"""`client_ip` has to return an IP or nothing, never a string that merely
looks like one, and never an address the caller chose.

`WebFormSubmission.submitted_ip` is a GenericIPAddressField, which maps to a
Postgres `inet` column. Django does not run field validators on `save()`, so an
unvalidated header value surfaces as a 500 from the database rather than as a
clean rejection. That is the whole reason this helper validates.
"""

from django.test import RequestFactory

from common.request_meta import client_ip, referer


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
