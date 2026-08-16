from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from accounts.models import Account
from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel
from common.models import Org, Profile, Tags, Teams
from common.utils import CASE_TYPE, CURRENCY_CODES, PRIORITY_CHOICE, STATUS_CHOICE
from contacts.models import Contact

# Cleanup notes:
# - Removed 'created_on_arrow' property from Case and Solution (frontend computes its own timestamps)
# - Fixed case_type default from "" to None (empty string is bad default for nullable field)


class Case(AssignableMixin, BaseModel):
    name = models.CharField(pgettext_lazy("Name of the case", "Name"), max_length=64)
    status = models.CharField(choices=STATUS_CHOICE, max_length=64)
    priority = models.CharField(choices=PRIORITY_CHOICE, max_length=64)
    case_type = models.CharField(
        choices=CASE_TYPE, max_length=255, blank=True, null=True, default=None
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="accounts_cases",
    )
    contacts = models.ManyToManyField(Contact, related_name="case_contacts")
    closed_on = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    assigned_to = models.ManyToManyField(Profile, related_name="case_assigned_users")
    is_active = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)
    teams = models.ManyToManyField(Teams, related_name="cases_teams")
    tags = models.ManyToManyField(Tags, related_name="case_tags", blank=True)
    watchers = models.ManyToManyField(
        Profile,
        through="CaseWatcher",
        related_name="case_watching",
        blank=True,
    )
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="cases")

    # SLA Tracking Fields
    first_response_at = models.DateTimeField(
        _("First Response At"), blank=True, null=True
    )
    resolved_at = models.DateTimeField(_("Resolved At"), blank=True, null=True)
    sla_first_response_hours = models.PositiveIntegerField(
        _("First Response SLA (hours)"),
        default=4,
        help_text="Target hours for first response",
    )
    sla_resolution_hours = models.PositiveIntegerField(
        _("Resolution SLA (hours)"),
        default=24,
        help_text="Target hours for resolution",
    )

    # Kanban/Pipeline support
    stage = models.ForeignKey(
        "CaseStage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cases",
        help_text="Current pipeline stage (null = use status-based kanban)",
    )
    kanban_order = models.DecimalField(
        _("Kanban Order"),
        max_digits=15,
        decimal_places=6,
        default=0,
        help_text="Order within the kanban column for drag-drop positioning",
    )

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # SLA pause: when status flips to Pending we record the moment, and on
    # any other status we accumulate the elapsed wait into sla_paused_seconds.
    # Both are managed by `cases.signals.case_pre_save_sla_pause` so the
    # invariants hold regardless of which view writes the case.
    sla_paused_at = models.DateTimeField(blank=True, null=True)
    sla_paused_seconds = models.PositiveIntegerField(default=0)

    # Escalation tracking (driven by cases.tasks.scan_for_breached_cases)
    last_escalation_fired_at = models.DateTimeField(
        _("Last Escalation Fired At"),
        blank=True,
        null=True,
        db_index=True,
    )
    escalation_count = models.PositiveSmallIntegerField(default=0)

    # Inbound-email threading: first Message-ID seen for this case, used as a
    # cheap external lookup before scanning EmailMessage rows. Set on case
    # creation from inbound; left null for cases created via the agent UI.
    external_thread_id = models.CharField(
        max_length=255, blank=True, null=True, db_index=True
    )

    # Merge: when this case is the duplicate ("source"), `merged_into` points
    # to the surviving primary. `merged_from_cases` is the reverse manager.
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from_cases",
        db_index=True,
    )
    merged_at = models.DateTimeField(blank=True, null=True, db_index=True)
    merged_by = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_cases",
    )
    # When a duplicate is merged in, its external_thread_id (and any of its
    # own alt thread ids) is appended here on the primary so that future
    # inbound emails to the duplicate's thread still match the primary.
    alt_thread_ids = models.JSONField(default=list, blank=True)
    # Merge audit (set on the source/duplicate when merged; cleared on
    # unmerge). Captures the IDs of items relocated to the target plus the
    # source's pre-merge status/closed_on so unmerge can reverse cleanly.
    # Schema: {"target_id": str, "moved_comment_ids": [str], "moved_attachment_ids": [str],
    # "moved_email_ids": [str], "inherited_thread_ids": [str], "source_prior_status": str,
    # "source_prior_closed_on": str|null}
    merge_record = models.JSONField(null=True, blank=True)

    # Tier 3 parent/child: ITIL problem→incident linkage. Cycle / depth /
    # self-link guards live in `clean()`; cascade close is a separate
    # endpoint, see `cases.parent_views.CaseCloseWithChildrenView`.
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    is_problem = models.BooleanField(
        default=False,
        help_text="Marks this case as an ITIL 'problem' (umbrella ticket).",
    )

    class Meta:
        verbose_name = "Case"
        verbose_name_plural = "Cases"
        db_table = "case"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["stage", "kanban_order"]),
            models.Index(fields=["status", "kanban_order"]),
            # Analytics resolution-window scans (MTTR by_priority, SLA breach).
            models.Index(fields=["org", "resolved_at"], name="case_org_resolved_idx"),
            # Listing children of a parent (Tier 3 parent-child).
            models.Index(fields=["parent"], name="case_parent_idx"),
        ]

    def __str__(self):
        return f"{self.name}"

    # Tier 3 parent/child: max nesting (problem → incident → sub-incident).
    PARENT_MAX_DEPTH = 3

    def clean(self):
        """Validate case data."""
        super().clean()
        errors = {}

        # Closed date required when status is Closed
        if self.status == "Closed" and not self.closed_on:
            errors["closed_on"] = _("Closed date is required when closing a case")

        # Tier 3 approvals: when transitioning *into* Closed and an active
        # rule matches, require a recorded `approved` Approval. Skip the
        # check when callers explicitly opt out (e.g. internal auto-flows
        # that have already verified the gate or migration data fixtures).
        if (
            self.status == "Closed"
            and self.org_id
            and not getattr(self, "_approval_gate_skip", False)
        ):
            old_status = None
            if self.pk:
                try:
                    old_status = (
                        type(self).objects.only("status").get(pk=self.pk).status
                    )
                except type(self).DoesNotExist:
                    old_status = None
            if old_status != "Closed":
                from cases.approvals import Approval, find_matching_rule

                rule = find_matching_rule(self, trigger_event="pre_close")
                if rule is not None:
                    has_approved = Approval.objects.filter(
                        case_id=self.pk, rule=rule, state="approved"
                    ).exists()
                    if not has_approved:
                        errors["status"] = _(
                            "An approval is required before this case can be "
                            "closed (rule: %(rule)s)."
                        ) % {"rule": rule.name}

        # Parent/child guards.
        if self.parent_id is not None:
            if self.parent_id == self.id:
                errors["parent"] = _("A case cannot be its own parent.")
            else:
                parent_obj = self.parent
                # Cycle: walk the parent chain to ensure self is not an ancestor.
                seen = {self.id} if self.id else set()
                cursor = parent_obj
                depth = 1
                while cursor is not None:
                    if cursor.id in seen:
                        errors["parent"] = _(
                            "Linking would create a cycle in the case tree."
                        )
                        break
                    seen.add(cursor.id)
                    cursor = cursor.parent
                    depth += 1
                else:
                    if depth > self.PARENT_MAX_DEPTH:
                        errors["parent"] = _(
                            "Case tree is limited to %(max)d levels."
                        ) % {"max": self.PARENT_MAX_DEPTH}
                # Block linking to/from a Duplicate (merged) case.
                if parent_obj is not None and parent_obj.status == "Duplicate":
                    errors["parent"] = _("Cannot link to a case that has been merged.")
                if self.status == "Duplicate":
                    errors["parent"] = _(
                        "A merged case cannot be linked under a parent."
                    )

        if errors:
            raise ValidationError(errors)

    def ancestor_ids(self):
        """Return a set of ancestor case IDs by walking the parent chain."""
        out = set()
        cursor = self.parent
        while cursor is not None and cursor.id not in out:
            out.add(cursor.id)
            cursor = cursor.parent
        return out

    def save(self, *args, **kwargs):
        """Auto-set SLA values based on priority for new cases."""
        from .workflow import DEFAULT_FIRST_RESPONSE_SLA, DEFAULT_RESOLUTION_SLA

        # Use `_state.adding` rather than `not self.pk`, BaseModel populates
        # `id` with a UUID default at __init__, so `self.pk` is truthy long
        # before the first INSERT.
        if self._state.adding:
            if self.sla_first_response_hours == 4:  # Default value
                self.sla_first_response_hours = DEFAULT_FIRST_RESPONSE_SLA.get(
                    self.priority, 4
                )
            if self.sla_resolution_hours == 24:  # Default value
                self.sla_resolution_hours = DEFAULT_RESOLUTION_SLA.get(
                    self.priority, 24
                )

        super().save(*args, **kwargs)

    def _sla_calendar(self):
        """Resolve the org's default BusinessCalendar (or None for 24/7)."""
        from business_hours.calendar import get_default_calendar

        if not self.org_id:
            return None
        return get_default_calendar(self.org_id)

    def _sla_deadline(self, hours):
        """Compute a deadline by walking ``hours`` business hours forward
        from ``created_at`` and pushing the answer forward by any time the
        case has spent in the Pending status (customer wait time).
        """
        from business_hours.calendar import add_business_hours

        if not self.created_at or hours is None:
            return None
        deadline = add_business_hours(self.created_at, hours, self._sla_calendar())
        # Customer wait time accumulated while Pending shifts the deadline
        # forward verbatim. We don't try to walk this through business hours
        # again because it represents real elapsed time the agent had to wait,
        # not a target that respects the calendar.
        paused = self.sla_paused_seconds or 0
        if self.sla_paused_at is not None:
            paused += max(int((timezone.now() - self.sla_paused_at).total_seconds()), 0)
        if paused:
            deadline = deadline + timedelta(seconds=paused)
        return deadline

    @property
    def is_sla_first_response_breached(self) -> bool:
        """Check if first response SLA has been breached."""
        if self.first_response_at:
            return False
        deadline = self.first_response_sla_deadline
        if deadline is None:
            return False
        return timezone.now() > deadline

    @property
    def is_sla_resolution_breached(self) -> bool:
        """Check if resolution SLA has been breached."""
        if self.resolved_at:
            return False
        deadline = self.resolution_sla_deadline
        if deadline is None:
            return False
        return timezone.now() > deadline

    @property
    def first_response_sla_deadline(self):
        """Return the deadline for first response in business hours."""
        return self._sla_deadline(self.sla_first_response_hours)

    @property
    def resolution_sla_deadline(self):
        """Return the deadline for resolution in business hours."""
        return self._sla_deadline(self.sla_resolution_hours)


