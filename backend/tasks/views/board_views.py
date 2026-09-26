from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Profile
from common.permissions import HasOrgContext
from common.validators import payload_id_list
from tasks.celery_tasks import send_board_card_email_to_assigned_user
from tasks.models import Board, BoardColumn, BoardMember, BoardTask
from tasks.serializer import (
    BoardColumnSerializer,
    BoardListSerializer,
    BoardSerializer,
    BoardTaskSerializer,
)


def _resequence_column(column, moved_task=None, target_index=None):
    """Renumber a column's cards to a contiguous ``0..n-1`` ``order``.

    A board card carries a per-column ``order`` with no uniqueness constraint, so
    a naive "save the dropped index" leaves ties and gaps that make reloads
    non-deterministic. After any move/reorder we renumber the affected column(s)
    here: existing cards are taken in ``(order, created_at)`` order (deterministic
    tie-break) and, when a card was dropped into this column, it is spliced in at
    ``target_index`` (clamped to the column's bounds). This lets a single PUT both
    relocate a card and place it exactly, without a separate batch-reorder
    endpoint. Only rows whose ``order`` actually changes are written.
    """
    others = list(
        column.tasks.exclude(pk=moved_task.pk)
        if moved_task is not None
        else column.tasks.all()
    )
    others.sort(key=lambda t: (t.order, t.created_at))
    if moved_task is not None:
        idx = 0 if target_index is None else max(0, min(int(target_index), len(others)))
        others.insert(idx, moved_task)
    for i, task in enumerate(others):
        if task.order != i:
            BoardTask.objects.filter(pk=task.pk).update(order=i)
            if moved_task is not None and task.pk == moved_task.pk:
                moved_task.order = i


def _pop_assignee_ids(data):
    """Take ``assigned_to_ids`` out of a card write body, parsed.

    Returns ``None`` when the key is absent (leave the assignees alone), else
    the list of ids, raising a 400 naming the field on a malformed one. The key
    is removed from ``data`` so the serializer never passes it to the model:
    ``BoardTask.objects.create(assigned_to_ids=...)`` is a TypeError, which is
    why every POST that carried it answered 500.
    """
    if "assigned_to_ids" not in data:
        return None
    value = data.get("assigned_to_ids")
    data.pop("assigned_to_ids")
    return payload_id_list(value, "assigned_to_ids")


def _readable_boards(profile):
    """Boards ``profile`` may open: its owner or a member, with no org-admin
    exception, inside their org.

    Every board, column and card lookup goes through this queryset, so an id
    the caller may not open raises the same 404 as an id that does not exist.
    Checking access after an org-wide lookup gave the two different answers:
    first a 403 on the write verbs, then a 404 with a different body, and
    either one told anyone in the org which board and card ids exist.
    """
    return (
        Board.objects.filter(org=profile.org)
        .filter(Q(owner=profile) | Q(members=profile))
        .distinct()
    )


def _set_card_assignees(card, org, ids):
    """Replace the card's assignees with the profiles in ``ids`` who can open
    its board, and email the ones this write added.

    Other-org and inactive ids are ignored, as on every task write path, and so
    is anyone off the board: its owner or a member, the rule
    `BoardDetailView.get_object` applies, with no org-admin exception. Assigning
    anyone else would email them a card on a board that answers them 404.
    """
    board = card.column.board
    previous = set(card.assigned_to.values_list("id", flat=True))
    card.assigned_to.set(
        Profile.objects.filter(id__in=ids, org=org, is_active=True)
        .filter(Q(id=board.owner_id) | Q(board_memberships__board=board))
        .distinct()
    )
    added = set(card.assigned_to.values_list("id", flat=True)) - previous
    if added:
        send_board_card_email_to_assigned_user.delay(
            sorted(str(pk) for pk in added), str(card.id), str(org.id)
        )


