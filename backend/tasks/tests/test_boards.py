import uuid

import pytest

from tasks.models import Board, BoardColumn, BoardMember, BoardTask


@pytest.mark.django_db
class TestBoardListView:
    """Tests for GET /api/boards/ and POST /api/boards/."""

    def test_list_boards(self, admin_client, admin_profile, org_a):
        board = Board.objects.create(name="Test Board", owner=admin_profile, org=org_a)
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.get("/api/boards/")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert data["count"] >= 1

    def test_create_board(self, admin_client, org_a):
        response = admin_client.post(
            "/api/boards/",
            {"name": "New Board", "create_default_columns": False},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Board"
        assert Board.objects.filter(name="New Board", org=org_a).exists()

    def test_create_board_with_default_columns(self, admin_client, org_a):
        """Creating a board with default columns creates 3 columns."""
        response = admin_client.post(
            "/api/boards/",
            {"name": "Default Cols Board", "create_default_columns": True},
            format="json",
        )
        assert response.status_code == 201
        board = Board.objects.get(name="Default Cols Board")
        assert board.columns.count() == 3
        # Owner should be added as member
        assert BoardMember.objects.filter(board=board, role="owner").exists()

    def test_list_boards_search(self, admin_client, admin_profile, org_a):
        """Search filter on board name."""
        board = Board.objects.create(name="Alpha Board", owner=admin_profile, org=org_a)
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        board2 = Board.objects.create(name="Beta Board", owner=admin_profile, org=org_a)
        BoardMember.objects.create(board=board2, profile=admin_profile, role="owner")
        response = admin_client.get("/api/boards/?search=Alpha")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Alpha Board"

    def test_list_boards_filter_archived(self, admin_client, admin_profile, org_a):
        """Filter by archived status."""
        board = Board.objects.create(
            name="Active Board",
            owner=admin_profile,
            org=org_a,
            is_archived=False,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        archived_board = Board.objects.create(
            name="Archived Board",
            owner=admin_profile,
            org=org_a,
            is_archived=True,
        )
        BoardMember.objects.create(
            board=archived_board, profile=admin_profile, role="owner"
        )
        response = admin_client.get("/api/boards/?archived=true")
        assert response.status_code == 200
        data = response.json()
        names = [b["name"] for b in data["results"]]
        assert "Archived Board" in names
        assert "Active Board" not in names

    def test_list_boards_reports_owner_role(self, admin_client, admin_profile, org_a):
        """The requester's own role rides on each row as ``my_role``."""
        board = Board.objects.create(name="Owned Board", owner=admin_profile, org=org_a)
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.get("/api/boards/")
        assert response.status_code == 200
        row = next(b for b in response.json()["results"] if b["id"] == str(board.id))
        assert row["my_role"] == "owner"

    def test_list_boards_reports_member_role(
        self, user_client, admin_profile, user_profile, org_a
    ):
        """A plain member sees ``my_role == 'member'``. The frontend uses this
        to hide the owner/admin-only column controls it could never use."""
        board = Board.objects.create(
            name="Shared Board", owner=admin_profile, org=org_a
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardMember.objects.create(board=board, profile=user_profile, role="member")
        response = user_client.get("/api/boards/")
        assert response.status_code == 200
        row = next(b for b in response.json()["results"] if b["id"] == str(board.id))
        assert row["my_role"] == "member"

    def test_my_role_is_derived_not_client_supplied(
        self, user_client, admin_profile, user_profile, org_a
    ):
        """``my_role`` is server-derived: a member cannot inflate it to owner."""
        board = Board.objects.create(name="Role Board", owner=admin_profile, org=org_a)
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardMember.objects.create(board=board, profile=user_profile, role="member")
        # Even if the client sends its own my_role, the field is read-only.
        response = user_client.get("/api/boards/?my_role=owner")
        row = next(b for b in response.json()["results"] if b["id"] == str(board.id))
        assert row["my_role"] == "member"


@pytest.mark.django_db
class TestBoardDetailView:
    """Tests for GET/PUT/DELETE /api/boards/<pk>/."""

    def test_get_board(self, admin_client, admin_profile, admin_user, org_a):
        board = Board.objects.create(
            name="Detail Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.get(f"/api/boards/{board.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Detail Board"

    def test_update_board(self, admin_client, admin_profile, admin_user, org_a):
        board = Board.objects.create(
            name="Old Board Name",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.put(
            f"/api/boards/{board.id}/",
            {"name": "Updated Board Name"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Board Name"

    def test_delete_board(self, admin_client, admin_profile, admin_user, org_a):
        board = Board.objects.create(
            name="Delete Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.delete(f"/api/boards/{board.id}/")
        assert response.status_code == 204
        assert not Board.objects.filter(id=board.id).exists()

    def test_get_board_non_member_denied(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """Non-member should get 404 when accessing a board."""
        board = Board.objects.create(
            name="Private Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = user_client.get(f"/api/boards/{board.id}/")
        assert response.status_code == 404

    def test_update_board_member_role_forbidden(
        self, user_client, admin_profile, admin_user, user_profile, org_a
    ):
        """Member with 'member' role should get 403 when trying to update board."""
        board = Board.objects.create(
            name="Member Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardMember.objects.create(board=board, profile=user_profile, role="member")
        response = user_client.put(
            f"/api/boards/{board.id}/",
            {"name": "Should Fail"},
            format="json",
        )
        assert response.status_code == 403

    def test_delete_board_non_owner_forbidden(
        self, user_client, admin_profile, admin_user, user_profile, org_a
    ):
        """Only the owner should be able to delete a board."""
        board = Board.objects.create(
            name="Owner Delete Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardMember.objects.create(board=board, profile=user_profile, role="admin")
        response = user_client.delete(f"/api/boards/{board.id}/")
        assert response.status_code == 403
        assert Board.objects.filter(id=board.id).exists()

    def test_patch_board(self, admin_client, admin_profile, admin_user, org_a):
        """PATCH should allow partial board update."""
        board = Board.objects.create(
            name="Patch Board",
            description="Old desc",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.patch(
            f"/api/boards/{board.id}/",
            {"description": "New desc"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["description"] == "New desc"
        assert response.json()["name"] == "Patch Board"

    def test_patch_board_non_member_denied(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """PATCH by non-member should return 404."""
        board = Board.objects.create(
            name="Patch Deny Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = user_client.patch(
            f"/api/boards/{board.id}/",
            {"name": "Should Fail"},
            format="json",
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestBoardColumns:
    """Tests for GET/POST /api/boards/<board_pk>/columns/."""

    def test_create_column(self, admin_client, admin_profile, admin_user, org_a):
        board = Board.objects.create(
            name="Column Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = admin_client.post(
            f"/api/boards/{board.id}/columns/",
            {"name": "Backlog", "order": 0, "color": "#6B7280"},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Backlog"
        assert BoardColumn.objects.filter(board=board, name="Backlog").exists()

    def test_list_columns(self, admin_client, admin_profile, admin_user, org_a):
        board = Board.objects.create(
            name="List Column Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)
        BoardColumn.objects.create(board=board, name="Done", order=2, org=org_a)
        response = admin_client.get(f"/api/boards/{board.id}/columns/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_create_column_member_forbidden(
        self, user_client, admin_profile, admin_user, user_profile, org_a
    ):
        """Member with 'member' role should not be able to create columns."""
        board = Board.objects.create(
            name="Member Col Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardMember.objects.create(board=board, profile=user_profile, role="member")
        response = user_client.post(
            f"/api/boards/{board.id}/columns/",
            {"name": "Should Fail", "order": 0},
            format="json",
        )
        assert response.status_code == 403

    def test_list_columns_non_member_forbidden(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """Non-member should get 404 when listing columns."""
        board = Board.objects.create(
            name="Private Col Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        response = user_client.get(f"/api/boards/{board.id}/columns/")
        assert response.status_code == 404

    def test_create_duplicate_column_name_returns_400(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """A second column with the same name is a clean 400, not a 500.

        ``BoardColumn`` has ``unique_together = (board, name)``; without an
        explicit guard the duplicate hits the DB constraint and 500s, because
        ``board`` is read-only on the serializer so DRF cannot auto-validate it.
        """
        board = Board.objects.create(
            name="Dup Col Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)
        response = admin_client.post(
            f"/api/boards/{board.id}/columns/",
            {"name": "To Do", "order": 2},
            format="json",
        )
        assert response.status_code == 400
        assert "name" in response.json()["errors"]
        # Only the original column survives.
        assert BoardColumn.objects.filter(board=board, name="To Do").count() == 1


@pytest.mark.django_db
class TestBoardTasks:
    """Tests for board task endpoints."""

    def _create_board_with_column(self, admin_profile, admin_user, org_a):
        """Helper to create a board with one column and owner membership."""
        board = Board.objects.create(
            name="Task Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        column = BoardColumn.objects.create(
            board=board, name="To Do", order=1, org=org_a
        )
        return board, column

    def test_create_board_task(self, admin_client, admin_profile, admin_user, org_a):
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        response = admin_client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {"title": "New Card", "priority": "medium"},
            format="json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Card"
        assert BoardTask.objects.filter(column=column, title="New Card").exists()

    def test_get_board_task(self, admin_client, admin_profile, admin_user, org_a):
        """Verify a board task appears in the column task list."""
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        BoardTask.objects.create(
            column=column,
            title="Existing Card",
            priority="high",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.get(f"/api/boards/columns/{column.id}/tasks/")
        assert response.status_code == 200
        data = response.json()
        titles = [t["title"] for t in data]
        assert "Existing Card" in titles

    def test_delete_board_task(self, admin_client, admin_profile, admin_user, org_a):
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=column,
            title="Delete Card",
            priority="low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.delete(f"/api/boards/tasks/{task.id}/")
        assert response.status_code == 204
        assert not BoardTask.objects.filter(id=task.id).exists()

    def test_create_board_task_with_assigned_to_ids(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """A card created with ``assigned_to_ids`` is created with its assignee.

        This test used to create the card without the key and assign it by PUT,
        with a docstring recording that POST with ``assigned_to_ids`` failed.
        """
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        response = admin_client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {
                "title": "Assigned Card",
                "priority": "high",
                "assigned_to_ids": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        task = BoardTask.objects.get(title="Assigned Card")
        assert list(task.assigned_to.all()) == [admin_profile]
        assert [p["id"] for p in response.json()["assigned_to"]] == [
            str(admin_profile.id)
        ]

    def test_update_board_task(self, admin_client, admin_profile, admin_user, org_a):
        """PUT on a board task should update it."""
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=column,
            title="Update Card",
            priority="medium",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"title": "Updated Card", "priority": "high"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Updated Card"
        task.refresh_from_db()
        assert task.title == "Updated Card"

    def test_update_board_task_non_member_forbidden(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """Non-member should get 404, as GET answers them, when updating a board task."""
        board = Board.objects.create(
            name="Private Task Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        column = BoardColumn.objects.create(board=board, name="Col", order=1, org=org_a)
        task = BoardTask.objects.create(
            column=column,
            title="Private Card",
            priority="low",
            org=org_a,
            created_by=admin_user,
        )
        response = user_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"title": "Hacked Card"},
            format="json",
        )
        assert response.status_code == 404

    def test_get_tasks_non_member_forbidden(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """Non-member should get 404 when listing column tasks."""
        board = Board.objects.create(
            name="Private Task List Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        column = BoardColumn.objects.create(board=board, name="Col", order=1, org=org_a)
        response = user_client.get(f"/api/boards/columns/{column.id}/tasks/")
        assert response.status_code == 404

    def test_create_task_non_member_forbidden(
        self, user_client, admin_profile, admin_user, org_a
    ):
        """Non-member should get 404, as GET answers them, when creating a task in a column."""
        board = Board.objects.create(
            name="No Access Task Board",
            owner=admin_profile,
            org=org_a,
            created_by=admin_user,
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        column = BoardColumn.objects.create(board=board, name="Col", order=1, org=org_a)
        response = user_client.post(
            f"/api/boards/columns/{column.id}/tasks/",
            {"title": "Blocked Card", "priority": "low"},
            format="json",
        )
        assert response.status_code == 404

    def test_update_board_task_with_assigned_to(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """PUT on a board task should update assigned_to when assigned_to_ids is provided."""
        _board, column = self._create_board_with_column(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=column,
            title="Assign Update Card",
            priority="medium",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {
                "title": "Assign Update Card",
                "assigned_to_ids": [str(admin_profile.id)],
            },
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.assigned_to.count() == 1

    def _board_with_two_columns(self, admin_profile, admin_user, org_a):
        """Board (owner membership) with two ordered columns for move tests."""
        board = Board.objects.create(
            name="Move Board", owner=admin_profile, org=org_a, created_by=admin_user
        )
        BoardMember.objects.create(board=board, profile=admin_profile, role="owner")
        todo = BoardColumn.objects.create(board=board, name="To Do", order=1, org=org_a)
        doing = BoardColumn.objects.create(
            board=board, name="Doing", order=2, org=org_a
        )
        return board, todo, doing

    def test_move_board_task_to_another_column(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """PUT with a sibling column id relocates the card. The core kanban move.

        Regression: ``column`` was read-only on the serializer, so the change was
        silently dropped and the card never left its lane (200, but no move)."""
        _board, todo, doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=todo,
            title="Drag me",
            priority="medium",
            order=0,
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"column": str(doing.id), "order": 0},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.column_id == doing.id
        assert task.order == 0

    def test_move_to_column_on_another_board_rejected(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """A card cannot be moved into a column that belongs to another board."""
        _board, todo, _doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        other_board = Board.objects.create(
            name="Other Board", owner=admin_profile, org=org_a, created_by=admin_user
        )
        BoardMember.objects.create(
            board=other_board, profile=admin_profile, role="owner"
        )
        foreign_col = BoardColumn.objects.create(
            board=other_board, name="Elsewhere", order=1, org=org_a
        )
        task = BoardTask.objects.create(
            column=todo,
            title="Stay put",
            priority="low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"column": str(foreign_col.id)},
            format="json",
        )
        assert response.status_code == 400
        task.refresh_from_db()
        assert task.column_id == todo.id  # unchanged

    def test_move_to_nonexistent_column_rejected(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """A random column id is a 400, not a silent no-op."""
        _board, todo, _doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=todo,
            title="Stay",
            priority="low",
            org=org_a,
            created_by=admin_user,
        )
        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"column": str(uuid.uuid4())},
            format="json",
        )
        assert response.status_code == 400
        task.refresh_from_db()
        assert task.column_id == todo.id

    def test_reorder_within_column_resequences(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """Dropping a card at the top of its own lane renumbers to a contiguous
        0..n-1 with no duplicate order values."""
        _board, todo, _doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        a = BoardTask.objects.create(
            column=todo, title="A", order=0, org=org_a, created_by=admin_user
        )
        b = BoardTask.objects.create(
            column=todo, title="B", order=1, org=org_a, created_by=admin_user
        )
        c = BoardTask.objects.create(
            column=todo, title="C", order=2, org=org_a, created_by=admin_user
        )
        response = admin_client.put(
            f"/api/boards/tasks/{c.id}/",
            {"order": 0},
            format="json",
        )
        assert response.status_code == 200
        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()
        assert c.order == 0
        assert a.order == 1
        assert b.order == 2

    def test_move_recompacts_source_column(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        """When a card leaves a lane, the cards left behind close the gap."""
        _board, todo, doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        a = BoardTask.objects.create(
            column=todo, title="A", order=0, org=org_a, created_by=admin_user
        )
        b = BoardTask.objects.create(
            column=todo, title="B", order=1, org=org_a, created_by=admin_user
        )
        c = BoardTask.objects.create(
            column=todo, title="C", order=2, org=org_a, created_by=admin_user
        )
        response = admin_client.put(
            f"/api/boards/tasks/{b.id}/",
            {"column": str(doing.id), "order": 0},
            format="json",
        )
        assert response.status_code == 200
        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()
        assert b.column_id == doing.id and b.order == 0
        assert sorted([a.order, c.order]) == [0, 1]  # no gap where B was

    def test_cannot_overwrite_created_by_on_update(
        self, admin_client, admin_profile, admin_user, org_a, user_b
    ):
        """created_by is a server-derived audit fact. It FKs common.User, which
        RLS does not org-scope, so it must not be mass-assignable from the body."""
        _board, todo, _doing = self._board_with_two_columns(
            admin_profile, admin_user, org_a
        )
        task = BoardTask.objects.create(
            column=todo,
            title="Owned",
            priority="low",
            org=org_a,
        )
        # Seed created_by directly (BaseModel.save() derives it from the request
        # user, so it can't be set through the ORM in a test).
        BoardTask.objects.filter(pk=task.pk).update(created_by=admin_user)

        response = admin_client.put(
            f"/api/boards/tasks/{task.id}/",
            {"title": "Owned", "created_by": str(user_b.id)},
            format="json",
        )
        assert response.status_code == 200
        task.refresh_from_db()
        assert task.created_by_id == admin_user.id  # unchanged
        assert task.created_by_id != user_b.id  # the mass-assignment was rejected