class CaseWatcher(BaseModel):
    """A profile subscribed to updates on a case.

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own org FK rather than using BaseOrgModel. RLS still
    applies via the migration that adds the `case_watcher` table.
    """

    SUBSCRIBED_VIA_CHOICES = (
        ("manual", "Manual"),
        ("mention", "Mention"),
        ("auto_assignee", "Auto (Assignee)"),
        ("auto_team", "Auto (Team)"),
    )

    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="watcher_rows"
    )
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="watched_cases"
    )
    subscribed_via = models.CharField(
        max_length=16, choices=SUBSCRIBED_VIA_CHOICES, default="manual"
    )
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="case_watchers")

    class Meta:
        verbose_name = "Case Watcher"
        verbose_name_plural = "Case Watchers"
        db_table = "case_watcher"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["profile", "-created_at"]),
            models.Index(fields=["case"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("case", "profile"), name="uniq_case_watcher_per_profile"
            ),
        ]

    def __str__(self):
        return f"{self.profile_id} watching {self.case_id} ({self.subscribed_via})"


class CsatSurvey(BaseModel):
    """Customer satisfaction survey row (Tier 2 csat).

    One survey per Case (OneToOne). Created when the case closes and the
    Celery task `cases.tasks.send_csat_survey` fires; populated when the
    customer clicks the rating link in their email. The `token_hash` is
    SHA-256 of the signed token so a database leak never exposes raw
    tokens.

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own org FK rather than `BaseOrgModel`; RLS is enforced via
    the policy in the migration.
    """

    RATING_CHOICES = ((i, str(i)) for i in range(1, 6))

    case = models.OneToOneField(
        Case, on_delete=models.CASCADE, related_name="csat_survey"
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="csat_surveys",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    sent_at = models.DateTimeField()
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default="")
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="csat_surveys")

    class Meta:
        verbose_name = "CSAT Survey"
        verbose_name_plural = "CSAT Surveys"
        db_table = "csat_survey"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-responded_at"]),
            models.Index(fields=["case"]),
            models.Index(fields=["token_hash"]),
        ]

    def __str__(self):
        if self.rating is None:
            return f"CsatSurvey(case={self.case_id}, pending)"
        return f"CsatSurvey(case={self.case_id}, rating={self.rating})"


