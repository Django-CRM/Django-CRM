"""Board write verbs answer a stranger the way GET does (D46).

Board GETs answered a non-member 404, but column create, card create, card
update and card delete answered 403, so anyone in the org could tell which
board and card ids exist. A 404 with a different body from a missing id's
told them the same thing, so the answers are compared whole.
"""

import uuid

import pytest

from tasks.models import Board, BoardColumn, BoardMember, BoardTask


@pytest.fixture
def board(admin_profile, admin_user, org_a):
    board = Board.objects.create(
        name="Private", owner=admin_profile, org=org_a, created_by=admin_user
    )
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    return board


@pytest.fixture
def column(board, org_a):
    return BoardColumn.objects.create(board=board, name="Col", order=1, org=org_a)


@pytest.fixture
def card(column, org_a, admin_user):
    return BoardTask.objects.create(
        column=column, title="Card", priority="low", org=org_a, created_by=admin_user
    )


def _join(board, profile, role="member"):
    BoardMember.objects.create(board=board, profile=profile, role=role)


class TestStrangerGets404:
    def test_column_create(self, user_client, board):
        response = user_client.post(
            f"/api/boards/{board.id}/columns/", {"name": "New"}, format="json"
        )
        assert response.status_code == 404
        assert board.columns.count() == 0

    def test_card_create(self, user_client, column):
        response = user_client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {"title": "New", "priority": "low"},
            format="json",
        )
        assert response.status_code == 404
        assert column.tasks.count() == 0

    def test_card_update(self, user_client, card):
        response = user_client.put(
            f"/api/boards/tasks/{card.id}/", {"title": "Changed"}, format="json"
        )
        assert response.status_code == 404
        card.refresh_from_db()
        assert card.title == "Card"

    def test_card_delete(self, user_client, card):
        response = user_client.delete(f"/api/boards/tasks/{card.id}/")
        assert response.status_code == 404
        assert BoardTask.objects.filter(id=card.id).exists()


class TestMembersStillAllowed:
    def test_member_updates_and_deletes_a_card(self, user_client, user_profile, card):
        _join(card.column.board, user_profile)
        response = user_client.put(
            f"/api/boards/tasks/{card.id}/", {"title": "Changed"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert user_client.delete(f"/api/boards/tasks/{card.id}/").status_code == 204

    def test_member_creates_a_card(self, user_client, user_profile, column):
        _join(column.board, user_profile)
        response = user_client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {"title": "New", "priority": "low"},
            format="json",
        )
        assert response.status_code == 201, response.content

    def test_plain_member_still_gets_403_on_column_create(
        self, user_client, user_profile, board
    ):
        """A member can see the board, so a refusal here reveals nothing."""
        _join(board, user_profile)
        response = user_client.post(
            f"/api/boards/{board.id}/columns/", {"name": "New"}, format="json"
        )
        assert response.status_code == 403

    def test_board_admin_creates_a_column(self, user_client, user_profile, board):
        _join(board, user_profile, role="admin")
        response = user_client.post(
            f"/api/boards/{board.id}/columns/", {"name": "New"}, format="json"
        )
        assert response.status_code == 201, response.content


def _calls(board, column, card):
    """(method, url for the real id, url for a missing id, body) per verb."""
    missing = uuid.uuid4()
    return [
        ("get", f"/api/boards/{board.id}/", f"/api/boards/{missing}/", None),
        ("put", f"/api/boards/{board.id}/", f"/api/boards/{missing}/", {"name": "x"}),
        ("delete", f"/api/boards/{board.id}/", f"/api/boards/{missing}/", None),
        (
            "get",
            f"/api/boards/{board.id}/columns/",
            f"/api/boards/{missing}/columns/",
            None,
        ),
        (
            "post",
            f"/api/boards/{board.id}/columns/",
            f"/api/boards/{missing}/columns/",
            {"name": "x"},
        ),
        (
            "get",
            f"/api/boards/columns/{column.id}/tasks/",
            f"/api/boards/columns/{missing}/tasks/",
            None,
        ),
        (
            "post",
            f"/api/boards/columns/{column.id}/tasks/",
            f"/api/boards/columns/{missing}/tasks/",
            {"title": "x", "priority": "low"},
        ),
        (
            "put",
            f"/api/boards/tasks/{card.id}/",
            f"/api/boards/tasks/{missing}/",
            {"title": "x"},
        ),
        (
            "delete",
            f"/api/boards/tasks/{card.id}/",
            f"/api/boards/tasks/{missing}/",
            None,
        ),
    ]


def test_hidden_and_missing_ids_get_the_same_answer(user_client, board, column, card):
    for method, real, missing, body in _calls(board, column, card):
        call = getattr(user_client, method)
        kwargs = {"format": "json"} if body is not None else {}
        hidden = call(real, body, **kwargs) if body is not None else call(real)
        absent = call(missing, body, **kwargs) if body is not None else call(missing)
        assert hidden.status_code == absent.status_code == 404, (method, real)
        assert hidden.json() == absent.json(), (method, real)
    assert Board.objects.filter(id=board.id).exists()
    assert BoardTask.objects.filter(id=card.id, title="Card").exists()
