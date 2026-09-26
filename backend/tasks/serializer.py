from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.serializer import (
    AttachmentsSerializer,
    CommentSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from contacts.serializer import ContactLinkSerializer
from tasks.access import visible_tasks_qs
from tasks.models import (
    Board,
    BoardColumn,
    BoardMember,
    BoardTask,
    Task,
    TaskPipeline,
    TaskStage,
)


class BoardMemberSerializer(serializers.ModelSerializer):
    """Serializer for board members"""

    profile = ProfileSerializer(read_only=True)
    profile_id = serializers.UUIDField(write_only=True, required=False)

    class Meta:
        model = BoardMember
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at", "board")


class BoardTaskSerializer(serializers.ModelSerializer):
    """Serializer for board tasks.

    The write surface is deliberately narrow. A card write may set only its own
    content (title, description, order, priority, due_date), and its assignees
    (through the write-only ``assigned_to_ids``). The board views take that
    key out of the body and parse it with ``payload_id_list`` before this
    serializer sees it, then apply it after save, org-filtered and active
    only; the field stays declared so the request schema documents it.
    Everything else is locked down:

    * ``column`` is applied server-side by the move endpoint (see
      ``BoardTaskDetailView.put``), so a card can't be mass-assigned into another
      board's, or another org's, column.
    * ``org``/``created_by``/``updated_by`` are server-derived audit facts.
      (``created_by`` FKs ``common.User``, which is *not* org-scoped by RLS, so
      leaving it writable let any user id be stamped onto a card.)
    * the CRM-link FKs (``account``/``contact``/``opportunity``) are render-only
      context, no UI sets them, and while writable they exposed the same
      unfiltered, cross-org ``PrimaryKeyRelatedField`` queryset that
      ``TaskCreateSerializer`` explicitly hardens against.
    """

    assigned_to = ProfileSerializer(many=True, read_only=True)
    assigned_to_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )
    account = serializers.SerializerMethodField()
    is_completed = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = BoardTask
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "completed_at",
            "org",
            "column",
            "created_by",
            "updated_by",
            "contact",
            "opportunity",
        )

    @extend_schema_field(dict)
    def get_account(self, obj):
        """Render the parent account as ``{id, name}`` for the card chip, or
        ``None``. Read-only: overriding the model FK with a method field also
        removes it from the write surface."""
        if not obj.account_id:
            return None
        return {
            "id": str(obj.account_id),
            "name": getattr(obj.account, "name", "") or "",
        }


class BoardColumnSerializer(serializers.ModelSerializer):
    """Serializer for board columns"""

    tasks = BoardTaskSerializer(many=True, read_only=True)
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = BoardColumn
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at", "board", "org")

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return obj.tasks.count()


class BoardColumnListSerializer(serializers.ModelSerializer):
    """Simplified column serializer for lists"""

    task_count = serializers.SerializerMethodField()

    class Meta:
        model = BoardColumn
        fields = ["id", "name", "order", "color", "limit", "task_count"]

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return obj.tasks.count()


class BoardSerializer(serializers.ModelSerializer):
    """Serializer for boards"""

    owner = ProfileSerializer(read_only=True)
    columns = BoardColumnListSerializer(many=True, read_only=True)
    members = BoardMemberSerializer(source="memberships", many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "owner",
            "org",
        )

    @extend_schema_field(int)
    def get_member_count(self, obj):
        return obj.members.count()

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return BoardTask.objects.filter(column__board=obj).count()