class Solution(BaseModel):
    """
    Knowledge Base Solution

    Solutions are reusable answers/guides that can be linked to cases.
    They form a knowledge base for common issues and their resolutions.
    """

    title = models.CharField(max_length=255)
    description = models.TextField()

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("reviewed", "Reviewed"),
        ("approved", "Approved"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    is_published = models.BooleanField(default=False)

    # Organization relation
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="solutions")

    # Cases that use this solution
    cases = models.ManyToManyField(Case, related_name="solutions", blank=True)

    # The agents' own vocabulary, shared with leads, deals and tickets. Written
    # for internal filing, not for customers, so tags stay off every portal
    # serializer and the portal uses them only to find related articles.
    tags = models.ManyToManyField(Tags, related_name="solution_tags", blank=True)

    class Meta:
        verbose_name = "Solution"
        verbose_name_plural = "Solutions"
        db_table = "solution"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["is_published"]),
            models.Index(fields=["org"]),
        ]

    def __str__(self):
        return self.title

    def publish(self):
        """Publish the solution (must be approved first)"""
        if self.status == "approved":
            self.is_published = True
            self.save()

    def unpublish(self):
        """Unpublish the solution"""
        self.is_published = False
        self.save()


class CasePipeline(BaseModel):
    """
    Custom pipeline for organizing cases into stages (Kanban columns).
    Each organization can have multiple pipelines (e.g., Support, Engineering, Billing).
    """

    name = models.CharField(_("Pipeline Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, null=True)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="case_pipelines"
    )
    is_default = models.BooleanField(
        default=False,
        help_text=(
            "Sorts this pipeline first in listings (see Meta.ordering) and is "
            "limited to one per org. It does NOT route new records: a case "
            "created without a stage keeps stage=NULL, which is the supported "
            "status-based kanban mode."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Case Pipeline"
        verbose_name_plural = "Case Pipelines"
        db_table = "case_pipeline"
        ordering = ("-is_default", "name")
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["org"],
                condition=models.Q(is_default=True),
                name="unique_default_case_pipeline_per_org",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.org.name})"


class CaseStage(BaseModel):
    """
    Stage within a Case Pipeline (Kanban column).
    """

    STAGE_TYPE_CHOICES = [
        ("open", "Open"),
        ("closed", "Closed"),
        ("rejected", "Rejected"),
    ]

    pipeline = models.ForeignKey(
        CasePipeline, on_delete=models.CASCADE, related_name="stages"
    )
    name = models.CharField(_("Stage Name"), max_length=100)
    order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=7, default="#6B7280")  # Hex color

    # Business logic fields
    stage_type = models.CharField(
        max_length=20, choices=STAGE_TYPE_CHOICES, default="open"
    )
    maps_to_status = models.CharField(
        _("Maps to Status"),
        max_length=64,
        blank=True,
        null=True,
        choices=STATUS_CHOICE,
        help_text="When case enters this stage, also update Case.status",
    )

    # Kanban features
    wip_limit = models.PositiveIntegerField(
        _("WIP Limit"),
        null=True,
        blank=True,
        help_text="Maximum cases allowed in this stage (null = unlimited)",
    )

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="case_stages")

    class Meta:
        verbose_name = "Case Stage"
        verbose_name_plural = "Case Stages"
        db_table = "case_stage"
        ordering = ("order",)
        unique_together = ("pipeline", "name")
        indexes = [
            models.Index(fields=["org", "order"]),
            models.Index(fields=["pipeline", "order"]),
        ]

    def __str__(self):
        return f"{self.pipeline.name} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.org_id and self.pipeline_id:
            self.org_id = self.pipeline.org_id
        super().save(*args, **kwargs)


