"""Each task board card says whether the viewer may move it (`can_move`).

The flag is `has_task_access`, the rule `TaskMoveView` asserts. A task has no
watcher, so the board's read rule (`visible_tasks_qs`) is that same rule and
every card a caller is shown is one they may move; a task outside it is not on
the board, and its move answers 403 as it did before. The flag is there so
both clients gate the drag on the one field every kanban card carries, and so
it follows the move rule if the two ever part.
"""

from types import SimpleNamespace

from django.db import connection
from django.test.utils import CaptureQueriesContext

from tasks.models import Task
from tasks.serializer import TaskKanbanCardSerializer


def _task(org, created_by, title="A task"):
    return Task.objects.create(
        title=title, status="New", priority="Low", org=org, created_by=created_by
    )


def _cards(client):
    response = client.get("/api/tasks/kanban/")
    assert response.status_code == 200, response.content
    return {
        card["id"]: card for col in response.json()["columns"] for card in col["tasks"]
    }


def _move(client, task):
    return client.patch(
        f"/api/tasks/{task.id}/move/", {"status": "In Progress"}, format="json"
    )


class TestTaskCardCanMove:
    def test_an_assignee_may_move_and_the_card_says_so(
        self, user_client, user_profile, admin_user, org_a
    ):
        task = _task(org_a, admin_user)
        task.assigned_to.add(user_profile)

        assert _cards(user_client)[str(task.id)]["can_move"] is True
        response = _move(user_client, task)
        assert response.status_code == 200, response.content
        assert response.json()["task"]["can_move"] is True

    def test_the_creator_may_move(self, user_client, regular_user, org_a):
        task = _task(org_a, regular_user)
        assert _cards(user_client)[str(task.id)]["can_move"] is True
        assert _move(user_client, task).status_code == 200

    def test_an_admin_may_move_every_card(self, admin_client, regular_user, org_a):
        task = _task(org_a, regular_user)
        assert _cards(admin_client)[str(task.id)]["can_move"] is True
        assert _move(admin_client, task).status_code == 200

    def test_a_superuser_on_a_member_profile_may_move(
        self, user_client, regular_user, admin_user, org_a
    ):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        task = _task(org_a, admin_user)
        assert _cards(user_client)[str(task.id)]["can_move"] is True
        assert _move(user_client, task).status_code == 200

    def test_a_task_the_caller_cannot_open_is_absent_and_refused(
        self, user_client, admin_user, org_a
    ):
        hidden = _task(org_a, admin_user)
        assert str(hidden.id) not in _cards(user_client)
        assert _move(user_client, hidden).status_code == 403
        hidden.refresh_from_db()
        assert hidden.status == "New"

    def test_the_rule_refuses_a_task_outside_it(
        self, user_profile, regular_user, admin_user, org_a
    ):
        hidden = _task(org_a, admin_user)
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        data = TaskKanbanCardSerializer(hidden, context={"request": viewer}).data
        assert data["can_move"] is False

    def test_without_a_request_the_flag_is_false(self, regular_user, org_a):
        task = _task(org_a, regular_user)
        assert TaskKanbanCardSerializer(task).data["can_move"] is False

    def test_the_flag_costs_no_query_per_card(
        self, user_profile, regular_user, admin_user, org_a
    ):
        for n in range(4):
            _task(org_a, admin_user, f"Held {n}").assigned_to.add(user_profile)
        tasks = list(
            Task.objects.filter(org=org_a)
            .select_related(
                "created_by", "stage", "account", "lead", "opportunity", "case"
            )
            .prefetch_related("assigned_to", "tags")
        )
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        TaskKanbanCardSerializer(tasks, many=True).data

        with CaptureQueriesContext(connection) as without_flag:
            TaskKanbanCardSerializer(tasks, many=True).data
        with CaptureQueriesContext(connection) as with_flag:
            data = TaskKanbanCardSerializer(
                tasks, many=True, context={"request": viewer}
            ).data
        assert all(card["can_move"] for card in data)
        assert len(with_flag) == len(without_flag)