class BoardListCreateView(APIView, LimitOffsetPagination):
    """List all boards or create a new board"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            ),
            OpenApiParameter(
                name="search",
                description="Search in board name",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="archived",
                description="Filter archived boards",
                required=False,
                type=bool,
            ),
        ],
        responses={200: BoardListSerializer(many=True)},
    )
    def get(self, request):
        """List all boards for the user's organization"""
        user_profile = request.profile

        # Get boards where user is owner or member
        queryset = (
            _readable_boards(user_profile)
            .prefetch_related("memberships")
            .order_by("-created_at")
        )

        # Filtering
        archived = request.query_params.get("archived")
        if archived is not None:
            is_archived = archived.lower() in ["true", "1", "yes"]
            queryset = queryset.filter(is_archived=is_archived)

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)

        # Pagination
        results = self.paginate_queryset(queryset, request, view=self)
        serializer = BoardListSerializer(
            results, many=True, context={"request": request}
        )

        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": serializer.data,
            }
        )

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardSerializer,
        responses={201: BoardSerializer},
    )
    def post(self, request):
        """Create a new board with default columns"""
        org = request.profile.org
        user_profile = request.profile
        data = request.data.copy()

        serializer = BoardSerializer(data=data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create board
        board = serializer.save(org=org, owner=user_profile, created_by=request.user)

        # Add owner as member with owner role
        BoardMember.objects.create(board=board, profile=user_profile, role="owner")

        # Create default columns if requested
        if request.data.get("create_default_columns", True):
            default_columns = [
                {"name": "To Do", "order": 1, "color": "#EF4444"},
                {"name": "In Progress", "order": 2, "color": "#F59E0B"},
                {"name": "Done", "order": 3, "color": "#10B981"},
            ]
            for col_data in default_columns:
                BoardColumn.objects.create(
                    board=board, org=org, created_by=request.user, **col_data
                )

        return Response(BoardSerializer(board).data, status=status.HTTP_201_CREATED)


class BoardDetailView(APIView):
    """Retrieve, update or delete a board"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk, user_profile):
        """The board, or 404 when it is missing or the caller may not open it."""
        return get_object_or_404(_readable_boards(user_profile), pk=pk)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        responses={200: BoardSerializer},
    )
    def get(self, request, pk):
        """Get board details with columns and tasks"""
        board = self.get_object(pk, request.profile)
        serializer = BoardSerializer(board)
        return Response(serializer.data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardSerializer,
        responses={200: BoardSerializer},
    )
    def put(self, request, pk):
        """Update board"""
        board = self.get_object(pk, request.profile)

        # Only owner or admin can update board
        membership = BoardMember.objects.filter(
            board=board, profile=request.profile
        ).first()
        if not membership or membership.role not in ["owner", "admin"]:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = BoardSerializer(board, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        board = serializer.save(updated_by=request.user)
        return Response(BoardSerializer(board).data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardSerializer,
        responses={200: BoardSerializer},
        description="Partial Board Update",
    )
    def patch(self, request, pk):
        """Handle partial updates to a board."""
        board = self.get_object(pk, request.profile)

        # Only owner or admin can update board
        membership = BoardMember.objects.filter(
            board=board, profile=request.profile
        ).first()
        if not membership or membership.role not in ["owner", "admin"]:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = BoardSerializer(board, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        board = serializer.save(updated_by=request.user)
        return Response(BoardSerializer(board).data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        responses={204: None},
    )
    def delete(self, request, pk):
        """Delete board (owner only)"""
        board = self.get_object(pk, request.profile)

        # Only owner can delete
        if board.owner != request.profile:
            return Response(
                {"error": "Only board owner can delete the board"},
                status=status.HTTP_403_FORBIDDEN,
            )

        board.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BoardColumnListCreateView(APIView):
    """List or create columns for a board"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        responses={200: BoardColumnSerializer(many=True)},
    )
    def get(self, request, board_pk):
        """List all columns for a board"""
        board = get_object_or_404(_readable_boards(request.profile), pk=board_pk)

        # Prefetch the nested cards with their account + assignees so rendering a
        # full board (this is the endpoint the kanban reads) stays a handful of
        # queries rather than one-per-card.
        columns = board.columns.prefetch_related(
            Prefetch(
                "tasks",
                queryset=BoardTask.objects.select_related("account").prefetch_related(
                    "assigned_to__user"
                ),
            )
        )
        serializer = BoardColumnSerializer(columns, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardColumnSerializer,
        responses={201: BoardColumnSerializer},
    )
    def post(self, request, board_pk):
        """Create a new column"""
        org = request.profile.org
        board = get_object_or_404(_readable_boards(request.profile), pk=board_pk)

        # Check permission
        membership = BoardMember.objects.filter(
            board=board, profile=request.profile
        ).first()
        if not membership or membership.role not in ["owner", "admin"]:
            return Response(
                {"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN
            )

        data = request.data.copy()
        serializer = BoardColumnSerializer(data=data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ``BoardColumn`` has ``unique_together = (board, name)``, but ``board``
        # is read-only on the serializer so DRF cannot auto-attach a uniqueness
        # validator for it. Without this, a duplicate name reaches the DB and
        # surfaces as a 500 IntegrityError; check it here for a clean 400.
        name = serializer.validated_data.get("name")
        if BoardColumn.objects.filter(board=board, name=name).exists():
            return Response(
                {
                    "error": True,
                    "errors": {
                        "name": "A column with this name already exists on this board."
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        column = serializer.save(board=board, org=org, created_by=request.user)
        return Response(
            BoardColumnSerializer(column).data, status=status.HTTP_201_CREATED
        )


class BoardTaskListCreateView(APIView):
    """List or create tasks for a column"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        responses={200: BoardTaskSerializer(many=True)},
    )
    def get(self, request, column_pk):
        """List all tasks for a column"""
        column = get_object_or_404(
            BoardColumn, pk=column_pk, board__in=_readable_boards(request.profile)
        )

        tasks = column.tasks.all()
        serializer = BoardTaskSerializer(tasks, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardTaskSerializer,
        responses={201: BoardTaskSerializer},
    )
    def post(self, request, column_pk):
        """Create a new task"""
        org = request.profile.org
        column = get_object_or_404(
            BoardColumn, pk=column_pk, board__in=_readable_boards(request.profile)
        )

        data = request.data.copy()
        try:
            assignee_ids = _pop_assignee_ids(data)
        except DRFValidationError as exc:
            return Response(
                {"error": True, "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = BoardTaskSerializer(data=data)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = serializer.save(column=column, org=org, created_by=request.user)
        if assignee_ids is not None:
            _set_card_assignees(task, org, assignee_ids)

        return Response(BoardTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class BoardTaskDetailView(APIView):
    """Retrieve, update or delete a task"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        request=BoardTaskSerializer,
        responses={200: BoardTaskSerializer},
    )
    def put(self, request, pk):
        """Update task (including moving to a different column)."""
        org = request.profile.org
        task = get_object_or_404(
            BoardTask, pk=pk, column__board__in=_readable_boards(request.profile)
        )
        board = task.column.board

        data = request.data.copy()
        try:
            assignee_ids = _pop_assignee_ids(data)
        except DRFValidationError as exc:
            return Response(
                {"error": True, "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve an optional column move. ``column`` is read-only on the
        # serializer, so the drag-and-drop target is validated and applied here
        # instead: it must be a column of *this* board (which, since the board is
        # already org-scoped, keeps the card inside its org). Sending a column
        # from another board, or a garbage id, is a 400, not a silent no-op.
        source_column = task.column
        target_column = source_column
        raw_column = data.get("column")
        if raw_column and str(raw_column) != str(source_column.id):
            try:
                target_column = BoardColumn.objects.filter(
                    pk=raw_column, board=board
                ).first()
            except (DjangoValidationError, ValueError, TypeError):
                target_column = None
            if target_column is None:
                return Response(
                    {
                        "error": True,
                        "errors": {"column": "Invalid column for this board."},
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = BoardTaskSerializer(task, data=data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        moved = target_column.id != source_column.id
        save_kwargs = {"updated_by": request.user}
        if moved:
            save_kwargs["column"] = target_column
        task = serializer.save(**save_kwargs)
        if assignee_ids is not None:
            _set_card_assignees(task, org, assignee_ids)

        # Keep each touched column densely ordered so the card lands exactly where
        # it was dropped and reloads are stable.
        if moved or "order" in serializer.validated_data:
            requested_index = serializer.validated_data.get("order", task.order)
            _resequence_column(
                target_column, moved_task=task, target_index=requested_index
            )
            if moved:
                _resequence_column(source_column)

        return Response(BoardTaskSerializer(task).data)

    @extend_schema(
        tags=["Boards"],
        parameters=[
            OpenApiParameter(
                name="org", description="Organization ID", required=True, type=str
            )
        ],
        responses={204: None},
    )
    def delete(self, request, pk):
        """Delete task"""
        task = get_object_or_404(
            BoardTask, pk=pk, column__board__in=_readable_boards(request.profile)
        )

        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