class ReopenPolicy(BaseModel):
    """Per-org policy controlling auto-reopen of closed cases on customer reply.

    Singleton: one row per org. See docs/cases/tier1/reopen.md.
    """

    org = models.OneToOneField(
        Org, on_delete=models.CASCADE, related_name="reopen_policy"
    )
    is_enabled = models.BooleanField(default=True)
    reopen_window_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Replies within this many days of closed_on reopen the case.",
    )
    reopen_to_status = models.CharField(
        max_length=64,
        choices=STATUS_CHOICE,
        default="Pending",
        help_text="Status the case flips to on reopen (must be non-terminal).",
    )
    notify_assigned = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Reopen Policy"
        verbose_name_plural = "Reopen Policies"
        db_table = "reopen_policy"

    def __str__(self):
        return f"ReopenPolicy(org={self.org_id})"


class EscalationPolicy(BaseModel):
    """Per-org, per-priority escalation rules consumed by `scan_for_breached_cases`.

    See docs/cases/tier1/escalation.md.
    """

    ACTION_CHOICES = [
        ("notify", "Notify"),
        ("reassign", "Reassign"),
        ("notify_and_reassign", "Notify and reassign"),
    ]

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="escalation_policies"
    )
    priority = models.CharField(max_length=64, choices=PRIORITY_CHOICE)
    first_response_action = models.CharField(
        max_length=32, choices=ACTION_CHOICES, default="notify"
    )
    resolution_action = models.CharField(
        max_length=32, choices=ACTION_CHOICES, default="notify"
    )
    first_response_target = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="first_response_escalation_policies",
        help_text="Profile reassigned to and/or notified on first-response breach.",
    )
    resolution_target = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="resolution_escalation_policies",
        help_text="Profile reassigned to and/or notified on resolution breach.",
    )
    notify_team = models.ForeignKey(
        Teams,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="escalation_policies",
        help_text="Optional team CC'd on either breach action.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Escalation Policy"
        verbose_name_plural = "Escalation Policies"
        db_table = "escalation_policy"
        ordering = ("priority",)
        constraints = [
            models.UniqueConstraint(
                fields=["org", "priority"],
                name="uniq_escalation_policy_per_org_priority",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "is_active"]),
        ]

    def __str__(self):
        return f"EscalationPolicy(org={self.org_id}, priority={self.priority})"


