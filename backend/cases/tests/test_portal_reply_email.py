"""An agent's public reply reaches the customer as a threaded email.

The email carries the reply itself, threads in the customer's mail client, and
is recorded as an outbound `EmailMessage` so the customer's email answer lands
back on the same case through the inbound pipeline.
"""

import uuid
from email.utils import make_msgid
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.mail.backends import locmem
from django.utils import timezone

from cases.inbound.parser import parse_raw_email
from cases.inbound.pipeline import ingest
from cases.inbound.threading import find_existing_case, short_case_id
from cases.models import Case, EmailMessage, InboundMailbox
from cases.tasks import THREAD_REFERENCES_TAIL, notify_portal_contacts
from common.models import Comment
from conftest import rls_org
from contacts.models import Contact

TOPIC = "arn:aws:sns:us-east-1:123456789012:inbound"


@pytest.fixture
def case(org_a):
    case = Case.objects.create(
        org=org_a, name="Printer on fire", status="Assigned", priority="Normal"
    )
    case.contacts.add(
        Contact.objects.create(
            org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
        )
    )
    return case


def _mailbox(org, address, **overrides):
    fields = {"provider": "ses", "topic_arn": TOPIC, "is_active": True}
    fields.update(overrides)
    with rls_org(org):
        return InboundMailbox.objects.create(org=org, address=address, **fields)


def _comment(case, text="We replaced the fuser.", **author):
    return Comment.objects.create(
        org=case.org,
        content_type=ContentType.objects.get_for_model(Case),
        object_id=case.id,
        comment=text,
        is_internal=author.pop("is_internal", False),
        **author,
    )


def _reply(case, admin_profile, text="We replaced the fuser."):
    comment = _comment(case, text, commented_by=admin_profile)
    mail.outbox.clear()
    return notify_portal_contacts(
        str(case.id), str(case.org_id), "reply", comment_id=str(comment.id)
    )


def _inbound(case, message_id, mailbox=None):
    return EmailMessage.objects.create(
        org=case.org,
        case=case,
        mailbox=mailbox,
        direction="inbound",
        message_id=message_id,
        from_address="pat@example.com",
        received_at=timezone.now(),
    )


def _headers():
    return mail.outbox[0].message()


@pytest.mark.django_db
class TestContent:
    def test_carries_the_reply_the_author_and_the_org(self, case, admin_profile):
        admin_profile.user.name = "Dana Agent"
        admin_profile.user.save()
        assert _reply(case, admin_profile) == 1
        body = mail.outbox[0].body
        assert "We replaced the fuser." in body
        assert "Dana Agent" in body
        assert case.org.name in body
        assert f"/portal/cases/{case.id}?org={case.org_id}" in body

    def test_reply_markup_is_escaped(self, case, admin_profile):
        _reply(case, admin_profile, text="<script>alert(1)</script>\nline two")
        body = mail.outbox[0].body
        assert "<script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;<br>line two" in body

    def test_a_contacts_reply_names_the_contact(self, case, org_a):
        pat = case.contacts.get()
        jo = Contact.objects.create(
            org=org_a, first_name="Jo", last_name="Blake", email="jo@example.com"
        )
        case.contacts.add(jo)
        comment = _comment(case, "Still broken", commented_by_contact=pat)
        mail.outbox.clear()
        notify_portal_contacts(
            str(case.id),
            str(org_a.id),
            "reply",
            actor_contact_id=str(pat.id),
            comment_id=str(comment.id),
        )
        assert [m.to for m in mail.outbox] == [["jo@example.com"]]
        assert "Pat Smith replied" in mail.outbox[0].body

    def test_status_email_keeps_its_content_and_threads(self, case):
        mail.outbox.clear()
        notify_portal_contacts(str(case.id), str(case.org_id), "status")
        assert "is now <strong>Assigned</strong>" in mail.outbox[0].body
        assert mail.outbox[0].subject.endswith(f"[Case #{short_case_id(case)}]")
        assert _headers()["Message-ID"]
        assert EmailMessage.objects.get(direction="outbound").case == case


