"""D33: leaving a board takes you off its cards, in the same transaction.

A card may only be assigned to someone who can open its board (the owner or a
member), but that was checked only when assignees were written, so a removed
member stayed on every card they held and kept being shown as its assignee.
`tasks.signals.unassign_removed_member` runs on every way a membership row is
deleted; each path below is pinned, along with what must be left alone.
"""

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection, transaction

from common.models import Profile, User
from common.testing import set_rls_context
from tasks.models import Board, BoardColumn, BoardMember, BoardTask


@pytest.fixture
def board(org_a, admin_profile):
    board = Board.objects.create(name="Launch", owner=admin_profile, org=org_a)
    BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
    return board


@pytest.fixture
def column(board, org_a):
    return BoardColumn.objects.create(board=board, name="To Do", org=org_a)


def _card(column, *assignees, title="Card"):
    card = BoardTask.objects.create(column=column, title=title, org=column.org)
    card.assigned_to.add(*assignees)
    return card


def _join(board, profile, role="member"):
    return BoardMember.objects.create(board=board, profile=profile, role=role)


def _assignees(card):
    return set(card.assigned_to.values_list("id", flat=True))


@pytest.mark.django_db
class TestRemovalPaths:
    def test_instance_delete(self, board, column, user_profile, admin_profile):
        membership = _join(board, user_profile)
        card = _card(column, user_profile, admin_profile)

        membership.delete()

        assert _assignees(card) == {admin_profile.id}

    def test_queryset_delete(self, board, column, user_profile):
        _join(board, user_profile)
        card = _card(column, user_profile)

        BoardMember.objects.filter(board=board, profile=user_profile).delete()

        assert _assignees(card) == set()

    def test_members_remove(self, board, column, user_profile):
        _join(board, user_profile)
        card = _card(column, user_profile)

        board.members.remove(user_profile)

        assert _assignees(card) == set()

    def test_members_clear_spares_the_owner(
        self, board, column, user_profile, admin_profile
    ):
        """The owner opens the board without a membership row, so keeps cards."""
        _join(board, user_profile)
        card = _card(column, user_profile, admin_profile)

        board.members.clear()

        assert _assignees(card) == {admin_profile.id}

    def test_rolled_back_removal_keeps_the_assignment(
        self, board, column, user_profile
    ):
        """Same transaction: if the removal does not commit, neither does this."""
        membership = _join(board, user_profile)
        membership_pk = membership.pk
        card = _card(column, user_profile)

        with pytest.raises(RuntimeError):
            with transaction.atomic():
                membership.delete()
                raise RuntimeError

        assert BoardMember.objects.filter(pk=membership_pk).exists()
        assert _assignees(card) == {user_profile.id}


@pytest.mark.django_db
class TestLeftAlone:
    def test_other_boards_keep_the_person(
        self, board, column, user_profile, admin_profile, org_a
    ):
        other = Board.objects.create(name="Other", owner=admin_profile, org=org_a)
        _join(other, user_profile)
        other_card = _card(
            BoardColumn.objects.create(board=other, name="To Do", org=org_a),
            user_profile,
        )
        membership = _join(board, user_profile)
        _card(column, user_profile)

        membership.delete()

        assert _assignees(other_card) == {user_profile.id}

    def test_other_members_keep_their_cards(
        self, board, column, user_profile, admin_profile
    ):
        membership = _join(board, user_profile)
        card = _card(column, admin_profile)

        membership.delete()

        assert _assignees(card) == {admin_profile.id}


@pytest.mark.django_db
class TestExistingStaleRows:
    """The migration clears what the signal cannot: rows stored before it."""

    def _run(self, org):
        migration = importlib.import_module(
            "tasks.migrations.0014_unassign_non_members_from_board_cards"
        )
        # The function reads only `schema_editor.connection`.
        migration.unassign_non_members(
            django_apps, SimpleNamespace(connection=connection)
        )
        # The migration clears the RLS context when it finishes.
        set_rls_context(org)

    def test_non_member_is_removed_member_and_owner_stay(
        self, board, column, user_profile, admin_profile, org_a
    ):
        # A same-org profile who was never a member, stored directly as the
        # pre-D33 code allowed.
        stranger = Profile.objects.create(
            user=User.objects.create_user(email="stranger@test.com", password="x"),
            org=org_a,
            role="USER",
            is_active=True,
        )
        _join(board, user_profile)
        card = _card(column, user_profile, admin_profile, stranger)

        self._run(org_a)

        assert _assignees(card) == {user_profile.id, admin_profile.id}
