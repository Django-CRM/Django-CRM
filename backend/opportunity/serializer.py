from decimal import Decimal

from django.db.models import Sum
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializer import AccountPickerSerializer
from common.serializer import (
    OrganizationSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from common.utils import OPPORTUNITY_TYPES
from contacts.serializer import ContactLinkSerializer, ContactPickerSerializer
from invoices.serializer import LineAmountsMixin, ProductSerializer
from opportunity.models import (
    DealPipeline,
    DealStage,
    Opportunity,
    OpportunityLineItem,
    SalesGoal,
)
from opportunity.stages import stage_index
from opportunity.workflow import CLOSED_KINDS, OPEN, STAGE_KINDS, WON

# A deal type multiplier above this is a data-entry slip, not a quota policy.
# The ceiling exists so one typo cannot make a goal unreachable or trivially
# met; it is deliberately loose enough for any real weighting.
MAX_DEAL_TYPE_WEIGHT = 100

# Note: Removed unused serializer properties that were computed but never used by frontend:
# - get_team_users, get_team_and_assigned_users, get_assigned_users_not_in_teams


class OpportunityLineItemSerializer(serializers.ModelSerializer):
    """Serializer for reading OpportunityLineItem data"""

    product = ProductSerializer(read_only=True)
    product_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    formatted_unit_price = serializers.SerializerMethodField()
    formatted_total = serializers.SerializerMethodField()

    class Meta:
        model = OpportunityLineItem
        fields = (
            "id",
            "product",
            "product_id",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "discount_amount",
            "subtotal",
            "total",
            "order",
            "formatted_unit_price",
            "formatted_total",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "discount_amount",
            "subtotal",
            "total",
            "created_at",
            "updated_at",
        )

    def get_formatted_unit_price(self, obj):
        """Format unit price with currency symbol"""
        currency = obj.opportunity.currency or "USD"
        return f"{currency} {obj.unit_price:,.2f}"

    def get_formatted_total(self, obj):
        """Format total with currency symbol"""
        currency = obj.opportunity.currency or "USD"
        return f"{currency} {obj.total:,.2f}"


class OpportunityLineItemCreateSerializer(
    LineAmountsMixin, serializers.ModelSerializer
):
    """Serializer for creating/updating OpportunityLineItem data.

    Needs ``context["opportunity"]``: the product is looked up in that deal's
    org. Quantity, price and discount follow the invoice line rules
    (`LineAmountsMixin`), since an invoice raised from the deal copies them.
    """

    product_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = OpportunityLineItem
        fields = (
            "product_id",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "order",
        )

    def validate_product_id(self, value):
        """The product, from the deal's own org, or a 400.

        The message is the same for another org's product and one that does
        not exist, so the answer does not reveal which ids exist elsewhere.
        """
        if value is None:
            return None
        from invoices.models import Product

        product = Product.objects.filter(
            id=value, org_id=self.context["opportunity"].org_id
        ).first()
        if product is None:
            raise serializers.ValidationError(
                "Product not found or does not belong to your organization"
            )
        return product

    def validate(self, attrs):
        if "product_id" in attrs:
            attrs["product"] = attrs.pop("product_id")
        # `OpportunityLineItem.save` prices a line left at zero from its
        # product; do it here first so the discount is checked against the
        # amount the line will actually have.
        stored = self.instance
        product = attrs.get("product", getattr(stored, "product", None))
        price = attrs.get("unit_price", getattr(stored, "unit_price", Decimal("0")))
        if product is not None and not price:
            attrs["unit_price"] = product.price or Decimal("0")
        return super().validate(attrs)


class DealContactSerializer(ContactPickerSerializer):
    """A person on the deal detail page: the picker's name, plus role.

    The detail GET's top-level `contacts` sent full `ContactSerializer`
    records. The web's deal page reads the name, `title` and `department`
    (`lib/server/v2/deals.js`, `getDeal`) and links each row to the contact;
    mobile reads the nested `opportunity_obj.contacts` instead.
    """

    class Meta(ContactPickerSerializer.Meta):
        fields = ContactPickerSerializer.Meta.fields + ("title", "department")


class DealStageFieldsMixin:
    """`stage_label`, `stage_kind` and `aging_status` from the deal's `DealStage`.

    The org's stages are read once per serialization and kept in the shared
    context, so a page of deals costs one stage query, not three per row. A
    caller may also pre-load them as `context["stages"]` (see
    `opportunity.stages.stage_index`).
    """

    def _stages(self, obj):
        stages = self.context.get("stages")
        if stages is None:
            stages = stage_index(obj.org_id)
            self.context["stages"] = stages
        return stages

    @extend_schema_field(str)
    def get_stage_label(self, obj):
        stage = obj.current_stage(self._stages(obj))
        return stage.label if stage else obj.stage

    @extend_schema_field(str)
    def get_stage_kind(self, obj):
        stage = obj.current_stage(self._stages(obj))
        return stage.kind if stage else None

    @extend_schema_field(int)
    def get_days_in_stage(self, obj):
        return obj.days_in_current_stage

    @extend_schema_field(str)
    def get_aging_status(self, obj):
        return obj.get_aging_status(stages=self._stages(obj))


class OpportunitySerializer(DealStageFieldsMixin, serializers.ModelSerializer):
    """Serializer for reading Opportunity data"""

    # `{id, name}` only. Seeing this record is not access to its account, and
    # both clients read only these two; the rest is on `/api/accounts/<id>/`.
    account = AccountPickerSerializer(read_only=True)
    closed_by = ProfileSerializer()
    created_by = UserSerializer()
    org = OrganizationSerializer()
    tags = TagsSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    # Name and email only; see `ContactLinkSerializer`.
    contacts = ContactLinkSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    line_items = OpportunityLineItemSerializer(read_only=True, many=True)
    created_on_arrow = serializers.SerializerMethodField()
    line_items_total = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()
    stage_kind = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    aging_status = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_created_on_arrow(self, obj):
        return obj.created_on_arrow

    @extend_schema_field(float)
    def get_line_items_total(self, obj):
        """Calculate total from line items"""
        return sum(item.total for item in obj.line_items.all())

    class Meta:
        model = Opportunity
        fields = (
            "id",
            # Core Opportunity Information
            "name",
            "account",
            "pipeline",
            "stage",
            "stage_label",
            "stage_kind",
            "opportunity_type",
            # Financial Information
            "currency",
            "amount",
            "amount_source",
            "probability",
            "closed_on",
            # Source & Context
            "lead_source",
            # Relationships
            "contacts",
            # Line Items / Products
            "line_items",
            "line_items_total",
            # Assignment
            "assigned_to",
            "teams",
            "closed_by",
            # Tags
            "tags",
            # Notes
            "description",
            # System
            "created_by",
            "created_at",
            "is_active",
            "org",
            "created_on_arrow",
            # Deal Aging
            "stage_changed_at",
            "days_in_stage",
            "aging_status",
            # Per-org custom fields (validated via common.custom_fields)
            "custom_fields",
        )


class OpportunityCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Opportunity data"""

    probability = serializers.IntegerField(
        max_value=100, required=False, allow_null=True
    )
    closed_on = serializers.DateField(required=False, allow_null=True)
    # Optional on both verbs: a deal created without them lands in the org's
    # default pipeline on its first open stage, which is what every client
    # that predates pipelines relies on. Checked against the pipeline in
    # `validate`, not against a fixed choice list.
    stage = serializers.CharField(required=False, max_length=64)
    pipeline = serializers.PrimaryKeyRelatedField(
        queryset=DealPipeline.objects.none(), required=False
    )

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = None
        # Set by `validate`: whether this save puts the deal in a won or lost
        # stage it named, which is when the view records who closed it.
        self.closing = False
        if request_obj:
            self.org = request_obj.profile.org
            # Only this org's pipelines resolve; another org's id is "does not
            # exist", the same answer as an id nobody has.
            self.fields["pipeline"].queryset = DealPipeline.objects.filter(org=self.org)

    def validate_name(self, name):
        if self.instance:
            if (
                Opportunity.objects.filter(name__iexact=name, org=self.org)
                .exclude(id=self.instance.id)
                .exists()
            ):
                raise serializers.ValidationError(
                    "Opportunity already exists with this name"
                )
        else:
            if Opportunity.objects.filter(name__iexact=name, org=self.org).exists():
                raise serializers.ValidationError(
                    "Opportunity already exists with this name"
                )
        return name

    def _resolved(self, data, field):
        """The value this save will end up with, the submitted one if the
        request carried the field, otherwise what is already on the record.

        Needed because PATCH is the verb the edit form uses: a request that
        only moves the stage still has to be judged against the amount and
        close date already stored, not against the empty half of its own body.
        """
        if field in data:
            return data[field]
        return getattr(self.instance, field, None)

    def _resolve_stage(self, data):
        """The `DealStage` this save leaves the deal in, filling `data` to match.

        The pipeline is the one named, else the deal's own, else the org's
        default. A named stage must be one of that pipeline's codes. Moving a
        deal to another pipeline must name a stage there: its current code may
        mean something else in the new pipeline, or nothing at all. A new deal
        that names no stage starts in the pipeline's first open stage.
        """
        if "pipeline" in data:
            pipeline = data["pipeline"]
        elif self.instance is not None:
            pipeline = self.instance.pipeline
        else:
            pipeline = DealPipeline.default_for(self.org)
            data["pipeline"] = pipeline
        moving = self.instance is not None and pipeline.id != self.instance.pipeline_id
        stages = list(pipeline.stages.all())

        if "stage" in data:
            stage = next((s for s in stages if s.code == data["stage"]), None)
            if stage is None:
                raise serializers.ValidationError(
                    {"stage": "That is not a stage of this deal's pipeline."}
                )
            return stage
        if moving:
            raise serializers.ValidationError(
                {"stage": "Choose a stage of the pipeline the deal is moving to."}
            )
        if self.instance is not None:
            return next((s for s in stages if s.code == self.instance.stage), None)
        stage = next((s for s in stages if s.kind == OPEN), None)
        if stage is not None:
            data["stage"] = stage.code
        return stage

    def validate(self, data):
        """Enforce the two rules `Opportunity.clean()` declares.

        `clean()` is a model method and DRF never calls it; `ModelSerializer`
        does not run `full_clean()`, so both rules existed only as unit tests
        against the model. Through the API a deal could be marked Closed Won
        with no amount and no close date, and it silently landed in
        `SalesGoal.compute_progress()` (which sums `amount` over won deals)
        as a win worth nothing.

        Kept as a serializer rule rather than wired into `save()` so the client
        gets a 400 naming the field instead of a 500 out of the database, per
        the API Validation rules in CLAUDE.md.
        """
        stage = self._resolve_stage(data)
        kind = stage.kind if stage else None
        self.closing = "stage" in data and kind in CLOSED_KINDS
        errors = {}

        if kind in CLOSED_KINDS and not self._resolved(data, "closed_on"):
            errors["closed_on"] = (
                "A deal cannot be closed without the date it closed on."
            )
        if kind == WON and not self._resolved(data, "amount"):
            errors["amount"] = "A won deal has to record what it was worth."

        # `amount` stops being the client's field once the deal has line items.
        # `OpportunityLineItem.save()` calls `recalculate_amount()`, which sets
        # `amount` from the lines and stamps `amount_source = "CALCULATED"`.
        # Nothing recomputed it on the deal's own update path, so a request
        # carrying a different figure overwrote the total while leaving the
        # source still claiming the number came from the lines. The record then
        # asserted two incompatible things about the same money.
        #
        # An unchanged value is accepted rather than refused, because clients
        # that send the whole record back on every edit (the mobile deal form
        # does) must still be able to move the stage or the close date. Only an
        # actual attempt to change the figure is an error.
        if self.instance is not None and "amount" in data:
            line_items = self.instance.line_items
            if line_items.exists():
                line_total = line_items.aggregate(total=Sum("total"))["total"] or 0
                if data["amount"] != line_total:
                    errors["amount"] = (
                        "This deal's amount is the sum of its line items. "
                        "Edit the line items to change it."
                    )

        if errors:
            raise serializers.ValidationError(errors)
        return data

    class Meta:
        model = Opportunity
        fields = (
            # Core Opportunity Information
            "name",
            "account",
            "pipeline",
            "stage",
            "opportunity_type",
            # Financial Information
            "currency",
            "amount",
            "probability",
            "closed_on",
            # Source & Context
            "lead_source",
            # Notes
            "description",
            # Status
            "is_active",
        )

    def create(self, validated_data):
        # Default currency from org if not provided
        if not validated_data.get("currency"):
            request = self.context.get("request")
            if request and hasattr(request, "profile") and request.profile.org:
                validated_data["currency"] = request.profile.org.default_currency
        return super().create(validated_data)


# ============================================================================
# Kanban Serializers
# ============================================================================


class _MinimalAccountField(serializers.RelatedField):
    """Account FK rendered as `{id, name}` so kanban cards can show the parent
    without the heavyweight AccountSerializer (which pulls in addresses, tags,
    contacts, etc., wasted bytes on a kanban card)."""

    def to_representation(self, value):
        return {"id": str(value.pk), "name": getattr(value, "name", "") or ""}


class OpportunityKanbanCardSerializer(
    DealStageFieldsMixin, serializers.ModelSerializer
):
    """Lightweight payload for kanban cards, only what the card UI renders."""

    account = _MinimalAccountField(read_only=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    stage_label = serializers.SerializerMethodField()
    stage_kind = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    aging_status = serializers.SerializerMethodField()
    # The shared KanbanBoard reads `opportunity_amount` to drive its pipeline
    # stats bar and per-column totals; aliasing `amount` lets the board light
    # those up without a frontend transform layer.
    opportunity_amount = serializers.DecimalField(
        source="amount",
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = Opportunity
        fields = (
            "id",
            "name",
            "pipeline",
            "stage",
            "stage_label",
            "stage_kind",
            "amount",
            "opportunity_amount",
            "currency",
            "probability",
            "closed_on",
            "kanban_order",
            "account",
            "assigned_to",
            "days_in_stage",
            "aging_status",
            "created_at",
        )


class OpportunityMoveSerializer(serializers.Serializer):
    """Payload for PATCH /opportunities/<pk>/move/.

    `column_id` is the id the board GET handed the client for the destination
    column, which for opportunities is a stage `code` of the deal's own
    pipeline (the view checks it against that pipeline). Boards for leads,
    cases and tasks take the same field name and put a stage UUID or a status
    value in it, so one client component drives all four.

    `above_id`/`below_id` name the cards the drop landed between; either, both,
    or neither may be sent. An explicit `kanban_order` wins over both.
    """

    column_id = serializers.CharField(max_length=64)
    kanban_order = serializers.DecimalField(
        max_digits=15, decimal_places=6, required=False
    )
    above_id = serializers.UUIDField(required=False, allow_null=True)
    below_id = serializers.UUIDField(required=False, allow_null=True)


class OpportunityCreateSwaggerSerializer(serializers.ModelSerializer):
    closed_on = serializers.DateField()

    class Meta:
        model = Opportunity
        fields = (
            "name",
            "account",
            "pipeline",
            "stage",
            "opportunity_type",
            "amount",
            "currency",
            "probability",
            "closed_on",
            "lead_source",
            "description",
            "assigned_to",
            "contacts",
            "teams",
            "tags",
        )


class OpportunityDetailEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


class OpportunityCommentEditSwaggerSerializer(serializers.Serializer):
    comment = serializers.CharField()


class DealStageSerializer(serializers.ModelSerializer):
    """One stage of a deal pipeline, read and written by the admin settings.

    `code` is derived from the label when the stage is created and never
    changes, because it is the value stored on every deal in the stage.
    `pipeline` and `org` come from the URL and the caller, never the body.
    """

    label = serializers.CharField(max_length=100)
    kind = serializers.ChoiceField(choices=[k for k, _label in STAGE_KINDS])
    expected_days = serializers.IntegerField(
        min_value=1, max_value=3650, required=False, allow_null=True
    )
    warning_days = serializers.IntegerField(
        min_value=1, max_value=3650, required=False, allow_null=True
    )

    class Meta:
        model = DealStage
        fields = (
            "id",
            "code",
            "label",
            "order",
            "kind",
            "expected_days",
            "warning_days",
        )
        read_only_fields = ("id", "code", "order")

    def validate_label(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A stage needs a name.")
        # Two columns with one name on a board are two columns nobody can
        # tell apart.
        clash = self.context["pipeline"].stages.filter(label__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "This pipeline already has a stage with this name."
            )
        return value

    def validate(self, data):
        kind = data.get("kind", getattr(self.instance, "kind", None))
        if kind != OPEN:
            # A closed deal does not age, so a threshold on a closed stage
            # would be a setting that does nothing.
            data["expected_days"] = None
            data["warning_days"] = None
        return data


class DealPipelineSerializer(serializers.ModelSerializer):
    """A pipeline with its stages in board order. Only `name` is writable."""

    name = serializers.CharField(max_length=100)
    stages = DealStageSerializer(many=True, read_only=True)

    class Meta:
        model = DealPipeline
        fields = ("id", "name", "is_default", "stages")
        read_only_fields = ("id", "is_default")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A pipeline needs a name.")
        org = self.context["org"]
        clash = DealPipeline.objects.filter(org=org, name__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A pipeline with this name exists.")
        return value


class SalesGoalSerializer(serializers.ModelSerializer):
    assigned_to_detail = ProfileSerializer(source="assigned_to", read_only=True)
    team_detail = serializers.SerializerMethodField()
    progress_value = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = SalesGoal
        fields = (
            "id",
            "name",
            "goal_type",
            "target_value",
            "currency",
            "period_type",
            "period_start",
            "period_end",
            "assigned_to",
            "assigned_to_detail",
            "team",
            "team_detail",
            "type_weights",
            "is_active",
            "milestone_50_notified",
            "milestone_90_notified",
            "milestone_100_notified",
            "org",
            "created_at",
            "updated_at",
            "progress_value",
            "progress_percent",
            "status",
        )

    def get_team_detail(self, obj):
        if obj.team:
            return {"id": str(obj.team.id), "name": obj.team.name}
        return None

    def get_progress_value(self, obj):
        return float(obj.compute_progress())

    def get_progress_percent(self, obj):
        return obj.progress_percent

    def get_status(self, obj):
        return obj.status


class SalesGoalCreateSerializer(serializers.ModelSerializer):
    """Write serializer for SalesGoal (create + partial update).

    ``org`` and ``created_by`` are set by the view from ``request.profile``.
    They are not fields here, so they can never be mass-assigned from the body.

    ``currency`` is checked against ``CURRENCY_CODES`` by the model field's
    choices. Left out (or blank), ``SalesGoal.save`` fills it from the org's
    default currency.

    ``assigned_to`` (a Profile) and ``team`` (a Teams) are the tenant-boundary
    risk on this serializer. DRF's default ``PrimaryKeyRelatedField`` resolves
    them against *every* row in the table, and ``common_profile`` is **not**
    RLS-protected (it is an auth-bootstrap table looked up before org context
    exists), so without the checks below an admin could POST/PUT a goal whose
    ``assigned_to`` is a Profile in another org, leaking that person's name and
    email into this org's goal detail and leaderboard. ``teams`` *is* RLS-scoped,
    but the contract is the explicit org check, not the safety net, so both are
    validated the same way. The view passes ``context={"request": request}`` for
    exactly this reason.
    """

    class Meta:
        model = SalesGoal
        fields = (
            "name",
            "goal_type",
            "target_value",
            "currency",
            "period_type",
            "period_start",
            "period_end",
            "assigned_to",
            "team",
            "type_weights",
            "is_active",
        )

    def _caller_org(self):
        request = self.context.get("request")
        profile = getattr(request, "profile", None) if request else None
        return getattr(profile, "org", None)

    def validate_assigned_to(self, value):
        if value is None:
            return value
        org = self._caller_org()
        if org is None or value.org_id != org.id:
            raise serializers.ValidationError(
                "Selected assignee is not a member of this organization."
            )
        return value

    def validate_team(self, value):
        if value is None:
            return value
        org = self._caller_org()
        if org is None or value.org_id != org.id:
            raise serializers.ValidationError(
                "Selected team does not belong to this organization."
            )
        return value

    def validate_type_weights(self, value):
        """Deal type multipliers: a JSON object of known type to a number >= 0.

        Left unchecked this is a free-form JSON column, so a typo like
        ``{"RENEWALS": 0.5}`` would be stored happily and then silently weigh
        nothing, and a string value would raise from ``Decimal`` deep inside
        progress computation on every later read. Both are refused here, where
        the caller still gets a 400 that names the problem.
        """
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "Deal type weights must be an object mapping a deal type to a number."
            )
        known = {choice[0] for choice in OPPORTUNITY_TYPES}
        cleaned = {}
        for deal_type, weight in value.items():
            if deal_type not in known:
                raise serializers.ValidationError(
                    f"'{deal_type}' is not a deal type. Choose from: "
                    f"{', '.join(sorted(known))}."
                )
            # bool is an int in Python, and True would quietly become a weight
            # of 1, so it is excluded rather than coerced.
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise serializers.ValidationError(
                    f"The weight for '{deal_type}' must be a number."
                )
            if weight < 0:
                raise serializers.ValidationError(
                    f"The weight for '{deal_type}' cannot be negative."
                )
            if weight > MAX_DEAL_TYPE_WEIGHT:
                raise serializers.ValidationError(
                    f"The weight for '{deal_type}' cannot be above "
                    f"{MAX_DEAL_TYPE_WEIGHT}."
                )
            cleaned[deal_type] = weight
        return cleaned

    def validate(self, data):
        period_start = data.get(
            "period_start", getattr(self.instance, "period_start", None)
        )
        period_end = data.get("period_end", getattr(self.instance, "period_end", None))
        if period_start and period_end and period_end <= period_start:
            raise serializers.ValidationError(
                {"period_end": "Period end must be after period start."}
            )
        target_value = data.get(
            "target_value", getattr(self.instance, "target_value", None)
        )
        if target_value is not None and target_value <= 0:
            raise serializers.ValidationError(
                {"target_value": "Target value must be greater than 0."}
            )

        # A goal counts one person's work or one team's, never both. The model
        # let both columns be set at once and `compute_progress` silently
        # preferred `assigned_to`, so a goal could name a team on screen while
        # scoring a single rep.
        assigned_to = data.get(
            "assigned_to", getattr(self.instance, "assigned_to", None)
        )
        team = data.get("team", getattr(self.instance, "team", None))
        if assigned_to and team:
            raise serializers.ValidationError(
                {"team": "A goal belongs to a person or to a team, not to both."}
            )

        goal_type = data.get("goal_type", getattr(self.instance, "goal_type", None))
        type_weights = data.get(
            "type_weights", getattr(self.instance, "type_weights", None)
        )
        if goal_type == "ACTIVITIES" and type_weights:
            raise serializers.ValidationError(
                {
                    "type_weights": (
                        "An activities goal counts logged activity, which has no "
                        "deal type to weigh."
                    )
                }
            )
        return data

    def update(self, instance, validated_data):
        """Save, clearing milestone flags when the bar itself moved.

        The three `milestone_*_notified` flags are one-shot per goal. That is
        right while the goal is fixed and wrong the moment an admin raises the
        target or shifts the period: the goal has already "notified" at 100%
        against a bar that no longer exists, so it could never announce the new
        one. Editing a name, an assignee or the paused flag is not a new bar and
        leaves the history alone. A new currency is a new bar: the deals that
        count toward it are a different set.
        """
        moved = [
            field
            for field in ("target_value", "currency", "period_start", "period_end")
            if field in validated_data
            and validated_data[field] != getattr(instance, field)
        ]
        if moved:
            validated_data["milestone_50_notified"] = False
            validated_data["milestone_90_notified"] = False
            validated_data["milestone_100_notified"] = False
        return super().update(instance, validated_data)