@pytest.mark.django_db
class TestInternalNeverEmails:
    def test_internal_comment_sends_nothing(self, case, admin_profile):
        note = _comment(case, "secret", commented_by=admin_profile, is_internal=True)
        mail.outbox.clear()
        sent = notify_portal_contacts(
            str(case.id), str(case.org_id), "reply", comment_id=str(note.id)
        )
        assert sent == 0
        assert mail.outbox == []
        assert not EmailMessage.objects.filter(direction="outbound").exists()

    def test_reply_turned_internal_before_the_task_ran(self, case, admin_profile):
        comment = _comment(case, "oops", commented_by=admin_profile)
        comment.is_internal = True
        comment.save()
        mail.outbox.clear()
        notify_portal_contacts(
            str(case.id), str(case.org_id), "reply", comment_id=str(comment.id)
        )
        assert mail.outbox == []

    def test_comment_from_another_case_is_not_sent(self, case, org_a, admin_profile):
        other = Case.objects.create(
            org=org_a, name="Other", status="New", priority="Normal"
        )
        foreign = _comment(other, "not yours", commented_by=admin_profile)
        mail.outbox.clear()
        notify_portal_contacts(
            str(case.id), str(case.org_id), "reply", comment_id=str(foreign.id)
        )
        assert mail.outbox == []


@pytest.mark.django_db
class TestThreadingHeaders:
    def test_first_reply_on_a_case_with_no_email(self, case, admin_profile, settings):
        """No email to answer yet, so no In-Reply-To. References carries the
        thread root minted for the case, so a reply threads by header even if
        the provider rewrote this email's Message-ID."""
        settings.DEFAULT_FROM_EMAIL = "Support <help@mail.example.org>"
        _reply(case, admin_profile)
        message = _headers()
        case.refresh_from_db()
        assert message["Message-ID"].endswith("@mail.example.org>")
        assert message["In-Reply-To"] is None
        assert case.external_thread_id.endswith("@mail.example.org")
        assert message["References"] == f"<{case.external_thread_id}>"
        assert mail.outbox[0].subject == (
            f"Re: Printer on fire [Case #{short_case_id(case)}]"
        )

    def test_second_reply_answers_the_first(self, case, admin_profile):
        _reply(case, admin_profile)
        first_id = _headers()["Message-ID"]
        root = Case.objects.get(pk=case.pk).external_thread_id
        _reply(case, admin_profile, text="Fixed now.")
        message = _headers()
        assert message["In-Reply-To"] == first_id
        assert message["References"].split() == [f"<{root}>", first_id]
        assert message["Message-ID"] != first_id
        # Minted once; the second email reuses it.
        assert Case.objects.get(pk=case.pk).external_thread_id == root

    def test_case_born_from_email_threads_under_the_customer(self, case, admin_profile):
        case.external_thread_id = "root@customer.example"
        case.save()
        _inbound(case, "root@customer.example")
        _inbound(case, "followup@customer.example")
        _reply(case, admin_profile)
        message = _headers()
        assert message["In-Reply-To"] == "<followup@customer.example>"
        assert message["References"].split() == [
            "<root@customer.example>",
            "<followup@customer.example>",
        ]

    def test_references_are_bounded_to_root_and_latest(self, case, admin_profile):
        case.external_thread_id = "root@x"
        case.save()
        for n in range(30):
            _inbound(case, f"m{n}@x")
        _reply(case, admin_profile)
        refs = _headers()["References"].split()
        assert len(refs) == THREAD_REFERENCES_TAIL + 1
        assert refs[0] == "<root@x>"
        assert refs[-1] == "<m29@x>"
        assert _headers()["In-Reply-To"] == "<m29@x>"

    def test_malformed_stored_ids_stay_out_of_the_headers(self, case, admin_profile):
        """Stored ids come from inbound mail, which the sender controls."""
        case.external_thread_id = "bad id>\nBcc: x@evil.test"
        case.save()
        _inbound(case, "also bad@x")
        _reply(case, admin_profile)
        message = _headers()
        assert message["In-Reply-To"] is None
        assert message["References"] is None
        assert message["Bcc"] is None


