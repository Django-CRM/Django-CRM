"""Task comments and attachments are reached only through a task the caller
may open.

Both endpoints used to look the row up org-wide across every module and then
answer 403 to a non-author, which reached other modules' comments and files
and confirmed a hidden id. A missing id, another module's row, and one on a
task this caller cannot open now answer one identical 404.
"""

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile

from common.models import Attachments, Comment
from leads.models import Lead
from tasks.models import Task


def _owned(model, obj, user):
    model.objects.filter(pk=obj.pk).update(created_by=user)
    return obj


@pytest.fixture
def hidden_task(org_a, admin_user):
    task = Task.objects.create(
        title="Private", status="New", priority="Medium", org=org_a
    )
    return _owned(Task, task, admin_user)


@pytest.fixture
def hidden_lead(org_a, admin_user):
    lead = Lead.objects.create(first_name="Private", last_name="Lead", org=org_a)
    return _owned(Lead, lead, admin_user)


def _comment(target, author):
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(type(target)),
        object_id=target.id,
        comment="Original",
        commented_by=author,
        org=target.org,
    )


def _attach(target, uploader):
    attachment = Attachments(
        file_name="notes.txt",
        content_object=target,
        org=target.org,
        attachment=SimpleUploadedFile("notes.txt", b"hello"),
    )
    attachment.save()
    return _owned(Attachments, attachment, uploader)


def _same_as_missing(client, verb, url_of, row):
    body = {"comment": "x"}
    hidden = getattr(client, verb)(url_of(row.id), body, format="json")
    missing = getattr(client, verb)(url_of(uuid.uuid4()), body, format="json")
    assert hidden.status_code == missing.status_code == 404, verb
    assert hidden.json() == missing.json(), verb


def comment_url(pk):
    return f"/api/tasks/comment/{pk}/"


def attachment_url(pk):
    return f"/api/tasks/attachment/{pk}/"


@pytest.mark.django_db
class TestComments:
    @pytest.mark.parametrize("verb", ["put", "patch", "delete"])
    def test_comment_on_a_task_the_member_cannot_open(
        self, user_client, admin_profile, hidden_task, verb
    ):
        comment = _comment(hidden_task, admin_profile)

        _same_as_missing(user_client, verb, comment_url, comment)
        comment.refresh_from_db()
        assert comment.comment == "Original"

    @pytest.mark.parametrize("verb", ["put", "delete"])
    def test_the_members_own_comment_on_a_lead_they_lost(
        self, user_client, user_profile, hidden_lead, verb
    ):
        comment = _comment(hidden_lead, user_profile)

        _same_as_missing(user_client, verb, comment_url, comment)
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_another_modules_comment_even_for_an_admin(
        self, admin_client, admin_profile, hidden_lead
    ):
        comment = _comment(hidden_lead, admin_profile)

        _same_as_missing(admin_client, "delete", comment_url, comment)
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_a_reader_who_did_not_write_it_is_refused(
        self, user_client, user_profile, admin_profile, hidden_task
    ):
        hidden_task.assigned_to.add(user_profile)
        comment = _comment(hidden_task, admin_profile)

        response = user_client.delete(comment_url(comment.id))

        assert response.status_code == 403
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_the_author_who_can_read_it_may_edit(
        self, user_client, user_profile, hidden_task
    ):
        hidden_task.assigned_to.add(user_profile)
        comment = _comment(hidden_task, user_profile)

        response = user_client.put(
            comment_url(comment.id), {"comment": "Fixed"}, format="json"
        )

        assert response.status_code == 200, response.content


@pytest.mark.django_db
class TestAttachments:
    def test_a_file_on_a_task_the_member_cannot_open(
        self, user_client, admin_user, hidden_task
    ):
        attachment = _attach(hidden_task, admin_user)

        _same_as_missing(user_client, "delete", attachment_url, attachment)
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_the_uploader_of_a_hidden_leads_file_cannot_delete_it_here(
        self, user_client, regular_user, hidden_lead
    ):
        attachment = _attach(hidden_lead, regular_user)

        _same_as_missing(user_client, "delete", attachment_url, attachment)
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_another_modules_file_even_for_an_admin(
        self, admin_client, admin_user, hidden_lead
    ):
        attachment = _attach(hidden_lead, admin_user)

        _same_as_missing(admin_client, "delete", attachment_url, attachment)
        assert Attachments.objects.filter(pk=attachment.pk).exists()

    def test_the_uploader_who_can_read_the_task_may(
        self, user_client, user_profile, regular_user, hidden_task
    ):
        hidden_task.assigned_to.add(user_profile)
        attachment = _attach(hidden_task, regular_user)

        response = user_client.delete(attachment_url(attachment.id))

        assert response.status_code == 200, response.content
