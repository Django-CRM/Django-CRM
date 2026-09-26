"""Assigning somebody to a task or a board card emails them, and nobody else.

Accounts, contacts, cases, leads and deals all mail a newly added assignee.
Tasks sent nothing on any path: ``tasks.celery_tasks`` had a ``send_email``
task that no view called, keyed on User ids rather than Profile ids and
linking to a hard-coded demo domain. Create, PUT and PATCH on a task, and
create and update on a board card, now enqueue one job naming each newly
added assignee, as the sibling modules do. The actor is not skipped, because
the siblings do not skip them either.
"""

from unittest.mock import patch

import pytest
from django.core import mail

from common.models import Profile, User
from tasks.celery_tasks import (
    send_board_card_email_to_assigned_user,
    send_email_to_assigned_user,
)
from tasks.models import Board, BoardColumn, BoardMember, BoardTask, Task

URL = "/api/tasks/"
BODY = {"title": "Task", "status": "New", "priority": "High"}
NOTIFY = "tasks.views.task_views.send_email_to_assigned_user.delay"
NOTIFY_CARD = "tasks.views.board_views.send_board_card_email_to_assigned_user.delay"


@pytest.fixture
def other_profile(org_a):
    user = User.objects.create_user(email="other@test.com", password="testpass123")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


@pytest.fixture
def task(org_a, admin_user, user_profile):
    task = Task.objects.create(
        title="Task", status="New", priority="High", org=org_a, created_by=admin_user
    )
    task.assigned_to.add(user_profile)
    return task


@pytest.fixture
def column(org_a, admin_profile, admin_user, user_profile, other_profile):
    board = Board.objects.create(
        name="Board", owner=admin_profile, org=org_a, created_by=admin_user
    )
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    # A card can be assigned only to people on its board.
    for profile in (user_profile, other_profile):
        BoardMember.objects.create(board=board, profile=profile, role="member")
    return BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)


@pytest.fixture
def card(column, org_a, admin_user, user_profile):
    card = BoardTask.objects.create(
        column=column, title="Card", org=org_a, created_by=admin_user
    )
    card.assigned_to.add(user_profile)
    return card


def _ids(*profiles):
    return sorted(str(p.id) for p in profiles)


@pytest.mark.django_db
class TestTaskCreateNotifies:
    def test_each_assignee_is_named_once(
        self, admin_client, org_a, user_profile, other_profile
    ):
        with patch(NOTIFY) as notify:
            response = admin_client.post(
                URL,
                {**BODY, "assigned_to": _ids(user_profile, other_profile)},
                format="json",
            )

        assert response.status_code == 200, response.data
        task = Task.objects.get(org=org_a)
        notify.assert_called_once_with(
            _ids(user_profile, other_profile), str(task.id), str(org_a.id)
        )

    def test_no_assignees_enqueues_nothing(self, admin_client):
        with patch(NOTIFY) as notify:
            response = admin_client.post(URL, BODY, format="json")

        assert response.status_code == 200, response.data
        notify.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["put", "patch"])
class TestTaskUpdateNotifies:
    def test_an_added_assignee_is_the_only_one_named(
        self, admin_client, task, org_a, user_profile, other_profile, method
    ):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{task.id}/",
                {**BODY, "assigned_to": _ids(user_profile, other_profile)},
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_called_once_with(
            [str(other_profile.id)], str(task.id), str(org_a.id)
        )

    def test_unchanged_assignees_enqueue_nothing(
        self, admin_client, task, user_profile, method
    ):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{task.id}/",
                {**BODY, "assigned_to": _ids(user_profile)},
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_not_called()

    def test_a_removed_assignee_enqueues_nothing(self, admin_client, task, method):
        with patch(NOTIFY) as notify:
            response = getattr(admin_client, method)(
                f"{URL}{task.id}/", {**BODY, "assigned_to": []}, format="json"
            )

        assert response.status_code == 200, response.data
        assert task.assigned_to.count() == 0
        notify.assert_not_called()


@pytest.mark.django_db
def test_patch_without_assigned_to_enqueues_nothing(admin_client, task, user_profile):
    with patch(NOTIFY) as notify:
        response = admin_client.patch(
            f"{URL}{task.id}/", {"title": "Renamed"}, format="json"
        )

    assert response.status_code == 200, response.data
    assert list(task.assigned_to.all()) == [user_profile]
    notify.assert_not_called()


@pytest.mark.django_db
class TestBoardCardNotifies:
    def test_create_names_each_assignee(
        self, admin_client, column, org_a, user_profile, other_profile
    ):
        with patch(NOTIFY_CARD) as notify:
            response = admin_client.post(
                f"/api/boards/columns/{column.id}/tasks/",
                {"title": "New", "assigned_to_ids": _ids(user_profile, other_profile)},
                format="json",
            )

        assert response.status_code == 201, response.data
        card = BoardTask.objects.get(title="New")
        notify.assert_called_once_with(
            _ids(user_profile, other_profile), str(card.id), str(org_a.id)
        )

    def test_create_without_assignees_enqueues_nothing(self, admin_client, column):
        with patch(NOTIFY_CARD) as notify:
            response = admin_client.post(
                f"/api/boards/columns/{column.id}/tasks/",
                {"title": "New"},
                format="json",
            )

        assert response.status_code == 201, response.data
        notify.assert_not_called()

    def test_update_names_only_the_added_assignee(
        self, admin_client, card, org_a, user_profile, other_profile
    ):
        with patch(NOTIFY_CARD) as notify:
            response = admin_client.put(
                f"/api/boards/tasks/{card.id}/",
                {"title": "Card", "assigned_to_ids": _ids(user_profile, other_profile)},
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_called_once_with(
            [str(other_profile.id)], str(card.id), str(org_a.id)
        )

    @pytest.mark.parametrize("body", [{"assigned_to_ids": []}, {}])
    def test_update_that_adds_nobody_enqueues_nothing(
        self, admin_client, card, user_profile, body
    ):
        with patch(NOTIFY_CARD) as notify:
            response = admin_client.put(
                f"/api/boards/tasks/{card.id}/",
                {"title": "Card", **body},
                format="json",
            )

        assert response.status_code == 200, response.data
        notify.assert_not_called()


@pytest.mark.django_db
class TestTheCeleryTasks:
    def test_task_email_goes_to_each_active_recipient(
        self, task, org_a, user_profile, other_profile, admin_profile
    ):
        admin_profile.is_active = False
        admin_profile.save(update_fields=["is_active"])

        send_email_to_assigned_user(
            _ids(user_profile, other_profile, admin_profile),
            str(task.id),
            str(org_a.id),
        )

        assert sorted(m.to[0] for m in mail.outbox) == sorted(
            [user_profile.user.email, other_profile.user.email]
        )
        assert all(f"/tasks/{task.id}" in m.body for m in mail.outbox)

    def test_task_email_ignores_another_orgs_profile(self, task, org_a, profile_b):
        send_email_to_assigned_user(_ids(profile_b), str(task.id), str(org_a.id))

        assert mail.outbox == []

    def test_card_email_links_to_its_board(self, card, org_a, user_profile):
        send_board_card_email_to_assigned_user(
            _ids(user_profile), str(card.id), str(org_a.id)
        )

        assert [m.to for m in mail.outbox] == [[user_profile.user.email]]
        assert f"/tasks/board?board={card.column.board_id}" in mail.outbox[0].body
