"""Turnstile verification, and specifically that it fails closed.

A captcha that passes when the verification service is unreachable stops
working exactly when someone is attacking it, because taking Cloudflare out of
the path becomes step one of the attack. Losing a lead during an outage is the
accepted cost, and it is recorded in the spec.
"""

import pytest
import requests

from webforms.captcha import verify
from webforms.models import WebForm


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def form(org_a):
    return WebForm.objects.create(
        name="Contact us",
        org=org_a,
        captcha_provider=WebForm.CAPTCHA_TURNSTILE,
        captcha_secret="secret-value",
    )


@pytest.mark.django_db
class TestVerify:
    def test_a_form_with_no_captcha_configured_always_passes(self, org_a):
        plain = WebForm.objects.create(name="Plain", org=org_a)
        assert verify(plain, token="", remote_ip=None) is True

    def test_a_successful_response_passes(self, form, monkeypatch):
        monkeypatch.setattr(
            "webforms.captcha.requests.post",
            lambda *a, **kw: _Response({"success": True}),
        )
        assert verify(form, token="tok", remote_ip="203.0.113.50") is True

    def test_an_unsuccessful_response_fails(self, form, monkeypatch):
        monkeypatch.setattr(
            "webforms.captcha.requests.post",
            lambda *a, **kw: _Response({"success": False}),
        )
        assert verify(form, token="tok", remote_ip=None) is False

    def test_a_response_with_no_success_key_fails(self, form, monkeypatch):
        monkeypatch.setattr(
            "webforms.captcha.requests.post", lambda *a, **kw: _Response({})
        )
        assert verify(form, token="tok", remote_ip=None) is False

    def test_a_missing_token_fails_without_calling_out(self, form, monkeypatch):
        def explode(*a, **kw):
            raise AssertionError("should not have called the verify endpoint")

        monkeypatch.setattr("webforms.captcha.requests.post", explode)
        assert verify(form, token="", remote_ip=None) is False

    def test_a_timeout_fails_closed(self, form, monkeypatch):
        def timeout(*a, **kw):
            raise requests.Timeout("too slow")

        monkeypatch.setattr("webforms.captcha.requests.post", timeout)
        assert verify(form, token="tok", remote_ip=None) is False

    def test_a_connection_error_fails_closed(self, form, monkeypatch):
        def boom(*a, **kw):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr("webforms.captcha.requests.post", boom)
        assert verify(form, token="tok", remote_ip=None) is False

    def test_a_non_json_response_fails_closed(self, form, monkeypatch):
        class Garbage:
            def json(self):
                raise ValueError("not json")

        monkeypatch.setattr(
            "webforms.captcha.requests.post", lambda *a, **kw: Garbage()
        )
        assert verify(form, token="tok", remote_ip=None) is False

    def test_a_configured_provider_with_no_secret_fails_closed(self, form, monkeypatch):
        form.captcha_secret = ""
        form.save(update_fields=["captcha_secret"])

        def explode(*a, **kw):
            raise AssertionError("should not have called the verify endpoint")

        monkeypatch.setattr("webforms.captcha.requests.post", explode)
        assert verify(form, token="tok", remote_ip=None) is False

    def test_the_secret_is_sent_but_never_logged(self, form, monkeypatch, caplog):
        sent = {}

        def capture(url, data=None, timeout=None):
            sent.update(data or {})
            raise requests.Timeout("forces the log line")

        monkeypatch.setattr("webforms.captcha.requests.post", capture)
        with caplog.at_level("DEBUG"):
            assert verify(form, token="tok", remote_ip=None) is False
        assert sent["secret"] == "secret-value"
        assert "secret-value" not in caplog.text

    def test_the_remote_ip_is_omitted_when_it_could_not_be_validated(
        self, form, monkeypatch
    ):
        sent = {}

        def capture(url, data=None, timeout=None):
            sent.update(data or {})
            return _Response({"success": True})

        monkeypatch.setattr("webforms.captcha.requests.post", capture)
        verify(form, token="tok", remote_ip=None)
        assert "remoteip" not in sent
