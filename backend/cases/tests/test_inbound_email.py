"""Inbound email pipeline tests: parser, spam filter, threading, pipeline, API.

See docs/cases/tier1/email-to-ticket.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from django.utils.timezone import now as dj_now

from cases.inbound.parser import ParsedEmail, parse_raw_email
from cases.inbound.pipeline import ingest
from cases.inbound.spam import should_drop
from cases.inbound.threading import find_existing_case, short_case_id
from cases.models import Case, EmailMessage, InboundMailbox
from common.models import Profile, User
from conftest import rls_org
from contacts.models import Contact

MAILBOXES_URL = "/api/cases/mailboxes/"

# The SNS topic a mailbox is pinned to. A signature only proves a message came
# from *some* SNS topic, so the webhook additionally requires it to come from
# the topic this mailbox was wired to.
SNS_TOPIC = "arn:aws:sns:us-east-1:123456789012:acme-inbound"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mailbox(org, **overrides):
    defaults = {
        "address": "support@acme.com",
        "provider": "ses",
        "webhook_secret": "test-secret",
        "topic_arn": SNS_TOPIC,
        "default_priority": "Normal",
        "default_case_type": None,
        "is_active": True,
    }
    defaults.update(overrides)
    # Seeded into another tenant: the insert-check policy compares
    # against `app.current_org`, so writing this row means being that
    # tenant for the length of the write.
    with rls_org(org):
        return InboundMailbox.objects.create(org=org, **defaults)


def _raw_email(
    *,
    message_id="<m1@example.com>",
    in_reply_to="",
    references="",
    from_="Customer <user@example.com>",
    to="support@acme.com",
    subject="Help with login",
    body="I cannot log in.",
    extra_headers="",
):
    headers = [
        f"From: {from_}",
        f"To: {to}",
        f"Subject: {subject}",
        "Date: Sat, 9 May 2026 12:00:00 +0000",
        f"Message-ID: {message_id}",
    ]
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    if references:
        headers.append(f"References: {references}")
    if extra_headers:
        headers.append(extra_headers)
    headers.append("MIME-Version: 1.0")
    headers.append('Content-Type: text/plain; charset="utf-8"')
    return "\r\n".join(headers) + "\r\n\r\n" + body


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestParser:
    def test_basic_headers_and_body(self):
        parsed = parse_raw_email(_raw_email())
        assert parsed.message_id == "m1@example.com"
        assert parsed.from_address == "user@example.com"
        assert parsed.from_display_name == "Customer"
        assert "support@acme.com" in parsed.to_addresses
        assert parsed.subject == "Help with login"
        assert "I cannot log in" in parsed.body_text

    def test_in_reply_to_and_references(self):
        parsed = parse_raw_email(
            _raw_email(
                message_id="<m2@example.com>",
                in_reply_to="<m1@example.com>",
                references="<m0@example.com> <m1@example.com>",
            )
        )
        assert parsed.in_reply_to == "m1@example.com"
        assert parsed.references == ["m0@example.com", "m1@example.com"]

    def test_strips_angle_brackets(self):
        parsed = parse_raw_email(_raw_email(message_id="<has-angles@x>"))
        assert parsed.message_id == "has-angles@x"

    def test_bounce_detection(self):
        # multipart/report with delivery-status report-type → bounce
        bounce = (
            "From: MAILER-DAEMON@example.com\r\n"
            "To: support@acme.com\r\n"
            "Subject: Delivery Status Notification\r\n"
            "Date: Sat, 9 May 2026 12:00:00 +0000\r\n"
            "Message-ID: <bounce@x>\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/report; report-type=delivery-status; "
            'boundary="b"\r\n'
            "\r\n"
            "--b\r\n"
            "Content-Type: text/plain\r\n\r\n"
            "Delivery failed.\r\n"
            "--b--\r\n"
        )
        parsed = parse_raw_email(bounce)
        assert parsed.is_bounce is True


# ---------------------------------------------------------------------------
# Spam filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSpam:
    def test_passes_normal_mail(self):
        parsed = parse_raw_email(_raw_email())
        drop, reason = should_drop(parsed)
        assert drop is False and reason == ""

    def test_drops_auto_submitted(self):
        parsed = parse_raw_email(
            _raw_email(extra_headers="Auto-Submitted: auto-replied")
        )
        drop, reason = should_drop(parsed)
        assert drop is True and reason == "auto_submitted"

    def test_drops_precedence_bulk(self):
        parsed = parse_raw_email(_raw_email(extra_headers="Precedence: bulk"))
        drop, reason = should_drop(parsed)
        assert drop is True and reason == "precedence_bulk"

    def test_drops_x_autoreply(self):
        parsed = parse_raw_email(_raw_email(extra_headers="X-Autoreply: yes"))
        drop, reason = should_drop(parsed)
        assert drop is True and reason == "x_autoreply"

    def test_drops_mailing_list(self):
        parsed = parse_raw_email(
            _raw_email(
                extra_headers="List-Id: <newsletter.example.com>\r\n"
                "List-Unsubscribe: <mailto:unsub@example.com>"
            )
        )
        drop, reason = should_drop(parsed)
        assert drop is True and reason == "mailing_list"

    def test_drops_bounce(self):
        parsed = ParsedEmail(
            raw_headers={},
            message_id="b@x",
            in_reply_to="",
            references=[],
            from_address="MAILER-DAEMON@example.com",
            from_display_name="",
            to_addresses=[],
            cc_addresses=[],
            subject="",
            body_text="",
            body_html="",
            received_at=datetime.now(timezone.utc),
            is_bounce=True,
        )
        drop, reason = should_drop(parsed)
        assert drop is True and reason == "bounce"


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestThreading:
    def test_in_reply_to_match(self, admin_user, org_a):
        case = Case.objects.create(
            name="Original",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            external_thread_id="root@x",
        )
        EmailMessage.objects.create(
            org=org_a,
            case=case,
            direction="inbound",
            message_id="root@x",
            from_address="user@x.com",
            received_at=datetime.now(timezone.utc),
        )
        parsed = parse_raw_email(
            _raw_email(message_id="<reply@x>", in_reply_to="<root@x>")
        )
        assert find_existing_case(parsed, org_a).case == case

    def test_references_match(self, admin_user, org_a):
        case = Case.objects.create(
            name="Original",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            external_thread_id="root@x",
        )
        parsed = parse_raw_email(
            _raw_email(message_id="<reply@x>", references="<root@x> <other@x>")
        )
        assert find_existing_case(parsed, org_a).case == case

    def test_no_match_returns_none(self, admin_user, org_a):
        parsed = parse_raw_email(_raw_email(message_id="<lonely@x>"))
        assert find_existing_case(parsed, org_a) is None

    def test_subject_fallback(self, admin_user, org_a):
        case = Case.objects.create(
            name="Original",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        prefix = short_case_id(case)
        parsed = parse_raw_email(
            _raw_email(
                message_id="<reply@x>",
                subject=f"Re: [Case #{prefix}] Help",
            )
        )
        match = find_existing_case(parsed, org_a)
        assert match.case == case
        assert match.by_header is False

    def test_subject_only_no_brackets_no_match(self, admin_user, org_a):
        Case.objects.create(
            name="Help",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
        )
        # Subject identical but no `[Case #...]` marker, must not match.
        parsed = parse_raw_email(_raw_email(message_id="<reply@x>", subject="Help"))
        assert find_existing_case(parsed, org_a) is None

    def test_cross_org_isolation(self, admin_user, org_a, org_b):
        Case.objects.create(
            name="org-a",
            status="New",
            priority="Normal",
            org=org_a,
            created_by=admin_user,
            external_thread_id="x@x",
        )
        parsed = parse_raw_email(
            _raw_email(message_id="<reply@x>", in_reply_to="<x@x>")
        )
        # Looking up against org_b should miss
        assert find_existing_case(parsed, org_b) is None


# ---------------------------------------------------------------------------
# Who a subject-tag match may attach
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubjectTagTrust:
    """The `[Case #...]` tag rides on every outbound reply, with the reply text.

    Anybody who sees one subject line can copy it, so a tag-only match may
    thread only a sender who is already on the case. Anyone else gets a new
    ticket of their own and is never added to the tagged one.
    """

    def _ticket(self, org, *contacts, **fields):
        fields = {"status": "Assigned", **fields}
        case = Case.objects.create(org=org, name="Printer", priority="Normal", **fields)
        for email in contacts:
            case.contacts.add(
                Contact.objects.create(org=org, first_name="Pat", email=email)
            )
        return case

    def test_stranger_with_the_tag_gets_a_new_ticket(self, org_a):
        mailbox = _make_mailbox(org_a)
        case = self._ticket(org_a, "pat@customer.test")
        raw = _raw_email(
            message_id="<intruder-1@evil.test>",
            from_="Eve <eve@evil.test>",
            subject=f"Re: Printer [Case #{short_case_id(case)}]",
            body="Add me please.",
        )
        result = ingest(parse_raw_email(raw), mailbox)

        assert result.created_case is True
        assert result.case != case
        assert result.case.name == "Re: Printer"
        assert result.email_message.case == result.case
        assert list(case.contacts.values_list("email", flat=True)) == [
            "pat@customer.test"
        ]
        assert result.case.contacts.get().email == "eve@evil.test"
        assert not EmailMessage.objects.filter(case=case).exists()

    def test_stranger_with_the_tag_does_not_reopen_the_ticket(self, org_a):
        mailbox = _make_mailbox(org_a)
        case = self._ticket(org_a, "pat@customer.test")
        case.status = "Closed"
        case.save()
        raw = _raw_email(
            message_id="<intruder-2@evil.test>",
            from_="eve@evil.test",
            subject=f"Re: Printer [Case #{short_case_id(case)}]",
        )
        ingest(parse_raw_email(raw), mailbox)
        case.refresh_from_db()
        assert case.status == "Closed"

    def test_contact_on_the_case_threads_by_tag(self, org_a):
        mailbox = _make_mailbox(org_a)
        case = self._ticket(org_a, "pat@customer.test")
        raw = _raw_email(
            message_id="<pat-2@customer.test>",
            from_="PAT@customer.test",
            subject=f"Re: Printer [Case #{short_case_id(case)}]",
        )
        result = ingest(parse_raw_email(raw), mailbox)
        assert result.created_case is False
        assert result.case == case
        assert case.contacts.count() == 1

    def test_header_match_threads_and_adds_a_new_sender(self, org_a):
        mailbox = _make_mailbox(org_a)
        case = self._ticket(org_a, "pat@customer.test")
        EmailMessage.objects.create(
            org=org_a,
            case=case,
            direction="outbound",
            message_id="agent-reply@acme.com",
            from_address="support@acme.com",
            received_at=dj_now(),
        )
        raw = _raw_email(
            message_id="<colleague-1@customer.test>",
            in_reply_to="<agent-reply@acme.com>",
            from_="Sam <sam@customer.test>",
            subject="Re: Printer",
        )
        result = ingest(parse_raw_email(raw), mailbox)
        assert result.created_case is False
        assert result.case == case
        assert case.contacts.filter(email="sam@customer.test").exists()

    def test_tag_of_a_merged_ticket_threads_its_contact_onto_the_primary(self, org_a):
        mailbox = _make_mailbox(org_a)
        primary = self._ticket(org_a, "pat@customer.test")
        duplicate = self._ticket(org_a, merged_into=primary, status="Duplicate")
        raw = _raw_email(
            message_id="<pat-3@customer.test>",
            from_="pat@customer.test",
            subject=f"Re: Printer [Case #{short_case_id(duplicate)}]",
        )
        result = ingest(parse_raw_email(raw), mailbox)
        assert result.created_case is False
        assert result.case == primary

    def test_tag_of_a_merged_ticket_from_a_stranger_is_a_new_ticket(self, org_a):
        mailbox = _make_mailbox(org_a)
        primary = self._ticket(org_a, "pat@customer.test")
        duplicate = self._ticket(org_a, merged_into=primary, status="Duplicate")
        raw = _raw_email(
            message_id="<intruder-3@evil.test>",
            from_="eve@evil.test",
            subject=f"Re: Printer [Case #{short_case_id(duplicate)}]",
        )
        result = ingest(parse_raw_email(raw), mailbox)
        assert result.created_case is True
        assert result.case not in (primary, duplicate)
        assert not primary.contacts.filter(email="eve@evil.test").exists()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPipeline:
    def test_first_email_creates_case_and_contact(self, org_a):
        mailbox = _make_mailbox(org_a, default_priority="High")
        parsed = parse_raw_email(_raw_email())
        result = ingest(parsed, mailbox)
        assert result.created_case is True and result.dropped is False
        assert result.case is not None
        assert result.case.priority == "High"
        assert result.case.external_thread_id == "m1@example.com"
        # Contact auto-created
        contact = Contact.objects.get(email__iexact="user@example.com", org=org_a)
        assert contact.auto_created is True
        assert contact.first_name == "Customer"
        assert result.case.contacts.filter(pk=contact.pk).exists()
        # EmailMessage row recorded with the case
        em = EmailMessage.objects.get(message_id="m1@example.com", org=org_a)
        assert em.case_id == result.case.id

    def test_reply_threads_to_existing(self, org_a):
        mailbox = _make_mailbox(org_a)
        first = parse_raw_email(_raw_email())
        first_result = ingest(first, mailbox)
        case = first_result.case

        reply = parse_raw_email(
            _raw_email(message_id="<m2@example.com>", in_reply_to="<m1@example.com>")
        )
        second_result = ingest(reply, mailbox)
        assert second_result.created_case is False
        assert second_result.case == case
        assert EmailMessage.objects.filter(case=case).count() == 2

    def test_spam_dropped_no_case_created(self, org_a):
        mailbox = _make_mailbox(org_a)
        parsed = parse_raw_email(
            _raw_email(extra_headers="Auto-Submitted: auto-replied")
        )
        result = ingest(parsed, mailbox)
        assert result.dropped is True
        assert result.drop_reason == "auto_submitted"
        assert result.case is None
        assert Case.objects.count() == 0
        em = EmailMessage.objects.get(message_id="m1@example.com")
        assert em.case is None and em.drop_reason == "auto_submitted"

    def test_existing_contact_reused(self, org_a):
        mailbox = _make_mailbox(org_a)
        Contact.objects.create(
            org=org_a,
            email="user@example.com",
            first_name="Existing",
            last_name="User",
            is_active=True,
        )
        parsed = parse_raw_email(_raw_email())
        result = ingest(parsed, mailbox)
        contact = Contact.objects.get(email__iexact="user@example.com", org=org_a)
        assert contact.auto_created is False
        assert contact.first_name == "Existing"
        assert result.case.contacts.filter(pk=contact.pk).exists()

    def test_provider_retry_idempotent(self, org_a):
        mailbox = _make_mailbox(org_a)
        parsed = parse_raw_email(_raw_email())
        first = ingest(parsed, mailbox)
        # Same Message-ID arriving again (provider retry) must not duplicate.
        parsed2 = parse_raw_email(_raw_email())
        second = ingest(parsed2, mailbox)
        assert Case.objects.count() == 1
        assert EmailMessage.objects.filter(message_id="m1@example.com").count() == 1
        assert second.case == first.case
        assert second.created_case is False

    def test_default_assignee_added(self, org_a):
        user = User.objects.create_user(email="agent@x.com", password="x")
        agent = Profile.objects.create(
            user=user, org=org_a, role="USER", is_active=True
        )
        mailbox = _make_mailbox(org_a, default_assignee=agent)
        parsed = parse_raw_email(_raw_email())
        result = ingest(parsed, mailbox)
        assert list(result.case.assigned_to.all()) == [agent]


# ---------------------------------------------------------------------------
# Activity row per inbound email + reopen tie-in
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInboundActivityAndReopen:
    """Item-2 (EMAIL_RECEIVED audit row) and item-1 (reopen on inbound)."""

    def _close(self, case, *, days_ago):
        from datetime import timedelta as _td

        from django.utils import timezone as _tz

        case.status = "Closed"
        case.closed_on = _tz.localdate() - _td(days=days_ago)
        case.save()
        return case

    def _activities(self, case, action):
        from common.models import Activity

        return Activity.objects.filter(
            entity_type="Case", entity_id=case.pk, action=action
        ).order_by("-created_at")

    def test_email_received_activity_emitted_on_new_case(self, org_a):
        mailbox = _make_mailbox(org_a)
        parsed = parse_raw_email(_raw_email())
        result = ingest(parsed, mailbox)
        rows = self._activities(result.case, "EMAIL_RECEIVED")
        assert rows.count() == 1
        meta = rows.first().metadata
        assert meta["from_address"] == "user@example.com"
        assert meta["message_id"] == "m1@example.com"
        assert meta["email_message_id"] == str(result.email_message.pk)

    def test_email_received_activity_emitted_on_threaded_reply(self, org_a):
        mailbox = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email()), mailbox)
        case = first.case
        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<m2@example.com>", in_reply_to="<m1@example.com>"
                )
            ),
            mailbox,
        )
        rows = self._activities(case, "EMAIL_RECEIVED")
        assert rows.count() == 2

    def test_email_received_not_emitted_for_drops(self, org_a):
        mailbox = _make_mailbox(org_a)
        parsed = parse_raw_email(
            _raw_email(extra_headers="Auto-Submitted: auto-replied")
        )
        result = ingest(parsed, mailbox)
        assert result.dropped is True
        from common.models import Activity

        assert Activity.objects.filter(action="EMAIL_RECEIVED", org=org_a).count() == 0

    def test_reply_within_window_reopens_closed_case(self, org_a):
        from common.models import Activity

        mailbox = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email()), mailbox)
        case = first.case
        self._close(case, days_ago=2)

        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<m2@example.com>", in_reply_to="<m1@example.com>"
                )
            ),
            mailbox,
        )

        case.refresh_from_db()
        assert case.status == "Pending"
        assert case.closed_on is None

        reopened = (
            Activity.objects.filter(
                entity_type="Case", entity_id=case.pk, action="REOPENED"
            )
            .order_by("-created_at")
            .first()
        )
        assert reopened is not None
        assert reopened.metadata["to_status"] == "Pending"
        assert reopened.metadata["days_since_close"] == 2
        assert "email_message_id" in reopened.metadata

    def test_reply_outside_window_does_not_reopen(self, org_a):
        from common.models import Activity

        mailbox = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email()), mailbox)
        case = first.case
        self._close(case, days_ago=30)

        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<m2@example.com>", in_reply_to="<m1@example.com>"
                )
            ),
            mailbox,
        )

        case.refresh_from_db()
        assert case.status == "Closed"
        assert (
            Activity.objects.filter(
                entity_type="Case", entity_id=case.pk, action="REOPENED"
            ).count()
            == 0
        )

    def test_reply_to_open_case_does_not_change_status(self, org_a):
        mailbox = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email()), mailbox)
        case = first.case
        original_status = case.status
        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<m2@example.com>", in_reply_to="<m1@example.com>"
                )
            ),
            mailbox,
        )
        case.refresh_from_db()
        assert case.status == original_status

    def test_disabled_policy_blocks_reopen(self, org_a):
        from cases.models import ReopenPolicy

        ReopenPolicy.objects.create(org=org_a, is_enabled=False)
        mailbox = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email()), mailbox)
        case = first.case
        self._close(case, days_ago=1)

        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<m2@example.com>", in_reply_to="<m1@example.com>"
                )
            ),
            mailbox,
        )
        case.refresh_from_db()
        assert case.status == "Closed"


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMailboxAPI:
    def test_admin_can_list_and_create(self, admin_client, org_a):
        response = admin_client.get(MAILBOXES_URL)
        assert response.status_code == 200
        assert response.json()["mailboxes"] == []

        create = admin_client.post(
            MAILBOXES_URL,
            {
                "address": "support@acme.com",
                "provider": "ses",
                "default_priority": "High",
            },
            format="json",
        )
        assert create.status_code == 201, create.content
        data = create.json()
        assert data["address"] == "support@acme.com"
        # No secret is minted. This used to assert one was auto-generated, and
        # nothing has ever compared that value: SES delivery is authenticated
        # by the SNS signature and the topic_arn pin.
        assert "webhook_secret" not in data
        assert data["has_webhook_secret"] is False

    def test_user_cannot_create(self, user_client, org_a):
        response = user_client.post(
            MAILBOXES_URL,
            {"address": "x@x.com", "provider": "ses"},
            format="json",
        )
        assert response.status_code == 403

    def test_non_admin_sees_config_without_the_admin_only_fields(
        self, user_client, org_a
    ):
        """A member may see the mailbox config but not the integration keys."""
        mailbox = _make_mailbox(org_a, webhook_secret="s3cr3t-value")

        listed = user_client.get(MAILBOXES_URL)
        assert listed.status_code == 200
        rows = listed.json()["mailboxes"]
        assert len(rows) == 1
        assert rows[0]["address"] == "support@acme.com"  # config still visible
        assert "webhook_secret" not in rows[0]
        assert "has_webhook_secret" not in rows[0]
        assert "topic_arn" not in rows[0]

        detail = user_client.get(f"{MAILBOXES_URL}{mailbox.id}/")
        assert detail.status_code == 200
        assert "webhook_secret" not in detail.json()
        assert "topic_arn" not in detail.json()

    def test_admin_cannot_read_the_webhook_secret_back(self, admin_client, org_a):
        """The inverse of an assertion this file used to make.

        It read `== "s3cr3t-value"` on both list and detail, so an admin's
        session, or any personal access token that admin had minted, could pull
        every mailbox's stored secret out of a list endpoint. Nothing consumes
        that value, so returning it bought nothing and would have turned into a
        live leak the moment a provider integration started checking it. An
        admin gets the boolean instead.
        """
        mailbox = _make_mailbox(org_a, webhook_secret="s3cr3t-value")

        listed = admin_client.get(MAILBOXES_URL)
        row = listed.json()["mailboxes"][0]
        assert "webhook_secret" not in row
        assert row["has_webhook_secret"] is True
        assert row["topic_arn"] == mailbox.topic_arn  # admin-only, still returned

        detail = admin_client.get(f"{MAILBOXES_URL}{mailbox.id}/")
        assert "webhook_secret" not in detail.json()
        assert detail.json()["has_webhook_secret"] is True

    def test_has_webhook_secret_is_false_when_none_is_stored(self, admin_client, org_a):
        _make_mailbox(org_a, webhook_secret="")
        row = admin_client.get(MAILBOXES_URL).json()["mailboxes"][0]
        assert row["has_webhook_secret"] is False

    def test_has_topic_arn_reports_whether_the_mailbox_is_pinned(
        self, admin_client, org_a
    ):
        """The fact both clients need to tell a working address from a silent one.

        An active SES mailbox with no pin rejects every notification, so
        `is_active` alone cannot say whether mail becomes tickets.
        """
        _make_mailbox(org_a, address="pinned@acme.com", topic_arn=SNS_TOPIC)
        _make_mailbox(org_a, address="unpinned@acme.com", topic_arn="")

        rows = {
            row["address"]: row
            for row in admin_client.get(MAILBOXES_URL).json()["mailboxes"]
        }
        assert rows["pinned@acme.com"]["has_topic_arn"] is True
        assert rows["unpinned@acme.com"]["has_topic_arn"] is False

    def test_a_member_sees_the_pin_state_but_never_the_arn(self, user_client, org_a):
        """Deliberately not admin-only, unlike `topic_arn` and
        `has_webhook_secret`. A member can see the row, so a member has to be
        able to read it: without this the list would show a never-connected
        address as one that is creating tickets."""
        mailbox = _make_mailbox(org_a, topic_arn="")

        row = user_client.get(MAILBOXES_URL).json()["mailboxes"][0]
        assert row["has_topic_arn"] is False
        assert "topic_arn" not in row

        detail = user_client.get(f"{MAILBOXES_URL}{mailbox.id}/").json()
        assert detail["has_topic_arn"] is False
        assert "topic_arn" not in detail

    def test_the_pin_state_is_not_settable_from_a_request_body(
        self, admin_client, org_a
    ):
        """It is derived from `topic_arn`, which the webhook owns. A body
        claiming the mailbox is connected must not make it read as connected."""
        mailbox = _make_mailbox(org_a, topic_arn="")

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"has_topic_arn": True},
            format="json",
        )

        assert response.status_code == 200, response.content
        assert response.json()["has_topic_arn"] is False
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""

    def test_admin_can_write_a_provider_issued_secret_without_reading_it_back(
        self, admin_client, org_a
    ):
        """Write-only means writable. A pasted key must still reach the column."""
        mailbox = _make_mailbox(org_a, webhook_secret="")

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"webhook_secret": "provider-issued-key"},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert "webhook_secret" not in response.json()
        assert response.json()["has_webhook_secret"] is True

        mailbox.refresh_from_db()
        assert mailbox.webhook_secret == "provider-issued-key"

    def test_non_admin_cannot_write_the_webhook_secret(self, user_client, org_a):
        mailbox = _make_mailbox(org_a, webhook_secret="original")

        response = user_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"webhook_secret": "attacker-chosen"},
            format="json",
        )
        assert response.status_code == 403

        mailbox.refresh_from_db()
        assert mailbox.webhook_secret == "original"

    def test_unsupported_provider_returns_501(self, admin_client, org_a):
        # Mailgun isn't yet wired into the webhook, but the model accepts it.
        # Create a mailbox with provider=mailgun and verify the webhook 501s.
        mailbox = _make_mailbox(org_a, provider="mailgun")
        response = admin_client.post(
            f"/api/cases/inbound/{mailbox.id}/",
            {
                "Type": "Notification",
                "Message": "x",
                "Signature": "x",
                "SigningCertURL": "x",
                "SignatureVersion": "1",
            },
            format="json",
        )
        assert response.status_code == 501

    def test_inactive_mailbox_404(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a, is_active=False)
        response = admin_client.post(
            f"/api/cases/inbound/{mailbox.id}/",
            {"Type": "Notification"},
            format="json",
        )
        assert response.status_code == 404

    def test_cross_org_isolation(self, admin_client, org_a, org_b):
        _make_mailbox(org_b, address="other@beta.com")
        response = admin_client.get(MAILBOXES_URL)
        assert response.json()["mailboxes"] == []

    def test_duplicate_address_on_update_returns_400(self, admin_client, org_a):
        """Editing a mailbox address onto another mailbox's address is a clean
        400, not an IntegrityError. The DB constraint is uniq(org, address);
        before this guard the update path skipped the duplicate check entirely
        and the constraint surfaced as a bodiless 500."""
        _make_mailbox(org_a, address="support@example.com")
        second = _make_mailbox(org_a, address="sales@example.com")

        response = admin_client.put(
            f"{MAILBOXES_URL}{second.id}/",
            {"address": "support@example.com"},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "address" in response.json()["errors"]
        second.refresh_from_db()
        assert second.address == "sales@example.com"

    def test_update_keeping_own_address_is_allowed(self, admin_client, org_a):
        """A mailbox may be saved with its own address unchanged. The duplicate
        guard must exclude the instance being edited or every edit that
        resubmits the address would 400 against itself."""
        mailbox = _make_mailbox(org_a, address="support@example.com")

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/",
            {"address": "support@example.com", "default_priority": "High"},
            content_type="application/json",
        )

        assert response.status_code == 200
        mailbox.refresh_from_db()
        assert mailbox.address == "support@example.com"


# ---------------------------------------------------------------------------
# Webhook (verification path bypassed via patch)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebhook:
    def test_subscription_confirmation_calls_subscribe(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a)
        with (
            patch("cases.inbound_views.verify_sns_message") as verify,
            patch("cases.inbound_views.confirm_subscription") as confirm,
        ):
            verify.return_value = None
            confirm.return_value = None
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {
                    "Type": "SubscriptionConfirmation",
                    "SubscribeURL": "https://x",
                    "TopicArn": SNS_TOPIC,
                },
                format="json",
            )
        assert response.status_code == 200
        assert confirm.called is True

    def test_notification_routes_through_pipeline(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a)
        raw = _raw_email()
        with patch("cases.inbound_views.verify_sns_message") as verify:
            verify.return_value = None
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {
                    "Type": "Notification",
                    "Message": raw,
                    "Signature": "x",
                    "SigningCertURL": "x",
                    "SignatureVersion": "1",
                    "TopicArn": SNS_TOPIC,
                },
                format="json",
            )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["created_case"] is True
        assert body["dropped"] is False

    def test_signature_failure_403(self, admin_client, org_a):
        from cases.inbound.sns import SNSVerificationError

        mailbox = _make_mailbox(org_a)
        with patch("cases.inbound_views.verify_sns_message") as verify:
            verify.side_effect = SNSVerificationError("nope")
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {
                    "Type": "Notification",
                    "Message": "x",
                    "Signature": "x",
                    "SigningCertURL": "x",
                    "SignatureVersion": "1",
                },
                format="json",
            )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# TopicArn pinning
#
# A valid SNS signature only proves a message came from *some* SNS topic in
# *some* AWS account: anyone who learns a mailbox UUID could otherwise point
# their own topic at the webhook and have AWS sign forged mail for them. The
# webhook therefore also pins the TopicArn to the mailbox.
# ---------------------------------------------------------------------------


OTHER_TOPIC = "arn:aws:sns:us-east-1:999999999999:attacker-topic"


def _notification(topic_arn, message="x"):
    return {
        "Type": "Notification",
        "Message": message,
        "Signature": "x",
        "SigningCertURL": "x",
        "SignatureVersion": "1",
        "TopicArn": topic_arn,
    }


@pytest.mark.django_db
class TestTopicArnPinning:
    def test_notification_from_the_pinned_topic_is_accepted(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a, topic_arn=SNS_TOPIC)
        with patch("cases.inbound_views.verify_sns_message"):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                _notification(SNS_TOPIC, _raw_email()),
                format="json",
            )
        assert response.status_code == 200, response.content
        assert Case.objects.filter(org=org_a).count() == 1

    def test_notification_from_a_foreign_topic_is_rejected(self, admin_client, org_a):
        """The crown-jewel case: a correctly-signed message from an attacker's
        own SNS topic must not be able to inject mail into this org."""
        mailbox = _make_mailbox(org_a, topic_arn=SNS_TOPIC)
        with patch("cases.inbound_views.verify_sns_message"):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                _notification(OTHER_TOPIC, _raw_email()),
                format="json",
            )
        assert response.status_code == 403
        assert Case.objects.filter(org=org_a).count() == 0
        assert EmailMessage.objects.filter(org=org_a).count() == 0

    def test_notification_to_an_unpinned_mailbox_is_rejected(self, admin_client, org_a):
        """Fail closed: until a topic is pinned there is nothing to check against."""
        mailbox = _make_mailbox(org_a, topic_arn="")
        with patch("cases.inbound_views.verify_sns_message"):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                _notification(SNS_TOPIC, _raw_email()),
                format="json",
            )
        assert response.status_code == 403
        assert Case.objects.filter(org=org_a).count() == 0

    def test_notification_without_a_topic_arn_is_rejected(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a, topic_arn=SNS_TOPIC)
        payload = _notification(SNS_TOPIC, _raw_email())
        del payload["TopicArn"]
        with patch("cases.inbound_views.verify_sns_message"):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/", payload, format="json"
            )
        assert response.status_code == 403

    def test_subscription_confirmation_pins_an_unpinned_mailbox(
        self, admin_client, org_a
    ):
        mailbox = _make_mailbox(org_a, topic_arn="")
        with (
            patch("cases.inbound_views.verify_sns_message"),
            patch("cases.inbound_views.confirm_subscription"),
        ):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {
                    "Type": "SubscriptionConfirmation",
                    "SubscribeURL": "https://x",
                    "TopicArn": SNS_TOPIC,
                },
                format="json",
            )
        assert response.status_code == 200, response.content
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == SNS_TOPIC

    def test_subscription_confirmation_cannot_repin_a_pinned_mailbox(
        self, admin_client, org_a
    ):
        """Once pinned, a confirmation from another topic must not steal the
        mailbox. Otherwise the pin is trivially resettable by an attacker."""
        mailbox = _make_mailbox(org_a, topic_arn=SNS_TOPIC)
        with (
            patch("cases.inbound_views.verify_sns_message"),
            patch("cases.inbound_views.confirm_subscription") as confirm,
        ):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {
                    "Type": "SubscriptionConfirmation",
                    "SubscribeURL": "https://x",
                    "TopicArn": OTHER_TOPIC,
                },
                format="json",
            )
        assert response.status_code == 403
        assert confirm.called is False, "must not fetch an unpinned SubscribeURL"
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == SNS_TOPIC

    def test_subscription_confirmation_without_a_topic_arn_pins_nothing(
        self, admin_client, org_a
    ):
        mailbox = _make_mailbox(org_a, topic_arn="")
        with (
            patch("cases.inbound_views.verify_sns_message"),
            patch("cases.inbound_views.confirm_subscription"),
        ):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox.id}/",
                {"Type": "SubscriptionConfirmation", "SubscribeURL": "https://x"},
                format="json",
            )
        assert response.status_code == 403
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""

    def test_pin_is_scoped_to_the_mailbox_not_shared_across_orgs(
        self, admin_client, org_a, org_b
    ):
        """org_b's mailbox pinned to its own topic must not accept org_a's."""
        mailbox_b = _make_mailbox(
            org_b, address="support@beta.com", topic_arn=OTHER_TOPIC
        )
        with patch("cases.inbound_views.verify_sns_message"):
            response = admin_client.post(
                f"/api/cases/inbound/{mailbox_b.id}/",
                _notification(SNS_TOPIC, _raw_email()),
                format="json",
            )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Mailbox list analytics: cases_last_30d / last_received_at, attributed to a
# mailbox via the EmailMessage.mailbox FK (set at ingest). Driven through the
# real pipeline so the FK is populated the way production populates it.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMailboxAnalytics:
    def _list(self, client):
        resp = client.get(MAILBOXES_URL)
        assert resp.status_code == 200
        return resp.json()

    def _row(self, body, address):
        return next((m for m in body["mailboxes"] if m["address"] == address), None)

    def test_cases_and_last_received_attributed(self, admin_client, org_a):
        mbx = _make_mailbox(org_a, address="support@acme.com")
        ingest(parse_raw_email(_raw_email(message_id="<a1@x.com>")), mbx)
        ingest(
            parse_raw_email(
                _raw_email(
                    message_id="<a2@x.com>",
                    from_="Other <o@x.com>",
                    subject="Second issue",
                )
            ),
            mbx,
        )
        body = self._list(admin_client)
        row = self._row(body, "support@acme.com")
        assert row["cases_last_30d"] == 2
        assert row["last_received_at"] is not None
        assert body["totals"]["cases_last_30d"] == 2

    def test_counts_isolated_between_mailboxes(self, admin_client, org_a):
        m1 = _make_mailbox(org_a, address="one@acme.com")
        _make_mailbox(org_a, address="two@acme.com")
        ingest(parse_raw_email(_raw_email(message_id="<b1@x.com>")), m1)
        body = self._list(admin_client)
        assert self._row(body, "one@acme.com")["cases_last_30d"] == 1
        two = self._row(body, "two@acme.com")
        assert two["cases_last_30d"] == 0
        assert two["last_received_at"] is None

    def test_reply_to_old_case_is_not_a_new_ticket(self, admin_client, org_a):
        mbx = _make_mailbox(org_a)
        first = ingest(parse_raw_email(_raw_email(message_id="<c1@x.com>")), mbx)
        Case.objects.filter(pk=first.case.pk).update(
            created_at=dj_now() - timedelta(days=40)
        )
        # A threaded reply arrives now: new EmailMessage, no new case.
        ingest(
            parse_raw_email(
                _raw_email(message_id="<c2@x.com>", in_reply_to="<c1@x.com>")
            ),
            mbx,
        )
        row = self._row(self._list(admin_client), "support@acme.com")
        assert row["cases_last_30d"] == 0  # no ticket created in the window
        assert row["last_received_at"] is not None  # but mail did arrive

    def test_dropped_mail_sets_last_received_but_no_ticket(self, admin_client, org_a):
        mbx = _make_mailbox(org_a)
        ingest(
            parse_raw_email(_raw_email(extra_headers="Auto-Submitted: auto-replied")),
            mbx,
        )
        row = self._row(self._list(admin_client), "support@acme.com")
        assert row["cases_last_30d"] == 0
        assert row["last_received_at"] is not None

    def test_totals_count_and_active(self, admin_client, org_a):
        _make_mailbox(org_a, address="live@acme.com", is_active=True)
        _make_mailbox(org_a, address="off@acme.com", is_active=False)
        totals = self._list(admin_client)["totals"]
        assert totals["count"] == 2
        assert totals["active"] == 1

    def test_cross_org_counts_isolated(self, admin_client, org_a, org_b):
        mbx_b = _make_mailbox(org_b, address="b@acme.com")
        # `ingest` writes the case and email rows for the mailbox's own org.
        with rls_org(org_b):
            ingest(parse_raw_email(_raw_email(message_id="<d1@x.com>")), mbx_b)
        body = self._list(admin_client)  # org_a admin
        assert all(m["address"] != "b@acme.com" for m in body["mailboxes"])
        assert body["totals"]["cases_last_30d"] == 0
