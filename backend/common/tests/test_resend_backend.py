from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives

from common.email_backends.resend import ResendAPIError, ResendEmailBackend


def make_message():
    message = EmailMultiAlternatives(
        "Welcome to Godan CRM",
        "Welcome to Godan CRM. Open your workspace.",
        "Godan CRM <noreply@godan.co.ke>",
        ["person@example.com"],
    )
    message.attach_alternative(
        "<html><body><h1>Welcome to Godan CRM</h1></body></html>",
        "text/html",
    )
    message.extra_headers = {"Idempotency-Key": "welcome-user-123"}
    return message


def test_resend_backend_sends_text_html_and_idempotency_header():
    backend = ResendEmailBackend(api_key="re_test_secret")
    response = Mock(ok=True, json=Mock(return_value={"id": "email_123"}))
    backend.session.post = Mock(return_value=response)

    assert backend.send_messages([make_message()]) == 1

    args, kwargs = backend.session.post.call_args
    assert args == ("https://api.resend.com/emails",)
    assert kwargs["headers"] == {
        "Authorization": "Bearer re_test_secret",
        "Content-Type": "application/json",
        "Idempotency-Key": "welcome-user-123",
    }
    assert kwargs["json"]["from"] == "Godan CRM <noreply@godan.co.ke>"
    assert kwargs["json"]["to"] == ["person@example.com"]
    assert (
        kwargs["json"]["text"] == "Welcome to Godan CRM. Open your workspace."
    )
    assert "Godan CRM" in kwargs["json"]["html"]
    assert "Idempotency-Key" not in kwargs["json"].get("headers", {})
    assert kwargs["timeout"] == 10
    backend.close()


def test_resend_backend_rejects_api_errors():
    backend = ResendEmailBackend(api_key="re_test_secret")
    backend.session.post = Mock(
        return_value=Mock(ok=False, status_code=422, text='{"message":"invalid from"}')
    )

    with pytest.raises(ResendAPIError, match="HTTP 422"):
        backend.send_messages([make_message()])
    backend.close()


def test_resend_backend_requires_email_id_in_success_response():
    backend = ResendEmailBackend(api_key="re_test_secret")
    backend.session.post = Mock(return_value=Mock(ok=True, json=Mock(return_value={})))

    with pytest.raises(ResendAPIError, match="email ID"):
        backend.send_messages([make_message()])
    backend.close()


def test_resend_backend_requires_api_key():
    backend = ResendEmailBackend(api_key="")
    with pytest.raises(ImproperlyConfigured, match="RESEND_API_KEY"):
        backend.send_messages([make_message()])
    backend.close()


def test_resend_backend_skips_messages_without_recipients():
    backend = ResendEmailBackend(api_key="re_test_secret")
    message = EmailMultiAlternatives("Subject", "Body", "from@example.com", [])
    backend.session.post = Mock()

    assert backend.send_messages([message]) == 0
    backend.session.post.assert_not_called()
    backend.close()