class InboundMailbox(BaseModel):
    """Per-org inbound email address configuration. See docs/cases/tier1/email-to-ticket.md.

    Tier 1 ships only the SES (SNS direct delivery) provider; the model carries
    the IMAP fields nullable so a follow-up can wire in IMAP without a schema
    migration.
    """

    PROVIDER_CHOICES = [
        ("ses", "AWS SES (SNS direct delivery)"),
        ("mailgun", "Mailgun"),
        ("postmark", "Postmark"),
        ("imap", "IMAP"),
    ]

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="inbound_mailboxes"
    )
    address = models.EmailField(_("Inbound Address"))
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, default="ses")
    webhook_secret = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Reserved for providers that sign deliveries with a shared "
        "secret, none of which are implemented yet. Nothing compares this "
        "value today: the SES webhook is authenticated by the SNS signature "
        "and the topic_arn pin below. Admin-writable so a provider-issued key "
        "can be stored, and write-only, so it is never returned by the API.",
    )
    topic_arn = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text="SNS Topic ARN this mailbox accepts mail from. A valid SNS "
        "signature only proves a message came from some topic, so the webhook "
        "also requires this exact match. Pinned automatically from the first "
        "verified SubscriptionConfirmation when left blank; until it is set, "
        "notifications are rejected.",
    )

    # Reserved for future IMAP support (Tier 1+ follow-up).
    imap_host = models.CharField(max_length=255, blank=True, default="")
    imap_port = models.PositiveIntegerField(blank=True, null=True)
    imap_username = models.CharField(max_length=255, blank=True, default="")
    imap_password_enc = models.CharField(max_length=512, blank=True, default="")

    default_priority = models.CharField(
        max_length=64, choices=PRIORITY_CHOICE, default="Normal"
    )
    default_case_type = models.CharField(
        max_length=255,
        choices=[
            ("Question", "Question"),
            ("Incident", "Incident"),
            ("Problem", "Problem"),
        ],
        blank=True,
        null=True,
    )
    default_assignee = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="default_inbound_mailboxes",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Inbound Mailbox"
        verbose_name_plural = "Inbound Mailboxes"
        db_table = "inbound_mailbox"
        ordering = ("address",)
        constraints = [
            models.UniqueConstraint(
                fields=["org", "address"],
                name="uniq_inbound_mailbox_per_org_address",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "is_active"]),
        ]

    def __str__(self):
        return f"InboundMailbox({self.address}, org={self.org_id})"


