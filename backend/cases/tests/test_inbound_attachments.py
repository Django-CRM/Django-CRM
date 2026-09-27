"""Files attached to inbound support email land on the ticket.

The parser always extracted them; the pipeline dropped them on the floor, so a
customer's screenshot or invoice PDF was lost without a trace.
"""

from __future__ import annotations

from email.message import EmailMessage as StdlibEmailMessage

import pytest
from django.contrib.contenttypes.models import ContentType

from cases.inbound.parser import ParsedAttachment, parse_raw_email
from cases.inbound.pipeline import ingest
from cases.models import Case, InboundMailbox
from common.models import Attachments
from common.utils import ATTACHMENT_MAX_BYTES
from conftest import rls_org


def _mailbox(org):
    with rls_org(org):
        return InboundMailbox.objects.create(
            org=org,
            address="support@acme.com",
            provider="ses",
            webhook_secret="test-secret",
            default_priority="Normal",
            is_active=True,
        )


def _raw(*, message_id="<m1@example.com>", in_reply_to="", files=(), headers=()):
    msg = StdlibEmailMessage()
    msg["From"] = "Customer <user@example.com>"
    msg["To"] = "support@acme.com"
    msg["Subject"] = "Broken export"
    msg["Date"] = "Sat, 9 May 2026 12:00:00 +0000"
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    for name, value in headers:
        msg[name] = value
    msg.set_content("See attached.")
    for filename, payload in files:
        msg.add_attachment(
            payload, maintype="application", subtype="pdf", filename=filename
        )
    return msg.as_bytes()


def _case_attachments(case):
    return Attachments.objects.filter(
        content_type=ContentType.objects.get_for_model(Case), object_id=case.id
    )


@pytest.mark.django_db
class TestInboundAttachments:
    def test_new_ticket_keeps_the_attachment(self, org_a):
        result = ingest(
            parse_raw_email(_raw(files=[("invoice.pdf", b"%PDF-1.4 data")])),
            _mailbox(org_a),
        )

        [att] = _case_attachments(result.case)
        assert att.file_name == "invoice.pdf"
        assert att.org_id == org_a.id
        with att.attachment.open("rb") as fh:
            assert fh.read() == b"%PDF-1.4 data"

    def test_threaded_reply_attaches_to_the_existing_ticket(self, org_a):
        mailbox = _mailbox(org_a)
        first = ingest(parse_raw_email(_raw()), mailbox)

        reply = ingest(
            parse_raw_email(
                _raw(
                    message_id="<m2@example.com>",
                    in_reply_to="<m1@example.com>",
                    files=[("log.pdf", b"trace")],
                )
            ),
            mailbox,
        )

        assert reply.case == first.case
        assert [a.file_name for a in _case_attachments(first.case)] == ["log.pdf"]

    def test_dropped_email_stores_nothing(self, org_a):
        result = ingest(
            parse_raw_email(
                _raw(
                    files=[("x.pdf", b"x")],
                    headers=[("Auto-Submitted", "auto-replied")],
                )
            ),
            _mailbox(org_a),
        )

        assert result.dropped is True
        assert not Attachments.objects.exists()

    def test_provider_retry_does_not_duplicate_attachments(self, org_a):
        mailbox = _mailbox(org_a)
        raw = _raw(files=[("invoice.pdf", b"data")])
        first = ingest(parse_raw_email(raw), mailbox)
        ingest(parse_raw_email(raw), mailbox)

        assert _case_attachments(first.case).count() == 1

    def test_oversized_file_is_skipped_and_the_ticket_still_opens(self, org_a):
        parsed = parse_raw_email(_raw(files=[("small.pdf", b"ok")]))
        parsed.attachments.append(
            ParsedAttachment(
                filename="huge.bin",
                content_type="application/octet-stream",
                payload=b"\0" * (ATTACHMENT_MAX_BYTES + 1),
            )
        )

        result = ingest(parsed, _mailbox(org_a))

        assert result.created_case is True
        assert [a.file_name for a in _case_attachments(result.case)] == ["small.pdf"]

    @pytest.mark.parametrize(
        "hostile, label",
        [
            ("../../etc/passwd", "passwd"),
            ("..", "attachment"),
            ("", "attachment"),
            # Nothing survives Django's storage-name cleaning, which then raises
            # SuspiciousFileOperation and would fail the whole email.
            ("()", "()"),
        ],
    )
    def test_sender_controlled_filename_cannot_escape_storage(
        self, org_a, hostile, label
    ):
        parsed = parse_raw_email(_raw())
        parsed.attachments.append(
            ParsedAttachment(
                filename=hostile, content_type="text/plain", payload=b"data"
            )
        )

        result = ingest(parsed, _mailbox(org_a))

        [att] = _case_attachments(result.case)
        assert att.file_name == label
        assert att.attachment.name.startswith("attachments/")
        assert ".." not in att.attachment.name
