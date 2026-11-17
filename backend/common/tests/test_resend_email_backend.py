import base64
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import override_settings

from common.email_backends.resend import RESEND_API_URL, ResendAPIError, ResendEmailBackend


def test_sends_plain_text_and_html_through_resend(monkeypatch):
    post = Mock(return_value=Mock(ok=True))
    backend = ResendEmailBackend(api_key="re_test", timeout=7)
    monkeypatch.setattr(backend.session, "post", post)
    message = EmailMultiAlternatives(
        "Welcome", "Plain body", "Godan <hello@example.com>", ["user@example.net"]
    )
    message.attach_alternative("<p>Welcome</p>", "text/html")

    assert backend.send_messages([message]) == 1
    args, kwargs = post.call_args
    assert args == (RESEND_API_URL,)
    assert kwargs["headers"]["Authorization"] == "Bearer re_test"
    assert kwargs["timeout"] == 7
    assert kwargs["json"]["text"] == "Plain body"
    assert kwargs["json"]["html"] == "<p>Welcome</p>"


def test_sends_html_only_message_and_attachments(monkeypatch):
    post = Mock(return_value=Mock(ok=True))
    backend = ResendEmailBackend(api_key="re_test")
    monkeypatch.setattr(backend.session, "post", post)
    message = EmailMessage(
        "Invoice", "<p>Attached</p>", "billing@example.com", ["user@example.net"]
    )
    message.content_subtype = "html"
    message.attach("invoice.pdf", b"pdf-bytes", "application/pdf")
    message.extra_headers = {"X-Godan-Event": "invoice.created"}

    assert backend.send_messages([message]) == 1
    payload = post.call_args.kwargs["json"]
    assert payload["html"] == "<p>Attached</p>"
    assert payload["attachments"][0] == {
        "filename": "invoice.pdf",
        "content": base64.b64encode(b"pdf-bytes").decode("ascii"),
        "content_type": "application/pdf",
    }
    assert payload["headers"] == {"X-Godan-Event": "invoice.created"}


def test_preserves_cc_bcc_and_reply_to(monkeypatch):
    post = Mock(return_value=Mock(ok=True))
    backend = ResendEmailBackend(api_key="re_test")
    monkeypatch.setattr(backend.session, "post", post)
    message = EmailMessage(
        "Subject", "Body", "sender@example.com", ["to@example.com"],
        cc=["cc@example.com"], bcc=["bcc@example.com"],
        reply_to=["reply@example.com"],
    )

    backend.send_messages([message])
    payload = post.call_args.kwargs["json"]
    assert payload["cc"] == ["cc@example.com"]
    assert payload["bcc"] == ["bcc@example.com"]
    assert payload["reply_to"] == ["reply@example.com"]


def test_missing_api_key_fails_loudly():
    with override_settings(RESEND_API_KEY=""):
        backend = ResendEmailBackend()
        with pytest.raises(ImproperlyConfigured, match="RESEND_API_KEY"):
            backend.send_messages([EmailMessage("Subject", "Body", to=["a@example.com"])])


def test_fail_silently_skips_failed_message(monkeypatch):
    post = Mock(side_effect=RuntimeError("provider unavailable"))
    backend = ResendEmailBackend(api_key="re_test", fail_silently=True)
    monkeypatch.setattr(backend.session, "post", post)
    assert backend.send_messages([EmailMessage("Subject", "Body", to=["a@example.com"])]) == 0


def test_provider_rejection_raises_a_mail_delivery_error(monkeypatch):
    response = Mock(ok=False, status_code=422, text='{"message":"domain not verified"}')
    backend = ResendEmailBackend(api_key="re_test")
    monkeypatch.setattr(backend.session, "post", Mock(return_value=response))
    message = EmailMessage("Subject", "Body", to=["a@example.com"])

    with pytest.raises(ResendAPIError, match="HTTP 422"):
        backend.send_messages([message])