class EmailMessage(BaseModel):
    """One row per inbound or outbound email handled by the system.

    `message_id` is the RFC-5322 Message-ID and is the primary threading key.
    `in_reply_to` and `references` are the secondary keys used when the first
    inbound message of a thread has already been turned into a Case.
    """

    DIRECTION_CHOICES = [("inbound", "Inbound"), ("outbound", "Outbound")]

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="email_messages"
    )
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="email_messages",
        null=True,
        blank=True,
        help_text="Null when the message was dropped (spam/bounce/auto-reply) "
        "but we still want an audit trail.",
    )
    mailbox = models.ForeignKey(
        "InboundMailbox",
        on_delete=models.SET_NULL,
        related_name="email_messages",
        null=True,
        blank=True,
        help_text="The inbound address this message arrived through. Set at "
        "ingest; SET_NULL keeps the audit row if the mailbox is later deleted. "
        "Null on historical rows the backfill could not attribute.",
    )
    direction = models.CharField(
        max_length=16, choices=DIRECTION_CHOICES, default="inbound"
    )
    message_id = models.CharField(max_length=512, db_index=True)
    in_reply_to = models.CharField(
        max_length=512, blank=True, default="", db_index=True
    )
    references = models.TextField(
        blank=True,
        default="",
        help_text="Whitespace-separated list of RFC-5322 Message-IDs from the "
        "References header.",
    )
    from_address = models.EmailField()
    to_addresses = models.TextField(blank=True, default="")
    cc_addresses = models.TextField(blank=True, default="")
    subject = models.CharField(max_length=512, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    received_at = models.DateTimeField()
    drop_reason = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="If non-empty, the message was rejected (spam/bounce/etc.) "
        "before any Case was touched. `case` will be null in this case.",
    )

    class Meta:
        verbose_name = "Email Message"
        verbose_name_plural = "Email Messages"
        db_table = "email_message"
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["org", "message_id"],
                name="uniq_email_message_per_org_id",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "-received_at"]),
            models.Index(fields=["case", "-received_at"]),
            models.Index(fields=["in_reply_to"]),
        ]

    def __str__(self):
        return f"EmailMessage({self.message_id}, case={self.case_id})"


class RoutingRule(BaseModel):
    """Per-org auto-assignment rule for newly created Cases.

    See docs/cases/tier1/auto-routing.md. The engine in `cases.routing.evaluate`
    walks active rules ordered by `priority_order`, runs the first match's
    `strategy`, and (when `stop_processing=True`) skips lower-priority rules.

    `conditions` is a list of `{field, op, value}` dicts. Supported `field`s:
    `priority`, `case_type`, `account`, `tags`, `custom_fields.<key>`,
    `from_email_domain`, `mailbox_id`. Supported `op`s: `eq`, `in`, `contains`,
    `regex`. AND across conditions; OR is modeled by writing multiple rules.
    """

    STRATEGY_CHOICES = [
        ("round_robin", "Round-robin within target_assignees"),
        ("least_busy", "Least busy in target_assignees"),
        ("direct", "Single profile in target_assignees"),
        ("by_team", "Assign to target_team"),
    ]

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="routing_rules")
    name = models.CharField(max_length=255)
    priority_order = models.PositiveIntegerField(
        default=100,
        db_index=True,
        help_text="Lower runs first; ties broken by created_at.",
    )
    is_active = models.BooleanField(default=True)
    conditions = models.JSONField(
        default=list,
        blank=True,
        help_text="List of {field, op, value} triples; AND across entries.",
    )
    strategy = models.CharField(
        max_length=16, choices=STRATEGY_CHOICES, default="direct"
    )
    target_assignees = models.ManyToManyField(
        Profile, related_name="routing_rules", blank=True
    )
    target_team = models.ForeignKey(
        Teams,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="routing_rules",
    )
    stop_processing = models.BooleanField(
        default=True,
        help_text="If a rule matches and this is True, skip lower-priority rules.",
    )

    class Meta:
        verbose_name = "Routing Rule"
        verbose_name_plural = "Routing Rules"
        db_table = "routing_rule"
        ordering = ("priority_order", "created_at")
        indexes = [
            models.Index(
                fields=["org", "is_active", "priority_order"],
                name="rr_eval_idx",
            ),
        ]

    def __str__(self):
        return f"RoutingRule({self.name!r}, org={self.org_id})"


