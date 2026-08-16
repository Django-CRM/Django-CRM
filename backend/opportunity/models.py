from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from accounts.models import Account
from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel
from common.models import Org, Profile, Tags, Teams
from common.utils import (
    CURRENCY_CODES,
    GOAL_TYPES,
    OPPORTUNITY_TYPES,
    PERIOD_TYPES,
    SOURCES,
    STAGES,
)
from contacts.models import Contact

# Amount source choices for Opportunity
AMOUNT_SOURCE_CHOICES = (
    ("MANUAL", "Manual"),
    ("CALCULATED", "Calculated from Products"),
)

# Discount type choices
DISCOUNT_TYPES = (
    ("PERCENTAGE", "Percentage (%)"),
    ("FIXED", "Fixed Amount"),
)


class Opportunity(AssignableMixin, BaseModel):
    """
    Opportunity model for CRM - Sales pipeline management
    Based on Twenty CRM and Salesforce patterns
    """

    # Core Opportunity Information
    name = models.CharField(_("Opportunity Name"), max_length=255)
    account = models.ForeignKey(
        Account,
        related_name="opportunities",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    stage = models.CharField(
        _("Stage"), max_length=64, choices=STAGES, default="PROSPECTING"
    )
    opportunity_type = models.CharField(
        _("Type"), max_length=64, choices=OPPORTUNITY_TYPES, blank=True, null=True
    )

    # Financial Information
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, blank=True, null=True
    )
    amount = models.DecimalField(
        _("Amount"), decimal_places=2, max_digits=12, blank=True, null=True
    )
    amount_source = models.CharField(
        _("Amount Source"),
        max_length=20,
        choices=AMOUNT_SOURCE_CHOICES,
        default="MANUAL",
    )
    probability = models.IntegerField(
        _("Probability (%)"), default=0, blank=True, null=True
    )
    closed_on = models.DateField(_("Expected Close Date"), blank=True, null=True)

    # Source & Context
    lead_source = models.CharField(
        _("Lead Source"), max_length=255, choices=SOURCES, blank=True, null=True
    )

    # Relationships
    contacts = models.ManyToManyField(Contact, related_name="opportunity_contacts")

    # Assignment
    assigned_to = models.ManyToManyField(
        Profile, related_name="opportunity_assigned_users"
    )
    teams = models.ManyToManyField(Teams, related_name="opportunity_teams")
    closed_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunity_closed_by",
    )

    # Tags
    tags = models.ManyToManyField(Tags, related_name="opportunity_tags", blank=True)

    # Notes
    description = models.TextField(_("Notes"), blank=True, null=True)

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # Deal Aging
    stage_changed_at = models.DateTimeField(
        _("Stage Changed At"), null=True, blank=True
    )

    # Kanban positioning within a stage column (fractional indexing, same
    # convention as Task.kanban_order: large stride between rows lets drag-drop
    # insert by averaging neighbors without needing to renumber the column).
    kanban_order = models.DecimalField(
        _("Kanban Order"),
        max_digits=15,
        decimal_places=6,
        default=0,
        help_text="Order within the kanban column for drag-drop positioning",
    )

    # System Fields
    is_active = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="opportunities",
    )

    class Meta:
        verbose_name = "Opportunity"
        verbose_name_plural = "Opportunities"
        db_table = "opportunity"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["stage"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["stage", "kanban_order"], name="opp_stage_kanban_idx"),
        ]
        constraints = [
            # Probability must be 0-100
            models.CheckConstraint(
                condition=Q(probability__gte=0) & Q(probability__lte=100),
                name="opportunity_probability_range",
            ),
            # Amount must be non-negative
            models.CheckConstraint(
                condition=Q(amount__gte=0) | Q(amount__isnull=True),
                name="opportunity_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.name}"

    @property
    def created_on_arrow(self):
        return timesince(self.created_at) + " ago"

    def clean(self):
        """Validate opportunity data."""
        super().clean()
        errors = {}

        # Closed date required for closed stages
        if self.stage in ["CLOSED_WON", "CLOSED_LOST"] and not self.closed_on:
            errors["closed_on"] = _(
                "Close date is required when stage is Closed Won/Lost"
            )

        # Amount required for closed won
        if self.stage == "CLOSED_WON" and not self.amount:
            errors["amount"] = _("Amount is required for Closed Won opportunities")

        if errors:
            raise ValidationError(errors)

    def recalculate_amount(self):
        """
        Recalculate opportunity amount from line items.
        Only updates if amount_source is CALCULATED or if line items exist.
        Returns True if amount was updated, False otherwise.
        """
        line_items = self.line_items.all()
        if line_items.exists():
            total = line_items.aggregate(total=Sum("total"))["total"] or Decimal("0")
            self.amount = total
            self.amount_source = "CALCULATED"
            return True
        if self.amount_source == "CALCULATED":
            # No line items but was calculated - reset to manual
            self.amount_source = "MANUAL"
            self.amount = Decimal("0")
            return True
        return False

    @property
    def days_in_current_stage(self):
        """Number of days the deal has been in its current stage."""
        if not self.stage_changed_at:
            return 0
        delta = timezone.now() - self.stage_changed_at
        return delta.days

    def get_aging_status(self, aging_configs=None):
        """Return 'green', 'yellow', or 'red' based on deal aging.

        Args:
            aging_configs: Optional dict {stage: StageAgingConfig} to avoid DB queries.
                           Pass this when processing lists to prevent N+1.
        """
        from .workflow import (
            CLOSED_STAGES,
            DEFAULT_STAGE_EXPECTED_DAYS,
            ROTTEN_MULTIPLIER,
        )

        if self.stage in CLOSED_STAGES:
            return "green"

        # Look up per-org config, fall back to defaults
        expected = DEFAULT_STAGE_EXPECTED_DAYS.get(self.stage)
        warning = None

        if aging_configs is not None:
            config = aging_configs.get(self.stage)
        else:
            config = StageAgingConfig.objects.filter(
                org=self.org, stage=self.stage
            ).first()

        if config:
            expected = config.expected_days
            warning = config.warning_days

        if expected is None:
            return "green"

        days = self.days_in_current_stage
        rotten_threshold = expected * ROTTEN_MULTIPLIER

        if days >= rotten_threshold:
            return "red"
        if warning and days >= warning:
            return "yellow"
        if days >= expected:
            return "yellow"
        return "green"

    @property
    def aging_status(self):
        """Return 'green', 'yellow', or 'red' based on deal aging."""
        return self.get_aging_status()

    def save(self, *args, **kwargs):
        """Auto-set probability and track stage changes."""
        from .workflow import STAGE_PROBABILITIES

        # Auto-set probability based on stage (only if probability is default/0)
        if self.probability == 0 or self.probability is None:
            self.probability = STAGE_PROBABILITIES.get(self.stage, 0)

        # Track stage changes for deal aging
        if not self._state.adding:
            old_stage = (
                Opportunity.objects.filter(pk=self.pk)
                .values_list("stage", flat=True)
                .first()
            )
            if old_stage and old_stage != self.stage:
                self.stage_changed_at = timezone.now()
                # Ensure stage_changed_at is persisted when update_fields is used
                if kwargs.get("update_fields") is not None:
                    update_fields = set(kwargs["update_fields"])
                    update_fields.add("stage_changed_at")
                    kwargs["update_fields"] = list(update_fields)
        else:
            # New record
            if not self.stage_changed_at:
                self.stage_changed_at = timezone.now()

        super().save(*args, **kwargs)


class OpportunityLineItem(BaseModel):
    """
    Line item for an opportunity - represents a product or service being quoted.
    Similar to InvoiceLineItem but for the pre-sale stage.
    """

    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    product = models.ForeignKey(
        "invoices.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunity_line_items",
    )

    # Item details (can override product info or be custom)
    name = models.CharField(_("Item Name"), max_length=255, blank=True)
    description = models.CharField(_("Description"), max_length=500, blank=True)

    # Quantity and pricing
    quantity = models.DecimalField(
        _("Quantity"), max_digits=10, decimal_places=2, default=1
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=12, decimal_places=2, default=0
    )

    # Per-item discount
    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=12, decimal_places=2, default=0
    )

    # Computed totals
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    total = models.DecimalField(_("Total"), max_digits=12, decimal_places=2, default=0)

    # Display order
    order = models.PositiveIntegerField(_("Order"), default=0)

    # Organization (for RLS)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="opportunity_line_items",
    )

    class Meta:
        verbose_name = "Opportunity Line Item"
        verbose_name_plural = "Opportunity Line Items"
        db_table = "opportunity_line_item"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["opportunity"]),
            models.Index(fields=["org"]),
        ]

    def __str__(self):
        display_name = (
            self.name if self.name else (self.product.name if self.product else "Item")
        )
        return f"{display_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        """Calculate totals before saving."""
        # Get name from product if not set
        if not self.name and self.product:
            self.name = self.product.name

        # Get unit price from product if not set and product exists
        if self.unit_price == 0 and self.product:
            self.unit_price = self.product.price or Decimal("0")

        # Calculate subtotal
        self.subtotal = self.quantity * self.unit_price

        # Calculate discount
        if self.discount_type == "PERCENTAGE":
            self.discount_amount = self.subtotal * (
                self.discount_value / Decimal("100")
            )
        else:
            self.discount_amount = self.discount_value

        # Calculate total (no tax at opportunity stage)
        self.total = self.subtotal - self.discount_amount

        # Ensure org is set from opportunity if not provided
        if not self.org_id and self.opportunity_id:
            self.org_id = self.opportunity.org_id

        super().save(*args, **kwargs)

        # Recalculate opportunity amount after saving line item
        if self.opportunity:
            self.opportunity.recalculate_amount()
            self.opportunity.save(update_fields=["amount", "amount_source"])

    def delete(self, *args, **kwargs):
        """Recalculate opportunity amount after deleting line item."""
        opportunity = self.opportunity
        super().delete(*args, **kwargs)

        # Recalculate opportunity amount after deletion
        if opportunity:
            opportunity.recalculate_amount()
            opportunity.save(update_fields=["amount", "amount_source"])


class StageAgingConfig(BaseModel):
    """Per-org configuration for expected days in each pipeline stage."""

    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="stage_aging_configs",
    )
    stage = models.CharField(_("Stage"), max_length=64, choices=STAGES)
    expected_days = models.PositiveIntegerField(_("Expected Days"), default=14)
    warning_days = models.PositiveIntegerField(_("Warning Days"), null=True, blank=True)

    class Meta:
        verbose_name = "Stage Aging Config"
        verbose_name_plural = "Stage Aging Configs"
        db_table = "stage_aging_config"
        unique_together = ("org", "stage")

    def __str__(self):
        return f"{self.org.name} - {self.stage}: {self.expected_days}d"


class SalesGoal(BaseModel):
    """Sales goal / quota for tracking revenue or deals closed targets."""

    name = models.CharField(_("Goal Name"), max_length=255)
    goal_type = models.CharField(_("Goal Type"), max_length=20, choices=GOAL_TYPES)
    target_value = models.DecimalField(
        _("Target Value"), max_digits=12, decimal_places=2
    )
    period_type = models.CharField(
        _("Period Type"), max_length=20, choices=PERIOD_TYPES
    )
    period_start = models.DateField(_("Period Start"))
    period_end = models.DateField(_("Period End"))
    assigned_to = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_goals",
    )
    team = models.ForeignKey(
        Teams,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_goals",
    )
    type_weights = models.JSONField(
        _("Deal Type Weights"),
        default=dict,
        blank=True,
        help_text=(
            "Optional multiplier per opportunity type, e.g. "
            '{"RENEWAL": 0.5, "NEW_BUSINESS": 1.5}. A type left out of the map, '
            "and a deal with no type at all, counts at its full value. An empty "
            "map is an unweighted goal. Not accepted on an ACTIVITIES goal, "
            "which has no deal type to weigh."
        ),
    )
    is_active = models.BooleanField(default=True)
    milestone_50_notified = models.BooleanField(default=False)
    milestone_90_notified = models.BooleanField(default=False)
    milestone_100_notified = models.BooleanField(default=False)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="sales_goals",
    )

    class Meta:
        verbose_name = "Sales Goal"
        verbose_name_plural = "Sales Goals"
        db_table = "sales_goal"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "period_start", "period_end"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_goal_type_display()})"

    def member_profile_ids(self):
        """Profile ids whose work counts toward this goal, or None for the org.

        `None` is "everybody in the org", which is what an unassigned goal
        means. It is distinct from `[]`, an empty team, which counts nothing.
        """
        if self.assigned_to_id:
            return [self.assigned_to_id]
        if self.team_id:
            return list(
                Profile.objects.filter(
                    user_teams=self.team_id, is_active=True
                ).values_list("id", flat=True)
            )
        return None

    def weight_for(self, deal_type):
        """The multiplier this goal applies to one opportunity type.

        A type missing from the map, and a deal saved without a type at all,
        weigh 1: an unlisted type is not a type worth nothing, it is a type
        nobody chose to re-weigh. `type_weights` is validated on write, so the
        values here are already numbers.
        """
        if not self.type_weights or not deal_type:
            return Decimal("1")
        weight = self.type_weights.get(deal_type)
        return Decimal("1") if weight is None else Decimal(str(weight))

    def tally_to_progress(self, tally):
        """Fold a per-deal-type tally into this goal's single progress number.

        `tally` maps an opportunity type (or None) to `(amount, count)`. Both
        the single-goal query and the batched `attach_progress` build one of
        these, so the weighting arithmetic has exactly one definition rather
        than a copy per call site.
        """
        unit = 0 if self.goal_type == "REVENUE" else 1
        total = Decimal("0")
        for deal_type, amounts in tally.items():
            total += self.weight_for(deal_type) * amounts[unit]
        return total

    def _opportunity_queryset(self):
        """Won deals in this goal's period, one row per deal.

        `Opportunity.assigned_to` is a ManyToMany, so filtering a team goal with
        `assigned_to__in=<members>` joins the through table and returns a deal
        once per member who holds it. Summing that double-counted a deal shared
        by two teammates and inflated the team's attainment. The membership test
        is a subquery on `id` instead, which is set membership and cannot
        duplicate a row.
        """
        opps = Opportunity.objects.filter(
            org=self.org,
            stage="CLOSED_WON",
            closed_on__gte=self.period_start,
            closed_on__lte=self.period_end,
        )
        member_ids = self.member_profile_ids()
        if member_ids is None:
            return opps
        return opps.filter(
            id__in=Opportunity.objects.filter(assigned_to__in=member_ids).values("id")
        )

    def _activity_count(self):
        """Activities logged by this goal's owners inside the period."""
        from common.models import Activity

        activities = Activity.objects.filter(
            org=self.org,
            created_at__date__gte=self.period_start,
            created_at__date__lte=self.period_end,
        )
        member_ids = self.member_profile_ids()
        if member_ids is not None:
            activities = activities.filter(user_id__in=member_ids)
        return Decimal(str(activities.count()))

    def compute_progress(self):
        """Compute current progress toward this goal.

        REVENUE and DEALS_CLOSED read CLOSED_WON opportunities in the period;
        ACTIVITIES counts logged activity instead, so `type_weights` does not
        apply to it and is refused on write.

        Results are cached on the instance to avoid redundant DB queries when
        progress_percent and status are accessed in the same request. For a
        collection of goals, call `SalesGoal.attach_progress` first: it fills
        this same cache for every goal in a fixed number of queries.
        """
        if hasattr(self, "_cached_progress"):
            return self._cached_progress

        if self.goal_type == "ACTIVITIES":
            result = self._activity_count()
        else:
            # Grouped by type even when unweighted: it is still one query, and
            # it keeps both progress paths reading the same tally shape.
            tally = {
                row["opportunity_type"]: (
                    row["amount_total"],
                    Decimal(str(row["deal_count"])),
                )
                for row in self._opportunity_queryset()
                .values("opportunity_type")
                .annotate(
                    amount_total=Coalesce(Sum("amount"), Decimal("0")),
                    deal_count=Count("id"),
                )
            }
            result = self.tally_to_progress(tally)

        self._cached_progress = result
        return result

    @classmethod
    def attach_progress(cls, goals):
        """Populate every goal's progress cache in a fixed number of queries.

        Serializing a list called `compute_progress()` once per goal, so a page
        of N goals cost N aggregate queries (the web list asks for up to 1000).
        This reads the won deals and activities that span the whole collection's
        period once, then does the per-goal arithmetic in Python through the
        same `tally_to_progress` the single-goal path uses.

        Returns the list it was given, so callers can wrap a queryset inline.
        """
        goals = list(goals)
        pending = [g for g in goals if not hasattr(g, "_cached_progress")]
        if not pending:
            return goals

        org_ids = {g.org_id for g in pending}
        period_start = min(g.period_start for g in pending)
        period_end = max(g.period_end for g in pending)

        # One query for every team referenced, so a page of team goals does not
        # re-read the same membership per goal.
        team_ids = {g.team_id for g in pending if g.team_id and not g.assigned_to_id}
        members_by_team = {team_id: [] for team_id in team_ids}
        if team_ids:
            for team_id, profile_id in Profile.objects.filter(
                user_teams__in=team_ids, is_active=True
            ).values_list("user_teams__id", "id"):
                if team_id in members_by_team:
                    members_by_team[team_id].append(profile_id)

        deal_goals = [g for g in pending if g.goal_type != "ACTIVITIES"]
        deals = []
        if deal_goals:
            # One row per (deal, assignee); an unassigned deal still arrives
            # once, with a null profile, because an org-wide goal counts it.
            deals = list(
                Opportunity.objects.filter(
                    org_id__in=org_ids,
                    stage="CLOSED_WON",
                    closed_on__gte=period_start,
                    closed_on__lte=period_end,
                ).values_list(
                    "id",
                    "org_id",
                    "closed_on",
                    "opportunity_type",
                    "amount",
                    "assigned_to__id",
                )
            )

        activity_goals = [g for g in pending if g.goal_type == "ACTIVITIES"]
        activities = []
        if activity_goals:
            from common.models import Activity

            activities = list(
                Activity.objects.filter(
                    org_id__in=org_ids,
                    created_at__date__gte=period_start,
                    created_at__date__lte=period_end,
                ).values_list("org_id", "created_at", "user_id")
            )

        for goal in pending:
            if goal.assigned_to_id:
                member_ids = {goal.assigned_to_id}
            elif goal.team_id:
                member_ids = set(members_by_team.get(goal.team_id, []))
            else:
                member_ids = None

            if goal.goal_type == "ACTIVITIES":
                goal._cached_progress = Decimal(
                    str(
                        sum(
                            1
                            for org_id, created_at, user_id in activities
                            if org_id == goal.org_id
                            and goal.period_start
                            <= timezone.localtime(created_at).date()
                            <= goal.period_end
                            and (member_ids is None or user_id in member_ids)
                        )
                    )
                )
                continue

            tally = {}
            counted = set()
            for (
                deal_id,
                org_id,
                closed_on,
                deal_type,
                amount,
                profile_id,
            ) in deals:
                if org_id != goal.org_id or deal_id in counted:
                    continue
                if not (goal.period_start <= closed_on <= goal.period_end):
                    continue
                if member_ids is not None and profile_id not in member_ids:
                    continue
                # `counted` is what stops a deal shared by two teammates from
                # landing in the tally twice, the same duplicate the single-goal
                # subquery avoids in SQL.
                counted.add(deal_id)
                amount_total, deal_count = tally.get(
                    deal_type, (Decimal("0"), Decimal("0"))
                )
                tally[deal_type] = (
                    amount_total + (amount or Decimal("0")),
                    deal_count + 1,
                )
            goal._cached_progress = goal.tally_to_progress(tally)

        return goals

    @property
    def progress_percent(self):
        """Progress as an integer percentage, capped at 100."""
        if not self.target_value or self.target_value == 0:
            return 0
        progress = self.compute_progress()
        return min(int(progress / self.target_value * 100), 100)

    @property
    def status(self):
        """Goal status based on progress vs expected pace."""
        percent = self.progress_percent
        if percent >= 100:
            return "completed"

        today = timezone.localdate()
        if today < self.period_start:
            expected_pace = 0
        elif today >= self.period_end:
            expected_pace = 100
        else:
            total_days = (self.period_end - self.period_start).days or 1
            elapsed_days = (today - self.period_start).days
            expected_pace = (elapsed_days / total_days) * 100

        if expected_pace == 0:
            return "on_track"
        if percent >= expected_pace:
            return "on_track"
        if percent >= expected_pace * 0.8:
            return "at_risk"
        return "behind"
