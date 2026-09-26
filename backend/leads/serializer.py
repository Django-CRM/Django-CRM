from django.db.models import Count, Q
from rest_framework import serializers

from common.serializer import (
    AttachmentsSerializer,
    LeadCommentSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from common.utils import LEAD_STATUS
from contacts.serializer import ContactPickerSerializer
from leads.access import visible_leads_qs
from leads.models import Lead, LeadPipeline, LeadStage
from leads.workflow import IRREVERSIBLE_STATUSES


class LeadSerializer(serializers.ModelSerializer):
    # Neither client reads these; the name is enough to say who they are.
    contacts = ContactPickerSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    created_by = UserSerializer()
    tags = TagsSerializer(read_only=True, many=True)
    lead_attachment = AttachmentsSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    lead_comments = LeadCommentSerializer(read_only=True, many=True)

    class Meta:
        model = Lead
        fields = (
            "id",
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "currency",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Assignment
            "assigned_to",
            "teams",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # Related
            "contacts",
            "lead_attachment",
            "lead_comments",
            "tags",
            # System
            "created_by",
            "created_at",
            "updated_at",
            "is_active",
            "is_sample",
            "company_name",
            # Kanban
            "stage",
            "kanban_order",
            # Per-org custom fields (validated via common.custom_fields)
            "custom_fields",
        )
        # is_sample is server-set only (see leads/models.py). Read-only here
        # even though this serializer is a read/list path today (writes go
        # through LeadCreateSerializer, which never lists this field at all).
        # Defense in depth: a future write path added to this serializer
        # without checking Meta.fields first still can't make it writable.
        read_only_fields = ("is_sample",)


class LeadPickerSerializer(serializers.ModelSerializer):
    """A lead as an option in a select: enough to label it, nothing more.

    The `leads` catalogue on `/api/accounts/` sent full `LeadSerializer` rows,
    each with the lead's email, phone, address, comments and attachments. A
    picker needs a label; the record is on `/api/leads/<id>/`.
    """

    class Meta:
        model = Lead
        fields = ("id", "title", "first_name", "last_name")


class LeadCreateSerializer(serializers.ModelSerializer):
    # The floors mirror `lead_probability_range` and `lead_amount_non_negative`.
    # Without them a negative value passed validation and the check constraint
    # turned it into an IntegrityError, a 500 on create and update.
    probability = serializers.IntegerField(
        min_value=0, max_value=100, required=False, allow_null=True
    )
    opportunity_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
    )
    close_date = serializers.DateField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        if self.initial_data and self.initial_data.get("status") == "converted":
            self.fields["email"].required = True
        self.fields["first_name"].required = False
        self.fields["last_name"].required = False
        self.fields["salutation"].required = False
        self.org = request_obj.profile.org

    def validate_email(self, value):
        """One lead per address per org, matching the DB constraint exactly.

        `unique_lead_email_per_org` is a `UniqueConstraint(Lower("email"),
        "org")` conditional on a non-empty email, and nothing checked it before
        the insert, so a duplicate reached the database and came back as an
        IntegrityError, which the view does not catch. The caller got a 500 and
        no indication of which field was wrong.

        The comparison here has to be case-insensitive and has to skip empty
        values for the same reasons the constraint does, or the two disagree
        and the 500 comes back for the cases this misses.

        This is a check, not the enforcement. The constraint is still what
        guarantees it under a race. This exists so the ordinary case is a 400
        that names the field.
        """
        if not value:
            return value

        duplicates = Lead.objects.filter(org=self.org, email__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                "Another lead in this organisation already uses that email address."
            )
        return value

    def validate_status(self, value):
        """Refuse transitions into and out of an irreversible status.

        Validate the *source* state, not just the target. On an update,
        `self.instance` is the lead as it stands in the database, which is the
        only trustworthy version. The client's idea of the current status is
        not evidence of anything.

        Two distinct failures are prevented here:

        1. Re-converting. `LeadDetailView.put` and `patch` call
           `convert_lead_to_account()` whenever the incoming status is
           "converted", so a repeat write would create a second Opportunity
           against the same Account.
        2. Un-converting. The Account, Contact and Opportunity that conversion
           created are not removed when the status changes back, so the lead
           returns to the working list with duplicates already downstream of
           it.

        Creating a lead directly as "converted" is left alone: there is no
        prior state to contradict. `LeadListView.post` saves the new lead and
        then converts it, once, the same way an update does.
        """
        if self.instance is None:
            return value

        current = self.instance.status
        if current not in IRREVERSIBLE_STATUSES:
            return value

        if value == current:
            raise serializers.ValidationError(
                f"This lead is already {current}. Converting it again would "
                "create a second opportunity against the same account."
            )
        raise serializers.ValidationError(
            f"A {current} lead cannot be changed back to '{value}'. The "
            "account, contact and opportunity created by the conversion are "
            "not removed, so the lead would reopen with duplicates already "
            "downstream of it."
        )

    class Meta:
        model = Lead
        fields = (
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "currency",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # System
            "company_name",
            "is_active",
        )

    def create(self, validated_data):
        # Default currency from org if not provided and has opportunity_amount
        if not validated_data.get("currency") and validated_data.get(
            "opportunity_amount"
        ):
            request = self.context.get("request")
            if request and hasattr(request, "profile") and request.profile.org:
                validated_data["currency"] = request.profile.org.default_currency
        return super().create(validated_data)


class LeadCreateSwaggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = [
            # Core Lead Information
            "title",
            "salutation",
            "first_name",
            "last_name",
            "email",
            "phone",
            "job_title",
            "website",
            "linkedin_url",
            # Sales Pipeline
            "status",
            "source",
            "industry",
            "rating",
            "opportunity_amount",
            "probability",
            "close_date",
            # Address
            "address_line",
            "city",
            "state",
            "postcode",
            "country",
            # Assignment & Related
            "assigned_to",
            "teams",
            "contacts",
            "tags",
            # Activity
            "last_contacted",
            "next_follow_up",
            "description",
            # System
            "company_name",
        ]


class CreateLeadFromSiteSwaggerSerializer(serializers.Serializer):
    apikey = serializers.CharField()
    title = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.CharField()
    source = serializers.CharField()
    description = serializers.CharField()


class LeadDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()
    lead_attachment = serializers.FileField()


class LeadCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


# ============================================
# Kanban Serializers
# ============================================


def _visible_lead_count(serializer, **lookup):
    """How many leads matching ``lookup`` the requester may see.

    ``lead_count`` once counted every lead in the org, so a member could learn
    how many leads were withheld from them. It follows ``visible_leads_qs`` now,
    the rule the lead list and the board use. The request has to be in the
    serializer context: a missing one is a KeyError, never an unscoped count.
    """
    request = serializer.context["request"]
    return visible_leads_qs(request.profile, request.user).filter(**lookup).count()


