"""A deactivated member cannot be put on a record.

Cases, tasks, accounts and deals resolved ``assigned_to`` ids with
``is_active=True``; contacts and leads (create, PUT and PATCH) and the task
board's ``assigned_to_ids`` did not, so a record could be handed to somebody
who can no longer sign in to see it. An inactive id is now ignored, the same
way an id from another org is.
"""

import pytest

from common.models import Profile, User
from contacts.models import Contact
from leads.models import Lead
from tasks.models import Board, BoardColumn, BoardMember, BoardTask

SPECS = {
    "contact": (
        "/api/contacts/",
        {"first_name": "Jo", "last_name": "C", "email": "jo@example.com"},
        lambda org, user: Contact.objects.create(
            first_name="Jo", last_name="C", email="jo@example.com", org=org
        ),
        Contact,
    ),
    "lead": (
        "/api/leads/",
        {"first_name": "Jo", "last_name": "L", "email": "jo@example.com"},
        lambda org, user: Lead.objects.create(
            first_name="Jo",
            last_name="L",
            email="jo@example.com",
            org=org,
            created_by=user,
        ),
        Lead,
    ),
}


@pytest.fixture
def inactive_profile(org_a):
    user = User.objects.create_user(email="gone@test.com", password="testpass123")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=False)


def _ids(*profiles):
    return [str(p.id) for p in profiles]


@pytest.mark.django_db
@pytest.mark.parametrize("name", list(SPECS))
class TestInactiveAssignee:
    def test_create_attaches_the_active_and_ignores_the_inactive(
        self, admin_client, org_a, user_profile, inactive_profile, name
    ):
        url, body, _, model = SPECS[name]

        response = admin_client.post(
            url,
            {**body, "assigned_to": _ids(user_profile, inactive_profile)},
            format="json",
        )

        assert response.status_code == 200, response.data
        record = model.objects.get(org=org_a)
        assert list(record.assigned_to.all()) == [user_profile]

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_attaches_the_active_and_ignores_the_inactive(
        self,
        admin_client,
        admin_user,
        org_a,
        user_profile,
        inactive_profile,
        name,
        method,
    ):
        url, body, make, _ = SPECS[name]
        record = make(org_a, admin_user)

        response = getattr(admin_client, method)(
            f"{url}{record.id}/",
            {**body, "assigned_to": _ids(user_profile, inactive_profile)},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(record.assigned_to.all()) == [user_profile]


@pytest.mark.django_db
def test_board_task_ignores_an_inactive_assignee(
    admin_client, admin_profile, admin_user, org_a, user_profile, inactive_profile
):
    board = Board.objects.create(
        name="Board", owner=admin_profile, org=org_a, created_by=admin_user
    )
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    # Both on the board, since a card can be assigned only to its members, so
    # what refuses the inactive one here is `is_active`.
    for profile in (user_profile, inactive_profile):
        BoardMember.objects.create(board=board, profile=profile, role="member")
    column = BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)
    task = BoardTask.objects.create(
        column=column, title="Card", org=org_a, created_by=admin_user
    )

    response = admin_client.put(
        f"/api/boards/tasks/{task.id}/",
        {"title": "Card", "assigned_to_ids": _ids(user_profile, inactive_profile)},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert list(task.assigned_to.all()) == [user_profile]
