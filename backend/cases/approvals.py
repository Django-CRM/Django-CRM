"""
Tier 3 approval workflows.

Two org-scoped models live here:

* ``ApprovalRule``: admin-configurable predicate. When an active rule matches
  a case (priority + case_type + team filters), the close transition is gated
  until an ``Approval`` row in state ``approved`` exists.
* ``Approval``: one row per request. State machine:
  ``pending`` -> ``approved`` | ``rejected`` | ``cancelled``.

Both models follow ``COORDINATION_DECISIONS.md`` D2: inherit ``BaseModel`` and
declare an explicit ``org`` FK; RLS is enforced by the migration that adds
``approval_rule`` / ``approval`` to the policy set.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from common.base import BaseModel
from common.models import Org, Profile, Teams
from common.permissions import is_org_admin
from common.utils import CASE_TYPE, PRIORITY_CHOICE

# Approver roles. Mirrors the spec's ``ADMIN``/``MANAGER`` choices even though
# the project's ``Profile.role`` only knows ``ADMIN``/``USER`` today, keeping
# ``MANAGER`` as a reserved value avoids a future schema change when the role
# model expands. Today, MANAGER simply matches no profiles.
APPROVER_ROLE_CHOICES = (
    ("ADMIN", "Admin"),
    ("MANAGER", "Manager"),
)

TRIGGER_EVENT_CHOICES = (("pre_close", "Pre-Close"),)

APPROVAL_STATE_CHOICES = (
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("cancelled", "Cancelled"),
)


class ApprovalRule(BaseModel):
    """Admin-configured rule that gates a case transition."""

    name = models.CharField(max_length=128)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="approval_rules"
    )

    trigger_event = models.CharField(
        max_length=16, choices=TRIGGER_EVENT_CHOICES, default="pre_close"
    )
    match_priority = models.CharField(
        max_length=32, choices=PRIORITY_CHOICE, blank=True, null=True
    )
    match_case_type = models.CharField(
        max_length=32, choices=CASE_TYPE, blank=True, null=True
    )
    match_team = models.ForeignKey(
        Teams,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_rules",
    )

    approver_role = models.CharField(
        max_length=16, choices=APPROVER_ROLE_CHOICES, default="ADMIN"
    )
    approvers = models.ManyToManyField(
        Profile, blank=True, related_name="approval_rules"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Approval Rule"
        verbose_name_plural = "Approval Rules"
        db_table = "approval_rule"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=["org", "trigger_event", "is_active"],
                name="approval_rule_match_idx",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.trigger_event})"

    @property
    def specificity(self) -> int:
        """Tie-breaker for `find_matching_rule`: more filters set = more specific."""
        return sum(
            1
            for v in (self.match_priority, self.match_case_type, self.match_team_id)
            if v
        )

    def matches(self, case) -> bool:
        """Return True when this rule applies to ``case``."""
        if not self.is_active:
            return False
        if self.org_id != case.org_id:
            return False
        if self.match_priority and case.priority != self.match_priority:
            return False
        if self.match_case_type and case.case_type != self.match_case_type:
            return False
        if self.match_team_id and not case.teams.filter(id=self.match_team_id).exists():
            return False
        return True


def find_matching_rule(case, trigger_event: str = "pre_close"):
    """Return the most-specific active rule matching ``case``, or ``None``.

    Specificity = number of filters set. Ties break by ``-created_at`` (last
    write wins) so admins can override an older rule by creating a newer one.
    """
    rules = (
        ApprovalRule.objects.filter(
            org_id=case.org_id, trigger_event=trigger_event, is_active=True
        )
        .prefetch_related("match_team")
        .order_by("-created_at")
    )
    candidates = [r for r in rules if r.matches(case)]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.specificity, reverse=True)
    return candidates[0]


def closing_date(case, *, status, closed_on):
    """The ``closed_on`` a write should leave on ``case``.

    ``closed_on`` is the date the write would leave (the one it sends, else
    the stored one). A write that moves the ticket into Closed without one is
    dated today in the org's timezone, which `GetProfileAndOrg` activates for
    the request, so ``timezone.localdate()`` is the org's day. A date the
    caller sends always wins.

    This is the one place a close is dated. Clients used to compute "today"
    themselves, each with its own idea of the org's timezone, and one of them
    could not date a close at all for an org stored under a legacy zone name
    such as ``US/Eastern``. Every close path calls this: the serializer (POST,
    PUT, PATCH, bulk and macro apply) and the board move.

    Only the transition is dated: a ticket already Closed keeps whatever it
    has, so an edit to an old closed ticket does not re-date it.
    """
    if closed_on or status != "Closed":
        return closed_on
    if case is not None and case.status == "Closed":
        return closed_on
    return timezone.localdate()


def close_refusal(case, *, status, priority, case_type):
    """Why a write may not close ``case``, as ``{field: message}``, or ``None``.

    The close gate, one rule for both API paths that close a single ticket:
    the detail PUT/PATCH (`CaseCreateSerializer.validate`) and the board move
    (`CaseMoveView.patch`). The move used to set ``status="Closed"`` itself and
    skip the gate, so a drag closed a ticket that PATCH refused for want of an
    approval.

    ``case`` is the stored record, ``None`` on create; ``status``,
    ``priority`` and ``case_type`` are the values the write would leave on it.
    Only the transition into Closed is judged, so a case that is already
    Closed can be edited without re-approving. A rule matches on priority,
    case_type and team, so it is evaluated against the incoming values, or a
    caller could re-target the case out of the rule and close it in the same
    request. ``case`` is restored either way; nothing is saved.

    The closing date is not judged here: `closing_date` supplies one to a
    close that sends none, so the only refusal left is the approval.
    """
    if status != "Closed":
        return None
    if case is not None and case.status == "Closed":
        return None
    if case is None:
        return None

    saved = (case.priority, case.case_type)
    case.priority, case.case_type = priority, case_type
    try:
        rule = find_matching_rule(case, trigger_event="pre_close")
    finally:
        case.priority, case.case_type = saved
    if rule is None:
        return None
    if Approval.objects.filter(case_id=case.pk, rule=rule, state="approved").exists():
        return None
    return {
        "status": (
            f"An approval is required before this case can be closed (rule: {rule.name})."
        )
    }


class Approval(BaseModel):
    """A single approval request bound to one Case + ApprovalRule."""

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="approvals")
    case = models.ForeignKey(
        "cases.Case", on_delete=models.CASCADE, related_name="approvals"
    )
    rule = models.ForeignKey(
        ApprovalRule, on_delete=models.PROTECT, related_name="requests"
    )
    requested_by = models.ForeignKey(
        Profile, on_delete=models.PROTECT, related_name="approvals_requested"
    )
    approver = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approvals_assigned",
    )

    state = models.CharField(
        max_length=16, choices=APPROVAL_STATE_CHOICES, default="pending"
    )
    note = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Approval"
        verbose_name_plural = "Approvals"
        db_table = "approval"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "state"], name="approval_org_state_idx"),
            models.Index(fields=["case", "state"], name="approval_case_state_idx"),
            models.Index(fields=["approver", "state"], name="approval_approver_idx"),
        ]

    def __str__(self):
        return f"Approval(case={self.case_id}, state={self.state})"

    def is_terminal(self) -> bool:
        return self.state in ("approved", "rejected", "cancelled")

    def can_be_acted_on_by(self, profile) -> bool:
        """True when ``profile`` is in the rule's allowed approver pool."""
        if profile is None:
            return False
        if self.rule.approvers.filter(id=profile.id).exists():
            return True
        if self.rule.approver_role == "ADMIN":
            # "Admin" means `is_org_admin`, which also admits a superuser's
            # profile, not a literal read of `role`.
            return is_org_admin(profile)
        if self.rule.approver_role and profile.role == self.rule.approver_role:
            return True
        return False

    def can_be_cancelled_by(self, profile) -> bool:
        """True when ``profile`` may withdraw this request: whoever filed it,
        or an org admin. The rule `ApprovalCancelView` enforces and
        `ApprovalSerializer.can_cancel` reports, so the two cannot drift."""
        if profile is None:
            return False
        return self.requested_by_id == profile.id or is_org_admin(profile)
