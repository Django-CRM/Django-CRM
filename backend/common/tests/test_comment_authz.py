"""A comment's target and owning org are the server's to set, not the client's.

``CommentSerializer`` listed ``object_id`` and ``org`` as ordinary writable
fields. The comment edit endpoints are gated on "you wrote it, or you are an
admin" and pass ``partial=True``, so an author could send a different
``object_id`` and move their own comment onto any record in the org, including
ones the access rules would refuse them, or send a different ``org`` and push
it into another tenant.

``Comment.clean()`` compares the new org against the new target's org, so a
consistent cross-org pair satisfied it and saved. Only the Postgres RLS policy
stopped that, and RLS is the safety net here, not the contract. The tests below
run on SQLite, where there is no RLS at all, which is exactly the point: they
assert the application refuses the write on its own.

The same change repairs the edit endpoints. Four of them use this serializer
non-partial, where ``object_id`` and ``org`` were required fields no honest
client sends, so every ordinary edit failed validation before reaching a save.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from rest_framework import status

from accounts.models import Account
from common.models import Comment
from tasks.models import Task


@pytest.fixture
def task_a(admin_user, org_a):
    return Task.objects.create(
        title="Original target", org=org_a, status="New", priority="Medium"
    )


@pytest.fixture
def other_task(admin_user, org_a):
    """A second record in the same org, the retarget destination."""
    return Task.objects.create(
        title="Somebody else's work", org=org_a, status="New", priority="Medium"
    )


@pytest.fixture
def comment_on_task(task_a, admin_profile, org_a):
    return Comment.objects.create(
        comment="Original text",
        content_type=ContentType.objects.get_for_model(Task),
        object_id=task_a.id,
        commented_by=admin_profile,
        org=org_a,
    )


@pytest.mark.django_db
class TestCommentCannotBeRetargeted:
    def test_object_id_in_the_body_is_ignored(
        self, admin_client, comment_on_task, task_a, other_task
    ):
        """The edit succeeds, but it edits the comment, not its target."""
        response = admin_client.put(
            f"/api/tasks/comment/{comment_on_task.id}/",
            {"comment": "Edited text", "object_id": str(other_task.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        comment_on_task.refresh_from_db()
        assert comment_on_task.comment == "Edited text"
        assert comment_on_task.object_id == task_a.id, (
            "the comment was moved onto another record by request body"
        )

    def test_org_in_the_body_is_ignored(
        self, admin_client, comment_on_task, org_a, org_b
    ):
        response = admin_client.put(
            f"/api/tasks/comment/{comment_on_task.id}/",
            {"comment": "Edited text", "org": str(org_b.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        comment_on_task.refresh_from_db()
        assert comment_on_task.org_id == org_a.id, (
            "the comment was moved into another org by request body"
        )

    def test_both_at_once_is_ignored(
        self, admin_client, comment_on_task, task_a, org_a, org_b, other_task
    ):
        """The consistent pair is the one that used to satisfy Comment.clean()."""
        response = admin_client.put(
            f"/api/tasks/comment/{comment_on_task.id}/",
            {
                "comment": "Edited text",
                "object_id": str(other_task.id),
                "org": str(org_b.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        comment_on_task.refresh_from_db()
        assert comment_on_task.object_id == task_a.id
        assert comment_on_task.org_id == org_a.id


@pytest.mark.django_db
class TestOrdinaryEditStillWorks:
    """The True direction. A guard that also blocks the honest edit is an outage."""

    def test_editing_only_the_text_succeeds(self, admin_client, comment_on_task):
        response = admin_client.put(
            f"/api/tasks/comment/{comment_on_task.id}/",
            {"comment": "Fixed my typo"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, (
            f"editing a comment answered {response.status_code}; "
            "object_id and org were required fields no client sends"
        )
        comment_on_task.refresh_from_db()
        assert comment_on_task.comment == "Fixed my typo"

    def test_a_third_party_still_cannot_edit(
        self, user_client, user_profile, task_a, comment_on_task
    ):
        """Author-or-admin gating is untouched by the field change. The third
        party can read the task: one who cannot gets the 404 a missing comment
        gets (`tasks/tests/test_attachment_and_comment_reach_only_visible_tasks.py`)."""
        task_a.assigned_to.add(user_profile)
        response = user_client.put(
            f"/api/tasks/comment/{comment_on_task.id}/",
            {"comment": "Not mine to edit"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        comment_on_task.refresh_from_db()
        assert comment_on_task.comment == "Original text"


@pytest.mark.django_db
class TestAccountCommentIsActuallyCreated:
    """``POST /api/accounts/<pk>/`` dropped every comment silently.

    ``object_id`` and ``org`` were required, the client sends neither, so
    ``is_valid()`` was False and the save was skipped inside an ``if``. The
    caller got a 200 and no comment. The dead ``save(account_id=...)`` under it
    would have raised ``TypeError`` had it ever been reached, since ``Comment``
    is generic and has no ``account_id``.
    """

    @pytest.fixture
    def account(self, org_a, admin_user):
        return Account.objects.create(
            name="Acme", email="acme@example.com", org=org_a, created_by=admin_user
        )

    def test_comment_is_persisted(self, admin_client, account, org_a):
        response = admin_client.post(
            f"/api/accounts/{account.id}/",
            {"comment": "Called them back"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        saved = Comment.objects.filter(
            content_type=ContentType.objects.get_for_model(Account),
            object_id=account.id,
        )
        assert saved.count() == 1, "the comment was accepted with a 200 and dropped"
        assert saved.first().comment == "Called them back"
        assert saved.first().org_id == org_a.id

    def test_the_target_comes_from_the_url_not_the_body(
        self, admin_client, account, other_task
    ):
        admin_client.post(
            f"/api/accounts/{account.id}/",
            {"comment": "Planted", "object_id": str(other_task.id)},
            format="json",
        )
        assert not Comment.objects.filter(object_id=other_task.id).exists()
