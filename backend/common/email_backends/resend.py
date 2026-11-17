"""Django email backend for Resend's HTTPS email API.

Uses the project's existing requests dependency, avoiding SMTP ports and
additional runtime dependencies.
"""
import base64
import logging
from email.message import Message as MIMEMessage

import requests

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)
RESEND_API_URL = "https://api.resend.com/emails"


class ResendAPIError(OSError):
    """A rejected Resend API request, compatible with existing mail error handling."""


class ResendEmailBackend(BaseEmailBackend):
    """Send Django EmailMessage objects through Resend's REST API."""

    def __init__(self, fail_silently=False, api_key=None, timeout=10, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_key = api_key or getattr(settings, "RESEND_API_KEY", "")
        self.timeout = timeout
        self.session = requests.Session()

    def _attachment_payload(self, attachment):
        if isinstance(attachment, MIMEMessage):
            filename = attachment.get_filename()
            content_type = attachment.get_content_type()
            content = attachment.get_payload(decode=True)
            if content is None:
                payload = attachment.get_payload()
                content = payload.encode() if isinstance(payload, str) else payload
        else:
            filename, content, content_type = attachment
            if isinstance(content, str):
                content = content.encode()
        if not filename or not content_type or content is None:
            raise ValueError("Resend attachments require a filename, content and MIME type.")
        return {
            "filename": filename,
            "content": base64.b64encode(content).decode("ascii"),
            "content_type": content_type,
        }

    def _message_payload(self, message):
        payload = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": message.to,
            "subject": message.subject,
        }
        if message.cc:
            payload["cc"] = message.cc
        if message.bcc:
            payload["bcc"] = message.bcc
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        html_body = None
        text_body = None
        if getattr(message, "content_subtype", "plain") == "html":
            html_body = message.body
        else:
            text_body = message.body

        for content, mimetype in getattr(message, "alternatives", ()):
            if mimetype == "text/html":
                html_body = content
            elif mimetype == "text/plain":
                text_body = content

        if html_body is not None:
            payload["html"] = html_body
        if text_body is not None:
            payload["text"] = text_body
        if message.attachments:
            payload["attachments"] = [
                self._attachment_payload(item) for item in message.attachments
            ]
        if message.extra_headers:
            payload["headers"] = {
                str(key): str(value) for key, value in message.extra_headers.items()
            }
        return payload

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            raise ImproperlyConfigured(
                "RESEND_API_KEY is required when the Resend email backend is enabled."
            )

        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                response = self.session.post(
                    RESEND_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._message_payload(message),
                    timeout=self.timeout,
                )
                if not response.ok:
                    raise ResendAPIError(
                        f"Resend API returned HTTP {response.status_code}: "
                        f"{response.text[:1000]}"
                    )
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Resend email delivery failed")
        return sent

    def close(self):
        self.session.close()
