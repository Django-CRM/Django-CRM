"""Inbound MIME parsing.

Wraps Python's stdlib `email` package to produce a `ParsedEmail` dataclass
with the fields downstream code (threading, spam filter, contact resolver)
actually needs. Anything that's not safe to render is stored in raw form so
the discussion-tab renderer can decide what to display.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage as StdlibEmailMessage
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    payload: bytes


@dataclass
class ParsedEmail:
    raw_headers: dict[str, str]
    message_id: str
    in_reply_to: str
    references: list[str]
    from_address: str
    from_display_name: str
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    body_text: str
    body_html: str
    received_at: datetime
    attachments: list[ParsedAttachment] = field(default_factory=list)
    is_bounce: bool = False
    # Every `Delivered-To` and `X-Original-To` address, the headers a relay
    # stamps with the envelope recipient it delivered to. Read by the webhook
    # to tell whether a raw message was addressed to its mailbox.
    delivered_to: list[str] = field(default_factory=list)


def _strip_brackets(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    return value


def _split_references(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [_strip_brackets(part) for part in raw.split() if part.strip()]


def _addresses_from(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [
        addr.lower().strip()
        for _, addr in email.utils.getaddresses([header_value])
        if addr
    ]


def _split_from(header_value: str | None) -> tuple[str, str]:
    """Return (display_name, address) from a From: header."""
    if not header_value:
        return ("", "")
    name, addr = email.utils.parseaddr(header_value)
    return (name.strip(), addr.lower().strip())


def _coerce_timestamp(header_value: str | None) -> datetime:
    if header_value:
        try:
            parsed = email.utils.parsedate_to_datetime(header_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _extract_bodies(msg: StdlibEmailMessage) -> tuple[str, str]:
    """Return (text, html) pulling the first text/plain and text/html parts.

    Skips attachment dispositions; for multipart/alternative trees, the
    `email.policy.default` policy gives us `get_body()` which already prefers
    text/plain over text/html in unstructured cases.
    """
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment":
                continue
            try:
                content = part.get_content()
            except Exception:  # pragma: no cover, corrupted MIME
                continue
            if not isinstance(content, str):
                continue
            if ctype == "text/plain" and not text_body:
                text_body = content
            elif ctype == "text/html" and not html_body:
                html_body = content
    else:
        ctype = (msg.get_content_type() or "").lower()
        try:
            content = msg.get_content()
        except Exception:  # pragma: no cover
            content = ""
        if isinstance(content, str):
            if ctype == "text/html":
                html_body = content
            else:
                text_body = content
    return text_body, html_body


def _extract_attachments(msg: StdlibEmailMessage) -> list[ParsedAttachment]:
    attachments: list[ParsedAttachment] = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        if part.is_multipart():
            continue
        if (part.get_content_disposition() or "").lower() != "attachment":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # pragma: no cover
            payload = b""
        attachments.append(
            ParsedAttachment(
                filename=part.get_filename() or "attachment",
                content_type=(
                    part.get_content_type() or "application/octet-stream"
                ).lower(),
                payload=payload,
            )
        )
    return attachments


def _is_bounce(msg: StdlibEmailMessage) -> bool:
    """True if the MIME tree looks like a delivery-status bounce."""
    ctype = (msg.get_content_type() or "").lower()
    if ctype == "multipart/report":
        report_type = (
            msg.get_param("report-type", header="content-type") or ""
        ).lower()
        if report_type == "delivery-status":
            return True
    return False


def parse_raw_email(raw: bytes | str) -> ParsedEmail:
    """Parse raw RFC-5322 email bytes/string into a ParsedEmail dataclass."""
    if isinstance(raw, str):
        msg = email.message_from_string(raw, policy=email.policy.default)
    else:
        msg = email.message_from_bytes(raw, policy=email.policy.default)

    raw_headers = {k: str(v) for k, v in msg.items()}
    from_name, from_addr = _split_from(raw_headers.get("From"))
    text_body, html_body = _extract_bodies(msg)

    return ParsedEmail(
        raw_headers=raw_headers,
        message_id=_strip_brackets(raw_headers.get("Message-ID", "")),
        in_reply_to=_strip_brackets(raw_headers.get("In-Reply-To", "")),
        references=_split_references(raw_headers.get("References")),
        from_address=from_addr,
        from_display_name=from_name,
        to_addresses=_addresses_from(raw_headers.get("To")),
        cc_addresses=_addresses_from(raw_headers.get("Cc")),
        subject=raw_headers.get("Subject", "")[:512],
        body_text=text_body,
        body_html=html_body,
        received_at=_coerce_timestamp(raw_headers.get("Date")),
        attachments=_extract_attachments(msg),
        is_bounce=_is_bounce(msg),
        delivered_to=[
            addr
            for name in ("Delivered-To", "X-Original-To")
            for value in msg.get_all(name) or []
            for addr in _addresses_from(str(value))
        ],
    )


# Convenience helpers used from tests
def parse_addresses(values: Iterable[str]) -> list[str]:  # pragma: no cover
    return [v.lower().strip() for v in values if v]
