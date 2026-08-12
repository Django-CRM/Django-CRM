from django.db.models import Sum
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializer import AccountSerializer
from common.serializer import (
    OrganizationSerializer,
    ProfileSerializer,
    TagsSerializer,
    TeamsSerializer,
    UserSerializer,
)
from contacts.serializer import ContactSerializer
from invoices.serializer import ProductSerializer
from opportunity.models import (
    Opportunity,
    OpportunityLineItem,
    SalesGoal,
    StageAgingConfig,
)
from opportunity.workflow import AMOUNT_REQUIRED_STAGES, CLOSED_STAGES

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


class OpportunityLineItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating OpportunityLineItem data"""

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

    def create(self, validated_data):
        # Handle product_id
        product_id = validated_data.pop("product_id", None)
        if product_id:
            from invoices.models import Product

            try:
                validated_data["product"] = Product.objects.get(id=product_id)
            except Product.DoesNotExist:
                pass

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Handle product_id
        product_id = validated_data.pop("product_id", None)
        if product_id:
            from invoices.models import Product

            try:
                validated_data["product"] = Product.objects.get(id=product_id)
            except Product.DoesNotExist:
                pass
        elif product_id is None and "product_id" in self.initial_data:
            # Explicitly set to null
            validated_data["product"] = None

        return super().update(instance, validated_data)


class OpportunitySerializer(serializers.ModelSerializer):
    """Serializer for reading Opportunity data"""

    account = AccountSerializer()
    closed_by = ProfileSerializer()
    created_by = UserSerializer()
    org = OrganizationSerializer()
    tags = TagsSerializer(read_only=True, many=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    contacts = ContactSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    line_items = OpportunityLineItemSerializer(read_only=True, many=True)
    created_on_arrow = serializers.SerializerMethodField()
    line_items_total = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    aging_status = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_created_on_arrow(self, obj):
        return obj.created_on_arrow

    @extend_schema_field(float)
    def get_line_items_total(self, obj):
        """Calculate total from line items"""
        return sum(item.total for item in obj.line_items.all())

    @extend_schema_field(int)
    def get_days_in_stage(self, obj):
        return obj.days_in_current_stage

    @extend_schema_field(str)
    def get_aging_status(self, obj):
        aging_configs = self.context.get("aging_configs")
        return obj.get_aging_status(aging_configs=aging_configs)

    class Meta:
        model = Opportunity
        fields = (
            "id",
            # Core Opportunity Information
            "name",
            "account",
            "stage",
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

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        if request_obj:
            self.org = request_obj.profile.org

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
        stage = self._resolved(data, "stage")
        errors = {}

        if stage in CLOSED_STAGES and not self._resolved(data, "closed_on"):
            errors["closed_on"] = (
                "A deal cannot be closed without the date it closed on."
            )
        if stage in AMOUNT_REQUIRED_STAGES and not self._resolved(data, "amount"):
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


class OpportunityKanbanCardSerializer(serializers.ModelSerializer):
    """Lightweight payload for kanban cards, only what the card UI renders."""

    account = _MinimalAccountField(read_only=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
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
            "stage",
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

    @extend_schema_field(int)
    def get_days_in_stage(self, obj):
        return obj.days_in_current_stage

    @extend_schema_field(str)
    def get_aging_status(self, obj):
        # Aging configs are passed via context to avoid N+1 lookups; falls back
        # to per-row DB query if the caller didn't prefetch (single-row case).
        aging_configs = self.context.get("aging_configs")
        return obj.get_aging_status(aging_configs=aging_configs)


class OpportunityMoveSerializer(serializers.Serializer):
    """Payload for PATCH /opportunities/<pk>/move/.

    `column_id` is the id the board GET handed the client for the destination
    column, which for opportunities is the `stage` value (there is no
    Pipeline/Stage model here, so columns are the flat stage choices). Boards
    for leads, cases and tasks take the same field name and put a stage UUID or
    a status value in it, so one client component drives all four.

    `above_id`/`below_id` name the cards the drop landed between; either, both,
    or neither may be sent. An explicit `kanban_order` wins over both.
    """

    column_id = serializers.ChoiceField(
        choices=Opportunity._meta.get_field("stage").choices
    )
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


class StageAgingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = StageAgingConfig
        fields = ("id", "stage", "expected_days", "warning_days")
        read_only_fields = ("id",)


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
            "period_type",
            "period_start",
            "period_end",
            "assigned_to",
            "assigned_to_detail",
            "team",
            "team_detail",
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
            "period_type",
            "period_start",
            "period_end",
            "assigned_to",
            "team",
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
        return data