class RoutingRuleState(BaseModel):
    """Mutable per-rule cursor for round_robin strategies.

    One row per RoutingRule with `strategy="round_robin"`. The engine takes a
    `SELECT ... FOR UPDATE` on this row inside the assignment transaction so
    concurrent Case creations cannot both pick agent index 0.
    """

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="routing_rule_states"
    )
    rule = models.OneToOneField(
        RoutingRule, on_delete=models.CASCADE, related_name="state"
    )
    last_assigned_index = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Routing Rule State"
        verbose_name_plural = "Routing Rule States"
        db_table = "routing_rule_state"

    def __str__(self):
        return f"RoutingRuleState(rule={self.rule_id}, idx={self.last_assigned_index})"


class TimeEntry(BaseModel):
    """One agent's logged time on a case (Tier 3 time-tracking).

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own org FK; RLS is enforced via the migration policy. The
    `one_active_timer_per_profile` partial unique index guarantees a profile
    cannot have two running timers concurrently. The start endpoint relies
    on this constraint as a race-defense in addition to its own existence
    check.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="time_entries")
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="time_entries"
    )
    profile = models.ForeignKey(
        Profile, on_delete=models.PROTECT, related_name="time_entries"
    )

    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(
        null=True, blank=True, help_text="Null while the timer is running."
    )
    duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Recomputed on save when both timestamps are present.",
    )

    description = models.TextField(blank=True, default="")
    billable = models.BooleanField(default=False)
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Snapshot at log time so future rate changes do not alter history.",
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CODES, default="USD")

    invoice = models.ForeignKey(
        "invoices.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="time_entries",
        help_text="Set when this entry has been pushed to a draft invoice.",
    )
    auto_stopped = models.BooleanField(
        default=False,
        help_text="Set by the auto-stop Celery task when a forgotten timer is killed.",
    )

    class Meta:
        verbose_name = "Time Entry"
        verbose_name_plural = "Time Entries"
        db_table = "time_entry"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["org", "profile", "started_at"]),
            models.Index(fields=["case", "started_at"]),
            models.Index(fields=["billable", "invoice"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(ended_at__isnull=True)
                    | models.Q(ended_at__gte=models.F("started_at"))
                ),
                name="time_entry_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["profile"],
                condition=models.Q(ended_at__isnull=True),
                name="one_active_timer_per_profile",
            ),
        ]

    def __str__(self):
        return (
            f"TimeEntry(case={self.case_id}, profile={self.profile_id}, "
            f"{self.duration_minutes}m)"
        )

    @property
    def is_running(self):
        return self.ended_at is None

    def compute_duration_minutes(self):
        if not self.ended_at:
            return 0
        delta = self.ended_at - self.started_at
        return max(int(delta.total_seconds() // 60), 0)

    def save(self, *args, **kwargs):
        if self.ended_at:
            self.duration_minutes = self.compute_duration_minutes()
        else:
            self.duration_minutes = 0
        if not self.org_id and self.case_id:
            self.org_id = self.case.org_id
        super().save(*args, **kwargs)


# Tier 3 approval workflows. The two model classes live in cases/approvals.py
# but must be importable through ``cases.models`` so Django's app registry,
# `makemigrations`, and existing reverse-related lookups all resolve them.
from cases.approvals import Approval, ApprovalRule  # noqa: E402,F401
