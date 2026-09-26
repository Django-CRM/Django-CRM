"""A board card is assigned only to people who can open its board.

``_set_card_assignees`` filtered the ids to active profiles in the org and
nothing else, so a card could be assigned to somebody who is not on the board.
They cannot open it (`BoardDetailView.get_object`: owner or member, with no
org-admin exception), and since card assignment started sending email they
were mailed the card's title and a link to a board that answers them 404.

A non-member is now dropped, the same way the view already drops an inactive
or another org's id: the rest of the write succeeds. The board rule has no
admin clause, so an org admin who is not on the board is dropped too.
"""

from unittest.mock import patch

import pytest

from common.models import Profile, User
from tasks.models import Board, BoardColumn, BoardMember, BoardTask

pytestmark = pytest.mark.django_db

NOTIFY_CARD = "tasks.views.board_views.send_board_card_email_to_assigned_user.delay"
FIELD = "assigned_to_ids"


@pytest.fixture
def board(org_a, admin_profile, admin_user):
    board = Board.objects.create(
        name="Board", owner=admin_profile, org=org_a, created_by=admin_user
    )
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    return board


@pytest.fixture
def column(board, org_a):
    return BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)


@pytest.fixture
def member(board, user_profile):
    BoardMember.objects.create(board=board, profile=user_profile, role="member")
    return user_profile


@pytest.fixture
def outsider(org_a):
    """An active member of the org who is not on the board."""
    user = User.objects.create_user(email="outsider@test.com", password="pw123456")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


@pytest.fixture
def other_admin(org_a):
    """An org admin who is not on the board, so cannot open it."""
    user = User.objects.create_user(email="boss@test.com", password="pw123456")
    return Profile.objects.create(user=user, org=org_a, role="ADMIN", is_active=True)


@pytest.fixture
def card(column, org_a, admin_user):
    return BoardTask.objects.create(
        column=column, title="Card", org=org_a, created_by=admin_user
    )


def _ids(*profiles):
    return sorted(str(p.id) for p in profiles)


def _create(client, column, *profiles):
    with patch(NOTIFY_CARD) as notify:
        response = client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {"title": "New", FIELD: _ids(*profiles)},
            format="json",
        )
    assert response.status_code == 201, response.data
    return BoardTask.objects.get(title="New"), notify


def _update(client, card, *profiles):
    with patch(NOTIFY_CARD) as notify:
        response = client.put(
            f"/api/boards/tasks/{card.id}/",
            {"title": "Card", FIELD: _ids(*profiles)},
            format="json",
        )
    assert response.status_code == 200, response.data
    return notify


class TestCreate:
    def test_a_member_is_assigned_and_emailed(
        self, admin_client, column, org_a, member
    ):
        card, notify = _create(admin_client, column, member)

        assert list(card.assigned_to.all()) == [member]
        notify.assert_called_once_with(_ids(member), str(card.id), str(org_a.id))

    def test_a_non_member_is_not_assigned_or_emailed(
        self, admin_client, column, org_a, member, outsider
    ):
        card, notify = _create(admin_client, column, member, outsider)

        assert list(card.assigned_to.all()) == [member]
        notify.assert_called_once_with(_ids(member), str(card.id), str(org_a.id))

    def test_only_non_members_sends_nothing(self, admin_client, column, outsider):
        card, notify = _create(admin_client, column, outsider)

        assert card.assigned_to.count() == 0
        notify.assert_not_called()

    def test_an_org_admin_off_the_board_is_not_assigned(
        self, admin_client, column, other_admin
    ):
        card, notify = _create(admin_client, column, other_admin)

        assert card.assigned_to.count() == 0
        notify.assert_not_called()

    def test_the_owner_counts_even_without_a_membership_row(
        self, user_client, column, board, member, admin_profile, org_a
    ):
        # The board's own rule reads `owner` OR a membership, so the assignee
        # check reads the same two. A member creates the card here, so the
        # owner is not the caller.
        BoardMember.objects.filter(board=board, profile=admin_profile).delete()

        card, notify = _create(user_client, column, admin_profile)

        assert list(card.assigned_to.all()) == [admin_profile]
        notify.assert_called_once_with(_ids(admin_profile), str(card.id), str(org_a.id))


class TestUpdate:
    def test_a_member_is_assigned_and_emailed(self, admin_client, card, org_a, member):
        notify = _update(admin_client, card, member)

        assert list(card.assigned_to.all()) == [member]
        notify.assert_called_once_with(_ids(member), str(card.id), str(org_a.id))

    def test_a_non_member_is_not_assigned_or_emailed(
        self, admin_client, card, member, outsider, org_a
    ):
        notify = _update(admin_client, card, member, outsider)

        assert list(card.assigned_to.all()) == [member]
        notify.assert_called_once_with(_ids(member), str(card.id), str(org_a.id))

    def test_an_org_admin_off_the_board_is_not_assigned(
        self, admin_client, card, other_admin
    ):
        notify = _update(admin_client, card, other_admin)

        assert card.assigned_to.count() == 0
        notify.assert_not_called()
