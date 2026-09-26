"""`/api/cases/comment/<pk>/` and `/api/cases/attachment/<pk>/` resolve
through a ticket the caller may open.

Both looked the row up across the whole org, in the generic tables every module
shares, and then answered 403 to a non-author. So the 403 confirmed that a
comment or attachment existed on a ticket (or a lead, or a deal) the caller
cannot open, and an author who had lost the ticket could still edit or delete
there. Now a row on a hidden ticket, or on another module's record, answers
exactly like an id that does not exist (compared whole: status and body), and
the author-or-admin 403 is reserved for readers of the ticket.
"""

import pytest
from crum import impersonate
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile

from cases.models import Case, CaseWatcher
from common.models import Attachments, Comment
from leads.models import Lead

MISSING = "00000000-0000-0000-0000-00000000c0de"
COMMENT = "/api/cases/comment/{}/"
ATTACHMENT = "/api/cases/attachment/{}/"


@pytest.fixture
def ticket(admin_user, org_a):
    return Case.objects.create(
        name="Payroll dispute",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def lead(org_a):
    return Lead.objects.create(title="A lead", first_name="Ay", org=org_a)


def _comment(record, author_profile, org):
    return Comment.objects.create(
        comment="Secret note",
        content_type=ContentType.objects.get_for_model(record),
        object_id=record.id,
        commented_by=author_profile,
        org=org,
    )


def _attachment(record, creator, org):
    # `BaseModel.save()` stamps `created_by` from crum's current user.
    with impersonate(creator):
        return Attachments.objects.create(
            file_name="payslip.pdf",
            attachment=SimpleUploadedFile("payslip.pdf", b"pdf"),
            content_type=ContentType.objects.get_for_model(record),
            object_id=record.id,
            org=org,
        )


def _whole(response):
    return response.status_code, response.content


def _comment_calls(client, pk):
    return [
        client.put(COMMENT.format(pk), {"comment": "edited"}, format="json"),
        client.patch(COMMENT.format(pk), {"comment": "edited"}, format="json"),
        client.delete(COMMENT.format(pk)),
    ]


class TestHiddenAnswersLikeMissing:
    def test_a_comment_on_a_ticket_the_caller_cannot_open(
        self, user_client, user_profile, ticket, org_a
    ):
        # Even their own comment, left before they lost the ticket.
        note = _comment(ticket, user_profile, org_a)
        for got, missing in zip(
            _comment_calls(user_client, note.id), _comment_calls(user_client, MISSING)
        ):
            assert got.status_code == 404
            assert _whole(got) == _whole(missing)
        note.refresh_from_db()
        assert note.comment == "Secret note"

    def test_a_comment_on_another_modules_record(
        self, admin_client, admin_profile, lead, org_a
    ):
        """Even an admin: this route is for ticket comments only."""
        note = _comment(lead, admin_profile, org_a)
        for got, missing in zip(
            _comment_calls(admin_client, note.id),
            _comment_calls(admin_client, MISSING),
        ):
            assert got.status_code == 404
            assert _whole(got) == _whole(missing)
        assert Comment.objects.filter(id=note.id).exists()

    def test_an_attachment_on_a_ticket_the_caller_cannot_open(
        self, user_client, regular_user, ticket, org_a
    ):
        att = _attachment(ticket, regular_user, org_a)
        got = user_client.delete(ATTACHMENT.format(att.id))
        missing = user_client.delete(ATTACHMENT.format(MISSING))
        assert got.status_code == 404
        assert _whole(got) == _whole(missing)
        assert Attachments.objects.filter(id=att.id).exists()

    def test_an_attachment_on_another_modules_record(
        self, admin_client, admin_user, lead, org_a
    ):
        att = _attachment(lead, admin_user, org_a)
        got = admin_client.delete(ATTACHMENT.format(att.id))
        missing = admin_client.delete(ATTACHMENT.format(MISSING))
        assert got.status_code == 404
        assert _whole(got) == _whole(missing)
        assert Attachments.objects.filter(id=att.id).exists()


class TestReadersOfTheTicket:
    @pytest.fixture
    def reader(self, user_profile, ticket, org_a):
        CaseWatcher.objects.create(case=ticket, profile=user_profile, org=org_a)
        return user_profile

    def test_a_reader_may_not_edit_someone_elses_comment(
        self, user_client, reader, admin_profile, ticket, org_a
    ):
        note = _comment(ticket, admin_profile, org_a)
        for response in _comment_calls(user_client, note.id):
            assert response.status_code == 403
        assert Comment.objects.get(id=note.id).comment == "Secret note"

    def test_a_reader_edits_and_deletes_their_own_comment(
        self, user_client, reader, ticket, org_a
    ):
        note = _comment(ticket, reader, org_a)
        response = user_client.patch(
            COMMENT.format(note.id), {"comment": "edited"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert Comment.objects.get(id=note.id).comment == "edited"
        assert user_client.delete(COMMENT.format(note.id)).status_code == 200
        assert not Comment.objects.filter(id=note.id).exists()

    def test_an_admin_edits_anyones_comment_on_a_ticket(
        self, admin_client, user_profile, ticket, org_a
    ):
        note = _comment(ticket, user_profile, org_a)
        response = admin_client.put(
            COMMENT.format(note.id), {"comment": "tidied"}, format="json"
        )
        assert response.status_code == 200, response.content

    def test_a_reader_may_not_delete_someone_elses_attachment(
        self, user_client, reader, admin_user, ticket, org_a
    ):
        att = _attachment(ticket, admin_user, org_a)
        assert user_client.delete(ATTACHMENT.format(att.id)).status_code == 403
        assert Attachments.objects.filter(id=att.id).exists()

    def test_a_reader_deletes_their_own_attachment(
        self, user_client, reader, regular_user, ticket, org_a
    ):
        att = _attachment(ticket, regular_user, org_a)
        assert user_client.delete(ATTACHMENT.format(att.id)).status_code == 200
        assert not Attachments.objects.filter(id=att.id).exists()
