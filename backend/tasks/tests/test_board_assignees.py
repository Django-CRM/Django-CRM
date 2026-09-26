"""A board card's ``assigned_to_ids``: parsed first, org-scoped, active only.

POST with ``assigned_to_ids`` answered 500 whatever the ids were: the key rode
through ``BoardTaskSerializer.validated_data`` into ``BoardTask.objects.create``,
which has no such field. PUT survived that only because ``update`` sets it as a
plain attribute, and then read the raw body value into ``id__in`` itself, so a
multipart body (one bare string) was iterated a character at a time and
answered 500 as well. Both now parse the value through ``payload_id_list``
before the first write, like every other task write path.
"""

import pytest

from common.models import Profile, User
from tasks.models import Board, BoardColumn, BoardMember, BoardTask

FIELD = "assigned_to_ids"
MALFORMED = ["not-json", "[not json", ["nope"], {"a": 1}, [1]]


@pytest.fixture
def inactive_profile(org_a):
    user = User.objects.create_user(email="gone@test.com", password="testpass123")
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=False)


@pytest.fixture
def column(org_a, admin_profile, admin_user, user_profile, inactive_profile):
    board = Board.objects.create(
        name="Board", owner=admin_profile, org=org_a, created_by=admin_user
    )
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    # Only people on the board can be assigned a card on it
    # (test_board_card_assignee_membership.py). The inactive profile is on it
    # too, so the tests below still show `is_active` doing the refusing.
    BoardMember.objects.create(board=board, profile=user_profile, role="member")
    BoardMember.objects.create(board=board, profile=inactive_profile, role="member")
    return BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)


@pytest.fixture
def card(column, org_a, admin_user, admin_profile):
    card = BoardTask.objects.create(
        column=column, title="Before", org=org_a, created_by=admin_user
    )
    card.assigned_to.add(admin_profile)
    return card


def _create_url(column):
    return f"/api/boards/columns/{column.id}/tasks/"


def _detail_url(card):
    return f"/api/boards/tasks/{card.id}/"


@pytest.mark.django_db
class TestBoardCardCreateAssignees:
    def test_valid_ids_attach(self, admin_client, column, admin_profile, user_profile):
        response = admin_client.post(
            _create_url(column),
            {"title": "Card", FIELD: [str(admin_profile.id), str(user_profile.id)]},
            format="json",
        )

        assert response.status_code == 201, response.data
        card = BoardTask.objects.get(column=column, title="Card")
        assert set(card.assigned_to.all()) == {admin_profile, user_profile}

    def test_other_org_and_inactive_ids_are_ignored(
        self, admin_client, column, user_profile, profile_b, inactive_profile
    ):
        response = admin_client.post(
            _create_url(column),
            {
                "title": "Card",
                FIELD: [
                    str(user_profile.id),
                    str(profile_b.id),
                    str(inactive_profile.id),
                ],
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        card = BoardTask.objects.get(column=column, title="Card")
        assert list(card.assigned_to.all()) == [user_profile]

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_is_400_naming_the_field_and_creates_nothing(
        self, admin_client, column, value
    ):
        response = admin_client.post(
            _create_url(column), {"title": "Card", FIELD: value}, format="json"
        )

        assert response.status_code == 400
        assert FIELD in response.data["errors"]
        assert not BoardTask.objects.filter(column=column).exists()


@pytest.mark.django_db
class TestBoardCardUpdateAssignees:
    def test_valid_ids_replace_the_assignees(
        self, admin_client, card, admin_profile, user_profile
    ):
        response = admin_client.put(
            _detail_url(card),
            {"title": "Before", FIELD: [str(user_profile.id)]},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(card.assigned_to.all()) == [user_profile]

    def test_a_multipart_id_attaches(self, admin_client, card, user_profile):
        response = admin_client.put(
            _detail_url(card),
            {"title": "Before", FIELD: str(user_profile.id)},
            format="multipart",
        )

        assert response.status_code == 200, response.data
        assert list(card.assigned_to.all()) == [user_profile]

    def test_other_org_and_inactive_ids_are_ignored(
        self, admin_client, card, user_profile, profile_b, inactive_profile
    ):
        response = admin_client.put(
            _detail_url(card),
            {
                "title": "Before",
                FIELD: [
                    str(user_profile.id),
                    str(profile_b.id),
                    str(inactive_profile.id),
                ],
            },
            format="json",
        )

        assert response.status_code == 200, response.data
        assert list(card.assigned_to.all()) == [user_profile]

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_is_400_naming_the_field_and_changes_nothing(
        self, admin_client, card, admin_profile, value
    ):
        response = admin_client.put(
            _detail_url(card), {"title": "After", FIELD: value}, format="json"
        )

        assert response.status_code == 400
        assert FIELD in response.data["errors"]
        card.refresh_from_db()
        assert card.title == "Before"
        assert list(card.assigned_to.all()) == [admin_profile]

    def test_malformed_multipart_is_400_and_changes_nothing(
        self, admin_client, card, admin_profile
    ):
        response = admin_client.put(
            _detail_url(card), {"title": "After", FIELD: "nope"}, format="multipart"
        )

        assert response.status_code == 400
        assert FIELD in response.data["errors"]
        card.refresh_from_db()
        assert card.title == "Before"
        assert list(card.assigned_to.all()) == [admin_profile]

    def test_omitting_the_key_leaves_the_assignees(
        self, admin_client, card, admin_profile
    ):
        response = admin_client.put(
            _detail_url(card), {"title": "After"}, format="json"
        )

        assert response.status_code == 200, response.data
        assert list(card.assigned_to.all()) == [admin_profile]