@pytest.mark.django_db
class TestSubject:
    def test_tag_is_not_duplicated(self, case, admin_profile):
        tag = f"[Case #{short_case_id(case)}]"
        case.name = f"Printer {tag}"
        case.save()
        _reply(case, admin_profile)
        assert mail.outbox[0].subject == f"Re: Printer {tag}"

    def test_another_cases_tag_is_replaced(self, case, admin_profile):
        case.name = "Re: Printer [Case #deadbeef]"
        case.save()
        _reply(case, admin_profile)
        assert mail.outbox[0].subject == (
            f"Re: Re: Printer [Case #{short_case_id(case)}]"
        )

    def test_newlines_in_the_name_cannot_reach_the_headers(self, case, admin_profile):
        case.name = "Help\r\nBcc: victim@evil.test"
        case.save()
        assert _reply(case, admin_profile) == 1
        message = _headers()
        assert "\n" not in mail.outbox[0].subject
        assert "\r" not in mail.outbox[0].subject
        assert message["Bcc"] is None
        assert mail.outbox[0].recipients() == ["pat@example.com"]

    def test_newlines_in_the_contact_name_stay_in_the_body(self, case, admin_profile):
        contact = case.contacts.get()
        contact.first_name = "Pat\r\nBcc: victim@evil.test"
        contact.save()
        _reply(case, admin_profile)
        assert _headers()["Bcc"] is None
        assert mail.outbox[0].recipients() == ["pat@example.com"]


@pytest.mark.django_db
class TestReplyTo:
    def test_mailbox_the_latest_inbound_email_came_through(self, case, admin_profile):
        first = _mailbox(case.org, "sales@acme.test")
        second = _mailbox(case.org, "help@acme.test")
        _inbound(case, "a@x", mailbox=first)
        _inbound(case, "b@x", mailbox=second)
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == ["help@acme.test"]
        assert "You can reply to this email" in mail.outbox[0].body

    def test_inactive_mailbox_falls_back_to_the_orgs_only_one(
        self, case, admin_profile
    ):
        retired = _mailbox(case.org, "old@acme.test", is_active=False)
        _mailbox(case.org, "help@acme.test")
        _inbound(case, "a@x", mailbox=retired)
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == ["help@acme.test"]

    def test_the_orgs_only_mailbox(self, case, admin_profile):
        _mailbox(case.org, "help@acme.test")
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == ["help@acme.test"]

    def test_two_mailboxes_and_no_inbound_history_means_none(self, case, admin_profile):
        _mailbox(case.org, "help@acme.test")
        _mailbox(case.org, "sales@acme.test")
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == []
        assert "Please do not reply to this email" in mail.outbox[0].body

    def test_no_mailbox_means_none(self, case, admin_profile):
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == []
        assert "Please do not reply to this email" in mail.outbox[0].body

    def test_mailbox_that_cannot_receive_is_not_offered(self, case, admin_profile):
        """The webhook refuses mail for an unpinned or non-SES mailbox."""
        _mailbox(case.org, "unpinned@acme.test", topic_arn="")
        _mailbox(case.org, "imap@acme.test", provider="imap")
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == []

    def test_another_orgs_mailbox_is_never_chosen(self, case, admin_profile, org_b):
        foreign = _mailbox(org_b, "theirs@other.test")
        # Even a row wrongly pointing at it does not make it this case's.
        _inbound(case, "a@x", mailbox=foreign)
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == []

    def test_another_orgs_only_mailbox_is_not_this_orgs_only_one(
        self, case, admin_profile, org_b
    ):
        _mailbox(org_b, "theirs@other.test")
        _reply(case, admin_profile)
        assert mail.outbox[0].reply_to == []