class BoardListSerializer(serializers.ModelSerializer):
    """Simplified board serializer for lists"""

    owner = ProfileSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    column_count = serializers.SerializerMethodField()
    task_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = [
            "id",
            "name",
            "description",
            "owner",
            "is_archived",
            "member_count",
            "column_count",
            "task_count",
            "my_role",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(str)
    def get_my_role(self, obj):
        """The requesting profile's role on this board (``owner``/``admin``/
        ``member``), or ``None`` when they have none.

        Read-only and derived from the request, never the client. A client
        cannot claim a role it does not hold. The board's own owner always
        resolves to ``owner`` even if no explicit ``BoardMember`` row was created
        for them, so a board created outside the API still gates correctly. The
        frontend uses this to decide whether to offer column management, but the
        backend (``BoardColumnListCreateView``) remains the enforcing boundary.
        """
        request = self.context.get("request")
        profile = getattr(request, "profile", None)
        if profile is None:
            return None
        membership = next(
            (m for m in obj.memberships.all() if m.profile_id == profile.id), None
        )
        if membership is not None:
            return membership.role
        if obj.owner_id == profile.id:
            return "owner"
        return None

    @extend_schema_field(int)
    def get_member_count(self, obj):
        return obj.members.count()

    @extend_schema_field(int)
    def get_column_count(self, obj):
        return obj.columns.count()

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return BoardTask.objects.filter(column__board=obj).count()


class _MinimalAccountField(serializers.RelatedField):
    """Account FK rendered as `{id, name}` so list rows can show the parent
    entity without an extra round-trip. Falls back to id-only when the related
    object is missing a name."""

    def to_representation(self, value):
        return {"id": str(value.pk), "name": getattr(value, "name", "") or ""}


class _MinimalOpportunityField(serializers.RelatedField):
    def to_representation(self, value):
        return {"id": str(value.pk), "name": getattr(value, "name", "") or ""}


class _MinimalCaseField(serializers.RelatedField):
    def to_representation(self, value):
        return {"id": str(value.pk), "name": getattr(value, "name", "") or ""}


class _MinimalLeadField(serializers.RelatedField):
    def to_representation(self, value):
        first = getattr(value, "first_name", "") or ""
        last = getattr(value, "last_name", "") or ""
        name = f"{first} {last}".strip() or (getattr(value, "email", "") or "")
        return {"id": str(value.pk), "name": name}


class TaskListSerializer(serializers.ModelSerializer):
    """Slim payload for /api/tasks/ list pages. Drops the comment/attachment
    bodies (sometimes hundreds of rows per task) and the contacts/teams M2Ms
    that the list UI doesn't render. Use TaskSerializer for the detail view."""

    assigned_to = ProfileSerializer(read_only=True, many=True)
    tags = TagsSerializer(read_only=True, many=True)
    account = _MinimalAccountField(read_only=True)
    opportunity = _MinimalOpportunityField(read_only=True)
    case = _MinimalCaseField(read_only=True)
    lead = _MinimalLeadField(read_only=True)

    class Meta:
        model = Task
        fields = (
            "id",
            "title",
            "status",
            "priority",
            "due_date",
            "account",
            "opportunity",
            "case",
            "lead",
            "assigned_to",
            "tags",
            "created_at",
        )


class TaskSerializer(serializers.ModelSerializer):
    created_by = UserSerializer()
    assigned_to = ProfileSerializer(read_only=True, many=True)
    # Name and email only; see `ContactLinkSerializer`.
    contacts = ContactLinkSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    tags = TagsSerializer(read_only=True, many=True)
    task_attachment = AttachmentsSerializer(read_only=True, many=True)
    task_comments = CommentSerializer(read_only=True, many=True)
    account = _MinimalAccountField(read_only=True)
    opportunity = _MinimalOpportunityField(read_only=True)
    case = _MinimalCaseField(read_only=True)
    lead = _MinimalLeadField(read_only=True)

    class Meta:
        model = Task
        fields = (
            "id",
            "title",
            "status",
            "priority",
            "due_date",
            "description",
            "account",
            "opportunity",
            "case",
            "lead",
            "created_by",
            "created_at",
            "contacts",
            "teams",
            "assigned_to",
            "tags",
            "task_attachment",
            "task_comments",
            "custom_fields",
        )


PARENT_FIELDS = ("account", "opportunity", "case", "lead")


class TaskCreateSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = request_obj.profile.org

        self.fields["title"].required = True
        # A `ModelSerializer` builds each FK as `PrimaryKeyRelatedField(
        # queryset=Model.objects.all())`: every account, lead, case and
        # opportunity in the database, not in the org. Proven live: a task in
        # one org was created with another org's account, and the list endpoint
        # then rendered that org's account *name* back to the requester. Org is
        # a server-derived fact; narrowing the queryset is what makes it one.
        for name in PARENT_FIELDS:
            self.fields[name].queryset = self.fields[name].queryset.filter(org=self.org)

    def validate(self, attrs):
        """A task links to at most one parent entity.

        The model says so too, in `Task.clean()`, and `Task.save()` calls
        `full_clean()`, but Django's `ValidationError` is not DRF's, so the
        model's refusal arrived as a 500. Checking here gets the client a 400
        with the field named.

        The check has to consider the parents already on the task, not only the
        ones in this payload. Reading `attrs` alone was right for a create and
        wrong for a `PATCH`: sending one parent to a task that already had a
        different one looked like a single-parent request and walked straight
        past the rule.
        """
        attrs = super().validate(attrs)
        resolved = {}
        for name in PARENT_FIELDS:
            if name in attrs:
                resolved[name] = attrs[name]
            elif self.instance is not None:
                resolved[name] = getattr(self.instance, f"{name}_id", None)
            else:
                resolved[name] = None
        set_parents = [name for name in PARENT_FIELDS if resolved[name]]
        if len(set_parents) > 1:
            raise serializers.ValidationError(
                {
                    "account": (
                        "A task can only be linked to one parent entity "
                        f"(Account, Opportunity, Case, or Lead). "
                        f"Currently set: {', '.join(set_parents)}"
                    )
                }
            )
        return attrs

    class Meta:
        model = Task
        fields = (
            "id",
            "title",
            "status",
            "priority",
            "due_date",
            "description",
            "account",
            "opportunity",
            "case",
            "lead",
            "created_by",
            "created_at",
        )
        # `created_by` was writable, so a client could name anyone in any org
        # the creator of a task. Proven live, a MicroPyramid task ended up
        # created by a user of another org. That was a data-integrity bug while
        # nothing read the field; the moment the creator clause in
        # `tasks.access` starts returning True it is privilege escalation, so
        # the two changes belong together.
        read_only_fields = ("id", "created_by", "created_at")


class TaskDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
    task_attachment = serializers.FileField()


class TaskCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


class TaskCreateSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = (
            "title",
            "status",
            "priority",
            "due_date",
            "description",
            "account",
            "opportunity",
            "case",
            "lead",
            "contacts",
            "teams",
            "assigned_to",
            "tags",
        )


# ============================================================================
# Kanban Serializers
# ============================================================================


def _visible_task_count(serializer, **lookup):
    """How many tasks matching ``lookup`` the requester may open.

    ``task_count`` once counted every task in the org, so a member read a
    count that included tasks they cannot open. It follows ``visible_tasks_qs``
    now, the rule the board and the list use. The request has to be in the
    serializer context: a missing one is a KeyError, never an unscoped count.
    """
    profile = serializer.context["request"].profile
    return visible_tasks_qs(profile).filter(**lookup).count()


class TaskStageSerializer(serializers.ModelSerializer):
    """Serializer for task stages. Needs ``request`` in its context."""

    task_count = serializers.SerializerMethodField()

    class Meta:
        model = TaskStage
        fields = [
            "id",
            "name",
            "order",
            "color",
            "stage_type",
            "maps_to_status",
            "wip_limit",
            "task_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "org")

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return _visible_task_count(self, stage=obj)

    def validate_maps_to_status(self, value):
        """Only a real task status, or nothing.

        The column is free text, and a stage mapped to anything else is one no
        task can move into: `Task.save()` runs `full_clean()` and refuses the
        status the move would set.
        """
        if value in (None, ""):
            return value
        allowed = [choice for choice, _ in Task.STATUS_CHOICES]
        if value not in allowed:
            raise serializers.ValidationError(
                f"maps_to_status must be one of {', '.join(allowed)}, or empty."
            )
        return value


class TaskPipelineSerializer(serializers.ModelSerializer):
    """Serializer for task pipelines with nested stages. Needs ``request`` in
    its context."""

    stages = TaskStageSerializer(many=True, read_only=True)
    stage_count = serializers.SerializerMethodField()
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = TaskPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stages",
            "stage_count",
            "task_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "org")

    @extend_schema_field(int)
    def get_stage_count(self, obj):
        return obj.stages.count()

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return _visible_task_count(self, stage__pipeline=obj)


class TaskPipelineListSerializer(serializers.ModelSerializer):
    """Simplified pipeline serializer for lists.

    Both counts are read from annotations, so every queryset serialized here
    must come through ``with_counts``: one query for the whole list, however
    many pipelines. A pipeline without them raises AttributeError, which is
    louder than a count that silently ignores who is asking.
    """

    stage_count = serializers.SerializerMethodField()
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = TaskPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stage_count",
            "task_count",
            "created_at",
        ]

    @staticmethod
    def with_counts(pipelines, profile):
        # The task join repeats a stage once per task in it, hence distinct on
        # the stage count. A task sits in one stage, so it is counted once.
        return pipelines.annotate(
            stage_count=Count("stages", distinct=True),
            task_count=Count(
                "stages__tasks",
                filter=Q(stages__tasks__in=visible_tasks_qs(profile).values("pk")),
            ),
        )

    @extend_schema_field(int)
    def get_stage_count(self, obj):
        return obj.stage_count

    @extend_schema_field(int)
    def get_task_count(self, obj):
        return obj.task_count


class RelatedEntitySerializer(serializers.Serializer):
    """Minimal serializer for related entities on kanban cards."""

    id = serializers.UUIDField()
    name = serializers.CharField()


class TaskKanbanCardSerializer(serializers.ModelSerializer):
    """Lightweight serializer for kanban cards (minimal fields for performance)."""

    assigned_to = ProfileSerializer(read_only=True, many=True)
    is_overdue = serializers.BooleanField(read_only=True)
    related_entity = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "status",
            "priority",
            "due_date",
            "is_overdue",
            "stage",
            "kanban_order",
            "assigned_to",
            "related_entity",
            "created_at",
        ]

    @extend_schema_field(RelatedEntitySerializer(allow_null=True))
    def get_related_entity(self, obj):
        """Return the related entity (account, lead, opportunity, or case) if any."""
        if obj.account_id:
            return {"id": obj.account_id, "name": obj.account.name, "type": "account"}
        if obj.lead_id:
            return {"id": obj.lead_id, "name": str(obj.lead), "type": "lead"}
        if obj.opportunity_id:
            return {
                "id": obj.opportunity_id,
                "name": obj.opportunity.name,
                "type": "opportunity",
            }
        if obj.case_id:
            return {"id": obj.case_id, "name": obj.case.name, "type": "case"}
        return None


class TaskMoveSerializer(serializers.Serializer):
    """Serializer for moving tasks in kanban."""

    stage_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES, required=False)
    kanban_order = serializers.DecimalField(
        max_digits=15, decimal_places=6, required=False
    )
    above_task_id = serializers.UUIDField(required=False, allow_null=True)
    below_task_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        # Must provide either stage_id or status
        if not attrs.get("stage_id") and not attrs.get("status"):
            raise serializers.ValidationError(
                "Either stage_id or status must be provided"
            )
        return attrs