class LeadStageSerializer(serializers.ModelSerializer):
    """Serializer for lead stages. Needs ``request`` in its context."""

    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadStage
        fields = [
            "id",
            "name",
            "order",
            "color",
            "stage_type",
            "maps_to_status",
            "win_probability",
            "wip_limit",
            "lead_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "org")
        # A lead moved into the stage takes this value as its probability, so
        # it has to fit `lead_probability_range` or the move is a 500.
        extra_kwargs = {"win_probability": {"min_value": 0, "max_value": 100}}

    def get_lead_count(self, obj):
        return _visible_lead_count(self, stage=obj)

    def validate_name(self, value):
        """One name per pipeline, the ``unique_together`` the model declares.

        DRF only generates that check when every field in it is on the
        serializer, and ``pipeline`` is set by the view, so a duplicate reached
        the database and answered 500. On create the view passes the pipeline
        in the context; on update it is the instance's.
        """
        pipeline = self.instance.pipeline if self.instance else self.context["pipeline"]
        clash = LeadStage.objects.filter(pipeline=pipeline, name__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "This pipeline already has a stage with that name."
            )
        return value

    def validate_maps_to_status(self, value):
        """Refuse a new mapping to "converted".

        The board refuses every move into such a stage, because converting has
        to run the conversion service, so mapping to it builds a column nobody
        can enter. A stage that already carries it may keep it, so a form that
        sends the stage back unchanged is not refused for a value it did not
        pick.
        """
        unchanged = self.instance is not None and self.instance.maps_to_status == value
        if value in IRREVERSIBLE_STATUSES and not unchanged:
            raise serializers.ValidationError(
                "A stage cannot convert a lead. Convert it from the lead's own page."
            )
        return value


class LeadPipelineSerializer(serializers.ModelSerializer):
    """Serializer for lead pipelines with nested stages. Needs ``request`` in
    its context."""

    stages = LeadStageSerializer(many=True, read_only=True)
    stage_count = serializers.SerializerMethodField()
    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stages",
            "stage_count",
            "lead_count",
            "created_at",
            "updated_at",
        ]
        # `is_active` only goes False through DELETE, which refuses a pipeline
        # that still has leads. Writable here, a PUT skipped that refusal.
        read_only_fields = ("id", "is_active", "created_at", "updated_at", "org")

    def get_stage_count(self, obj):
        return obj.stages.count()

    def get_lead_count(self, obj):
        return _visible_lead_count(self, stage__pipeline=obj)


class LeadPipelineListSerializer(serializers.ModelSerializer):
    """Simplified pipeline serializer for lists.

    Both counts are read from annotations, so every queryset serialized here
    must come through ``with_counts``: one query for the whole list, however
    many pipelines. A pipeline without them raises AttributeError, which is
    louder than a count that silently ignores who is asking.
    """

    stage_count = serializers.SerializerMethodField()
    lead_count = serializers.SerializerMethodField()

    class Meta:
        model = LeadPipeline
        fields = [
            "id",
            "name",
            "description",
            "is_default",
            "is_active",
            "stage_count",
            "lead_count",
            "created_at",
        ]

    @staticmethod
    def with_counts(pipelines, profile, user):
        # The lead join repeats a stage once per lead in it, hence distinct on
        # the stage count. A lead sits in one stage, so it is counted once.
        return pipelines.annotate(
            stage_count=Count("stages", distinct=True),
            lead_count=Count(
                "stages__leads",
                filter=Q(stages__leads__in=visible_leads_qs(profile, user)),
            ),
        )

    def get_stage_count(self, obj):
        return obj.stage_count

    def get_lead_count(self, obj):
        return obj.lead_count


class LeadKanbanCardSerializer(serializers.ModelSerializer):
    """Lightweight serializer for kanban cards (minimal fields for performance)."""

    assigned_to = ProfileSerializer(read_only=True, many=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = [
            "id",
            "title",
            "full_name",
            "company_name",
            "email",
            "rating",
            "opportunity_amount",
            "currency",
            "status",
            "stage",
            "kanban_order",
            "next_follow_up",
            "is_follow_up_overdue",
            "assigned_to",
            "created_at",
        ]

    def get_full_name(self, obj):
        return str(obj)


class LeadMoveSerializer(serializers.Serializer):
    """Serializer for moving leads in kanban."""

    stage_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=LEAD_STATUS, required=False)
    kanban_order = serializers.DecimalField(
        max_digits=15, decimal_places=6, required=False
    )
    above_lead_id = serializers.UUIDField(required=False, allow_null=True)
    below_lead_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        # A stage, a status, or an explicit `"stage_id": null`, which moves the
        # lead back out of its pipeline to the board's "No stage" group.
        if "stage_id" not in attrs and not attrs.get("status"):
            raise serializers.ValidationError(
                "Either stage_id or status must be provided"
            )
        return attrs