@pytest.mark.django_db
class TestOutboundRecord:
    def test_one_row_per_email_sent(self, case, admin_profile, org_a):
        mailbox = _mailbox(org_a, "help@acme.test")
        case.contacts.add(
            Contact.objects.create(
                org=org_a, first_name="Jo", last_name="B", email="jo@example.com"
            )
        )
        assert _reply(case, admin_profile) == 2
        rows = EmailMessage.objects.filter(case=case, direction="outbound")
        assert rows.count() == 2
        sent_ids = {m.message()["Message-ID"].strip("<>") for m in mail.outbox}
        assert set(rows.values_list("message_id", flat=True)) == sent_ids
        row = rows.get(to_addresses="jo@example.com")
        assert row.org == org_a
        assert row.mailbox == mailbox
        assert row.body_text == "We replaced the fuser."
        assert row.subject == mail.outbox[0].subject
        assert row.from_address

    def test_send_failure_is_logged_without_the_address_and_others_still_go(
        self, case, admin_profile, org_a, caplog
    ):
        case.contacts.add(
            Contact.objects.create(
                org=org_a, first_name="Jo", last_name="B", email="jo@example.com"
            )
        )
        comment = _comment(case, commented_by=admin_profile)
        mail.outbox.clear()
        real_send = mail.EmailMessage.send

        def flaky(self, *args, **kwargs):
            if self.to == ["pat@example.com"]:
                raise RuntimeError("rejected pat@example.com")
            return real_send(self, *args, **kwargs)

        with patch.object(mail.EmailMessage, "send", flaky):
            sent = notify_portal_contacts(
                str(case.id), str(org_a.id), "reply", comment_id=str(comment.id)
            )
        assert sent == 1
        assert [m.to for m in mail.outbox] == [["jo@example.com"]]
        rows = EmailMessage.objects.filter(direction="outbound")
        assert list(rows.values_list("to_addresses", flat=True)) == ["jo@example.com"]
        assert "send failed" in caplog.text
        assert "pat@example.com" not in caplog.text

    def test_outbound_rows_stay_out_of_the_case_feed(
        self, case, admin_profile, admin_client
    ):
        """The reply is already in the feed as a comment."""
        _reply(case, admin_profile)
        _inbound(case, "in@x")
        response = admin_client.get(f"/api/cases/{case.id}/")
        assert response.status_code == 200
        assert [e["direction"] for e in response.json()["email_messages"]] == [
            "inbound"
        ]


def _raw_reply(*, subject, in_reply_to="", references=""):
    lines = [
        "From: Pat Smith <pat@example.com>",
        "To: help@acme.test",
        f"Subject: {subject}",
        "Date: Sat, 26 Sep 2026 12:00:00 +0000",
        f"Message-ID: {make_msgid(domain='customer.example')}",
    ]
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    lines += ["MIME-Version: 1.0", 'Content-Type: text/plain; charset="utf-8"']
    return "\r\n".join(lines) + "\r\n\r\nThanks, that worked."


@pytest.mark.django_db
class TestCustomerAnswerThreadsBack:
    def test_reply_to_the_sent_message_id_lands_on_the_case(
        self, case, admin_profile, org_a
    ):
        mailbox = _mailbox(org_a, "help@acme.test")
        _reply(case, admin_profile)
        sent_id = _headers()["Message-ID"]
        parsed = parse_raw_email(
            _raw_reply(subject="Re: Printer on fire", in_reply_to=sent_id)
        )
        assert find_existing_case(parsed, org_a).case == case
        result = ingest(parsed, mailbox)
        assert result.case == case
        assert result.created_case is False
        assert Case.objects.filter(org=org_a).count() == 1

    def test_headers_stripped_but_subject_tag_kept(self, case, admin_profile, org_a):
        mailbox = _mailbox(org_a, "help@acme.test")
        _reply(case, admin_profile)
        result = ingest(
            parse_raw_email(_raw_reply(subject=mail.outbox[0].subject)), mailbox
        )
        assert result.case == case
        assert result.created_case is False

    def test_subject_tag_finds_the_case_among_many(self, org_a):
        """The fallback used to look at 200 of the org's cases and stop."""
        Case.objects.bulk_create(
            Case(
                id=uuid.UUID(int=n),
                org=org_a,
                name=f"c{n}",
                status="New",
                priority="Normal",
            )
            for n in range(1, 251)
        )
        target = Case.objects.create(
            id=uuid.UUID("ffffffff-0000-4000-8000-000000000001"),
            org=org_a,
            name="Last",
            status="New",
            priority="Normal",
        )
        parsed = parse_raw_email(_raw_reply(subject="Re: Last [Case #ffffffff]"))
        assert find_existing_case(parsed, org_a).case == target

    def test_a_reply_carrying_only_the_thread_root_threads_by_header(
        self, case, admin_profile, org_a
    ):
        """What a customer's reply looks like when the stored Message-ID does
        not match the one they received: an unknown In-Reply-To, and our own
        root in References, which a mail client copies forward."""
        _reply(case, admin_profile)
        references = _headers()["References"]
        parsed = parse_raw_email(
            _raw_reply(
                subject="Re: Printer on fire",
                in_reply_to="<unknown@elsewhere.amazonses.com>",
                references=f"{references} <unknown@elsewhere.amazonses.com>",
            )
        )
        match = find_existing_case(parsed, org_a)
        assert match.case == case
        assert match.by_header is True

    def test_subject_tag_never_crosses_orgs(self, case, org_b):
        tag = short_case_id(case)
        parsed = parse_raw_email(_raw_reply(subject=f"Re: x [Case #{tag}]"))
        assert find_existing_case(parsed, org_b) is None


