"""
Approval workflow endpoints (Tier 3 approvals).

Two flavors of endpoint live here:

* Admin-configured rules (CRUD): ``GET/POST /api/cases/approval-rules/``,
  ``GET/PUT/DELETE /api/cases/approval-rules/<pk>/``.
* Per-case requests + decisions: ``POST /api/cases/<pk>/request-approval/``,
  ``GET /api/cases/approvals/`` inbox, ``POST /api/cases/approvals/<pk>/{approve,reject,cancel}/``.

Each transition writes a ``common.Activity`` row using the verbs registered in
``common/models.Activity.ACTION_CHOICES`` (``APPROVAL_REQUESTED``,
``APPROVED``, ``REJECTED``, ``APPROVAL_CANCELLED``). Two approvers cannot
double-approve the same row. Every transition takes a row lock first.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import (
    assert_case_write_access,
    get_case_or_404,
    has_case_read_access,
)
from cases.approvals import Approval, ApprovalRule, find_matching_rule
from cases.serializer import (
    ApprovalRequestSerializer,
    ApprovalRuleSerializer,
    ApprovalSerializer,
)
from common.models import Activity
from common.permissions import HasOrgContext, is_org_admin
from common.validators import uuid_param


def _visible_approvals(profile, rows):
    """The approvals ``profile`` may see, out of every approval in the org.

    ``ApprovalSerializer.case_summary`` carries a case's name, status, priority
    and account name, so an approval row discloses the case behind it. Listing
    every row in the org therefore handed any member the summary of cases that
    `cases.access` would refuse to open, which is the same read-around already
    closed on the case detail and solution-link endpoints.

    The fix cannot simply be ``visible_cases_qs``: an approver is routinely
    somebody with no stake in the case, and giving them the decision is the
    entire point of the queue. So a row is visible when any of four things is
    true, and no narrower:

    * the caller is an org admin,
    * the caller may read the underlying case,
    * the caller is in the rule's approver pool, so it is genuinely their queue,
    * the caller filed the request, so it is their own.

    Evaluated in Python rather than SQL because the read rule spans a
    many-to-many on watchers and another on approvers; the row set here is one
    org's approvals, already fetched with the needed relations prefetched.
    """
    if is_org_admin(profile):
        return list(rows)
    return [
        a
        for a in rows
        if has_case_read_access(profile, a.case)
        or a.can_be_acted_on_by(profile)
        or a.requested_by_id == profile.id
    ]


def _approval_rule_analytics(org):
    """Pending-approval count per rule + the org total, from the Approval log.

    A clean compute: every pending decision is an ``Approval`` row in state
    ``pending`` with a ``rule`` FK (the model even indexes ``(org, state)``), so
    ``pending_count`` is a straight grouped count and the org total is the sum.
    Scoped to ``org`` explicitly (RLS is inert in dev/test).
    """
    pending = (
        Approval.objects.filter(org=org, state="pending")
        .values("rule_id")
        .annotate(n=Count("id"))
    )
    by_rule = {str(row["rule_id"]): row["n"] for row in pending}
    total = sum(by_rule.values())
    return by_rule, total


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _record(case, action, metadata, actor):
    Activity.objects.create(
        user=actor,
        action=action,
        entity_type="Case",
        entity_id=case.pk,
        entity_name=str(case)[:255],
        metadata=metadata,
        org_id=case.org_id,
    )


# ---------------------------------------------------------------------------
# Rule CRUD


class ApprovalRuleListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        org = request.profile.org
        qs = ApprovalRule.objects.filter(org=org).prefetch_related(
            "approvers__user", "match_team"
        )
        rules = ApprovalRuleSerializer(qs, many=True).data
        pending_by_rule, total_pending = _approval_rule_analytics(org)
        active = 0
        for rule in rules:
            rule["pending_count"] = pending_by_rule.get(str(rule["id"]), 0)
            if rule["is_active"]:
                active += 1
        totals = {"count": len(rules), "active": active, "pending": total_pending}
        return Response(
            {"rules": rules, "totals": totals},
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        if not is_org_admin(request.profile):
            return _admin_required()
        org = request.profile.org
        serializer = ApprovalRuleSerializer(data=request.data, context={"org": org})
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Filter approver / match_team selection to the requester's org so
        # an admin in org A cannot point at a profile in org B.
        approvers = serializer.validated_data.get("approvers") or []
        for p in approvers:
            if p.org_id != org.id:
                return Response(
                    {"error": True, "errors": "Approver is outside this org."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        match_team = serializer.validated_data.get("match_team")
        if match_team is not None and match_team.org_id != org.id:
            return Response(
                {"error": True, "errors": "Team is outside this org."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rule = serializer.save(org=org)
        return Response(
            ApprovalRuleSerializer(rule).data, status=status.HTTP_201_CREATED
        )


class ApprovalRuleDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get(self, pk, org):
        return ApprovalRule.objects.filter(pk=pk, org=org).first()

    def get(self, request, pk):
        rule = self._get(pk, request.profile.org)
        if rule is None:
            return Response(
                {"error": True, "errors": "Rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ApprovalRuleSerializer(rule).data)

    def put(self, request, pk):
        if not is_org_admin(request.profile):
            return _admin_required()
        rule = self._get(pk, request.profile.org)
        if rule is None:
            return Response(
                {"error": True, "errors": "Rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = ApprovalRuleSerializer(
            rule,
            data=request.data,
            partial=True,
            context={"org": request.profile.org},
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rule = serializer.save()
        return Response(ApprovalRuleSerializer(rule).data)

    def delete(self, request, pk):
        if not is_org_admin(request.profile):
            return _admin_required()
        rule = self._get(pk, request.profile.org)
        if rule is None:
            return Response(
                {"error": True, "errors": "Rule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Hard delete is fine. Approval.rule is on_delete=PROTECT, so any
        # historical request keeps the rule alive automatically.
        if rule.requests.exists():
            # Soft-disable instead of hard-delete when there is history.
            rule.is_active = False
            rule.save(update_fields=["is_active", "updated_at"])
            return Response(
                {"id": str(rule.id), "is_active": False},
                status=status.HTTP_200_OK,
            )
        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Per-case requests + decisions


class CaseRequestApprovalView(APIView):
    """``POST /api/cases/<pk>/request-approval/``, agent fires a new request."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        # A case the caller may not open is a 404, as on the detail view.
        case = get_case_or_404(request.profile, pk)
        # Filing an approval acts on the case and returns its summary, so it
        # needs the same write access as replying on it. Org membership alone
        # let a member who cannot open a case create a row against it and read
        # the case's name, status, priority and account back in the response.
        assert_case_write_access(request.profile, case)
        body = ApprovalRequestSerializer(data=request.data)
        if not body.is_valid():
            return Response(
                {"error": True, "errors": body.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rule_id = body.validated_data.get("rule_id")
        note = body.validated_data.get("note") or ""

        if rule_id:
            rule = ApprovalRule.objects.filter(
                id=rule_id, org=org, is_active=True
            ).first()
            if rule is None:
                return Response(
                    {"error": True, "errors": "Rule not found or inactive."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not rule.matches(case):
                return Response(
                    {"error": True, "errors": "Rule does not match this case."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            rule = find_matching_rule(case, trigger_event="pre_close")
            if rule is None:
                return Response(
                    {
                        "error": True,
                        "errors": "No active approval rule matches this case.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Reject when a still-open request exists for the same case+rule.
        existing = (
            Approval.objects.select_for_update()
            .filter(case=case, rule=rule, state="pending")
            .first()
        )
        if existing is not None:
            return Response(
                {
                    "error": True,
                    "errors": "An approval is already pending for this case.",
                    "approval_id": str(existing.id),
                },
                status=status.HTTP_409_CONFLICT,
            )

        approval = Approval.objects.create(
            org=org,
            case=case,
            rule=rule,
            requested_by=request.profile,
            note=note,
        )
        _record(
            case,
            "APPROVAL_REQUESTED",
            {
                "approval_id": str(approval.id),
                "rule_id": str(rule.id),
                "rule_name": rule.name,
            },
            actor=request.profile,
        )
        return Response(
            ApprovalSerializer(approval).data, status=status.HTTP_201_CREATED
        )


class ApprovalInboxView(APIView):
    """``GET /api/cases/approvals/``, list approvals.

    Query params:
      * ``state`` (default ``pending``): filter by state, or ``all`` to drop the filter.
      * ``mine`` (``true``/``false``), when true, restrict to approvals the
        caller can act on (in rule.approvers OR matches rule.approver_role).
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request):
        org = request.profile.org
        qs = (
            Approval.objects.filter(org=org)
            .select_related(
                "case",
                "case__account",
                "rule",
                "requested_by__user",
                "approver__user",
            )
            # `rule.approvers` is now serialized on every row (the queue shows
            # who may act), so prefetch it here rather than per-call.
            # `case__assigned_to` and `case__watchers` feed the visibility
            # filter below, which asks `cases.access` about every row. Without
            # them that is two queries per approval. The rule itself stays in
            # `cases.access`, one definition, rather than being restated here
            # in a faster form that could drift from it.
            .prefetch_related(
                "rule__approvers__user",
                "case__assigned_to",
                "case__watchers",
            )
            .order_by("-created_at")
        )

        state = (request.query_params.get("state") or "pending").lower()
        if state and state != "all":
            qs = qs.filter(state=state)

        case_id = uuid_param(request.query_params, "case")
        if case_id:
            qs = qs.filter(case_id=case_id)

        mine = (request.query_params.get("mine") or "").lower() in (
            "true",
            "1",
            "yes",
        )
        rows = _visible_approvals(request.profile, qs)
        if mine:
            # "Mine" = rows I can act on right now, which, like the approve
            # endpoint, excludes requests I filed myself (separation of duties).
            profile = request.profile
            rows = [
                a
                for a in rows
                if a.can_be_acted_on_by(profile) and a.requested_by_id != profile.id
            ]

        return Response(
            {
                "approvals": ApprovalSerializer(
                    rows, many=True, context={"request": request}
                ).data
            },
            status=status.HTTP_200_OK,
        )


def _load_pending(pk, org):
    """Locked fetch for state transitions; returns None if missing/wrong-org."""
    return (
        Approval.objects.select_for_update()
        .filter(id=pk, org=org)
        .select_related("case", "rule", "requested_by")
        .first()
    )


class ApprovalApproveView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        approval = _load_pending(pk, org)
        if approval is None:
            return Response(
                {"error": True, "errors": "Approval not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if approval.state != "pending":
            return Response(
                {
                    "error": True,
                    "errors": f"Approval already {approval.state}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not approval.can_be_acted_on_by(request.profile):
            return Response(
                {"error": True, "errors": "You are not an approver for this rule."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Separation of duties: the person who filed the request cannot approve
        # it, even if they are otherwise an approver (an admin who defaults into
        # every rule's pool). Both are Profile FKs, so compare Profile ids.
        if approval.requested_by_id == request.profile.id:
            return Response(
                {
                    "error": True,
                    "errors": "You cannot approve your own request; "
                    "another approver must decide it.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        approval.state = "approved"
        approval.approver = request.profile
        approval.note = (request.data.get("note") or approval.note)[:4096]
        approval.decided_at = timezone.now()
        approval.save(
            update_fields=["state", "approver", "note", "decided_at", "updated_at"]
        )
        _record(
            approval.case,
            "APPROVED",
            {
                "approval_id": str(approval.id),
                "rule_id": str(approval.rule_id),
            },
            actor=request.profile,
        )
        return Response(ApprovalSerializer(approval).data, status=status.HTTP_200_OK)


class ApprovalRejectView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {"error": True, "errors": "Rejection reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        approval = _load_pending(pk, org)
        if approval is None:
            return Response(
                {"error": True, "errors": "Approval not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if approval.state != "pending":
            return Response(
                {
                    "error": True,
                    "errors": f"Approval already {approval.state}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not approval.can_be_acted_on_by(request.profile):
            return Response(
                {"error": True, "errors": "You are not an approver for this rule."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Separation of duties: the requester cannot reject their own request
        # either (see ApprovalApproveView). Another approver must decide it.
        if approval.requested_by_id == request.profile.id:
            return Response(
                {
                    "error": True,
                    "errors": "You cannot reject your own request; "
                    "another approver must decide it.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        approval.state = "rejected"
        approval.approver = request.profile
        approval.reason = reason[:4096]
        approval.decided_at = timezone.now()
        approval.save(
            update_fields=[
                "state",
                "approver",
                "reason",
                "decided_at",
                "updated_at",
            ]
        )
        _record(
            approval.case,
            "REJECTED",
            {
                "approval_id": str(approval.id),
                "rule_id": str(approval.rule_id),
                "reason": approval.reason[:1000],
            },
            actor=request.profile,
        )
        return Response(ApprovalSerializer(approval).data, status=status.HTTP_200_OK)


class ApprovalCancelView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @transaction.atomic
    def post(self, request, pk):
        org = request.profile.org
        approval = _load_pending(pk, org)
        if approval is None:
            return Response(
                {"error": True, "errors": "Approval not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if approval.state != "pending":
            return Response(
                {
                    "error": True,
                    "errors": f"Approval already {approval.state}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Only the requester (or an admin) can cancel.
        if approval.requested_by_id != request.profile.id and not is_org_admin(
            request.profile
        ):
            return Response(
                {"error": True, "errors": "Only the requester can cancel."},
                status=status.HTTP_403_FORBIDDEN,
            )
        approval.state = "cancelled"
        approval.decided_at = timezone.now()
        approval.save(update_fields=["state", "decided_at", "updated_at"])
        _record(
            approval.case,
            "APPROVAL_CANCELLED",
            {
                "approval_id": str(approval.id),
                "rule_id": str(approval.rule_id),
            },
            actor=request.profile,
        )
        return Response(ApprovalSerializer(approval).data, status=status.HTTP_200_OK)
