"""The board move takes the task's access rule, exactly (D42).

`TaskMoveView.patch` rewrites a task's status, stage and kanban order. It used
to gate on ``is_org_admin(...) or request.user.is_superuser`` plus creator or
assignee, while `tasks.access` gives a Django superuser nothing and
`TaskDetailView.patch` refuses them. So a superuser who is a plain member could
move, through the board, any task they could not edit through the detail
endpoint. These tests pin the move to `assert_task_access`.
"""

import pytest

from conftest import rls_org
from tasks.models import Task


def _move(client, task, status="In Progress"):
    return client.patch(
        f"/api/tasks/{task.id}/move/", {"status": status}, format="json"
    )


@pytest.fixture
def superuser(regular_user):
    """The `user_client` caller, still a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


def _task(org, created_by, title="A task"):
    return Task.objects.create(
        title=title, status="New", priority="Low", org=org, created_by=created_by
    )


@pytest.fixture
def others_task(admin_user, org_a):
    """A task the `user_client` caller neither created nor was handed."""
    return _task(org_a, admin_user, "Someone else's task")


def _assert_moved(task):
    task.refresh_from_db()
    assert task.status == "In Progress"


def _assert_unchanged(task):
    task.refresh_from_db()
    assert task.status == "New"


class TestSuperuserPlainMember:
    def test_refused_on_a_task_they_cannot_edit(
        self, superuser, user_client, others_task
    ):
        assert (
            user_client.patch(
                f"/api/tasks/{others_task.id}/",
                {"status": "In Progress"},
                format="json",
            ).status_code
            == 403
        )
        response = _move(user_client, others_task)
        assert response.status_code == 403, response.content
        _assert_unchanged(others_task)

    def test_allowed_on_a_task_they_created(
        self, superuser, user_client, regular_user, org_a
    ):
        task = _task(org_a, regular_user, "Mine")
        response = _move(user_client, task)
        assert response.status_code == 200, response.content
        _assert_moved(task)

    def test_allowed_on_a_task_they_are_assigned(
        self, superuser, user_client, user_profile, others_task
    ):
        others_task.assigned_to.add(user_profile)
        response = _move(user_client, others_task)
        assert response.status_code == 200, response.content
        _assert_moved(others_task)


class TestOrdinaryRoles:
    def test_admin_may_move_any_task(self, admin_client, regular_user, org_a):
        task = _task(org_a, regular_user, "A member's task")
        response = _move(admin_client, task)
        assert response.status_code == 200, response.content
        _assert_moved(task)

    def test_member_with_access_may_move(self, user_client, user_profile, others_task):
        others_task.assigned_to.add(user_profile)
        response = _move(user_client, others_task)
        assert response.status_code == 200, response.content
        _assert_moved(others_task)

    def test_member_without_access_is_refused(self, user_client, others_task):
        response = _move(user_client, others_task)
        assert response.status_code == 403, response.content
        _assert_unchanged(others_task)


@pytest.mark.parametrize("caller", ["member", "admin", "superuser"])
def test_another_orgs_task_is_404_for_everyone(
    caller, request, user_client, admin_client, user_b, org_b
):
    if caller == "superuser":
        request.getfixturevalue("superuser")
    client = admin_client if caller == "admin" else user_client
    with rls_org(org_b):
        foreign = _task(org_b, user_b, "Foreign")
    response = _move(client, foreign)
    assert response.status_code == 404, response.content
    with rls_org(org_b):
        _assert_unchanged(foreign)