class FakeSESBackend(locmem.EmailBackend):
    """Delivers like locmem, then reports a fresh provider id the way
    django_ses.SESBackend does after a successful send."""

    issued: list[str] = []

    def send_messages(self, messages):
        sent = super().send_messages(messages)
        for message in messages:
            ses_id = f"0109019a2b3c4d5e-{uuid.uuid4()}-000000"
            FakeSESBackend.issued.append(ses_id)
            message.extra_headers["message_id"] = ses_id
        return sent


def _ses_id():
    return FakeSESBackend.issued[-1]


@pytest.mark.django_db
class TestSesReplacesTheMessageId:
    """SES overwrites the Message-ID we supply, so the one worth storing is the
    one the customer receives and quotes back in In-Reply-To."""

    @pytest.fixture(autouse=True)
    def ses(self, settings):
        settings.EMAIL_BACKEND = f"{__name__}.FakeSESBackend"

    @pytest.mark.parametrize(
        "region, host",
        [
            ("ap-south-1", "ap-south-1.amazonses.com"),
            ("eu-west-1", "eu-west-1.amazonses.com"),
            ("us-east-1", "email.amazonses.com"),
        ],
    )
    def test_stores_the_id_ses_sent_under(
        self, case, admin_profile, settings, region, host
    ):
        settings.AWS_SES_REGION_NAME = region
        _reply(case, admin_profile)
        row = EmailMessage.objects.get(case=case, direction="outbound")
        assert row.message_id == f"{_ses_id()}@{host}"

    def test_region_unset_is_django_ses_default(self, case, admin_profile, settings):
        if hasattr(settings, "AWS_SES_REGION_NAME"):
            del settings.AWS_SES_REGION_NAME
        _reply(case, admin_profile)
        row = EmailMessage.objects.get(case=case, direction="outbound")
        assert row.message_id == f"{_ses_id()}@email.amazonses.com"

    def test_reply_quoting_the_ses_id_threads_by_header(
        self, case, admin_profile, org_a, settings
    ):
        """The first email on a portal-created case: its References holds only
        our root, and the customer's reply answers the SES id."""
        settings.AWS_SES_REGION_NAME = "ap-south-1"
        mailbox = _mailbox(org_a, "help@acme.test")
        _reply(case, admin_profile)
        parsed = parse_raw_email(
            _raw_reply(
                subject="Re: something else entirely",
                in_reply_to=f"<{_ses_id()}@ap-south-1.amazonses.com>",
            )
        )
        match = find_existing_case(parsed, org_a)
        assert match.case == case
        assert match.by_header is True
        result = ingest(parsed, mailbox)
        assert result.case == case
        assert result.created_case is False

    def test_second_email_answers_the_id_ses_sent_under(
        self, case, admin_profile, settings
    ):
        settings.AWS_SES_REGION_NAME = "ap-south-1"
        _reply(case, admin_profile)
        first = _ses_id()
        _reply(case, admin_profile, text="Fixed now.")
        assert _headers()["In-Reply-To"] == f"<{first}@ap-south-1.amazonses.com>"
