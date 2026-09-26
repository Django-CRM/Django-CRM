"""Tests for Tier 3 approval workflows.

Covers rule matching/specificity, the Case.clean() close-gate, the per-case
request flow, the inbox endpoint, and the four state transitions
(approve / reject / cancel + re-entry guards).
"""

from __future__ import annotations

import pytest
from crum import impersonate
from django.core.exceptions import ValidationError
from django.utils import timezone

from cases.approvals import Approval, ApprovalRule, find_matching_rule
from cases.models import Case
from common.models import Activity
from conftest import rls_org


def _make_case(
    org,
    creator,
    *,
    name="Sample case",
    status_value="New",
    priority="Normal",
    case_type=None,
):
    with impersonate(creator), rls_org(org):
        return Case.objects.create(
            name=name,
            status=status_value,
            priority=priority,
            case_type=case_type,
            org=org,
        )


def _make_rule(
    org,
    *,
    name="Default approval",
    is_active=True,
    match_priority=None,
    match_case_type=None,
    approver_role="ADMIN",
    approvers=None,
):
    # Seeded into another tenant: the insert-check policy compares
    # against `app.current_org`, so writing this row means being that
    # tenant for the length of the write.
    with rls_org(org):
        rule = ApprovalRule.objects.create(
            org=org,
            name=name,
            is_active=is_active,
            match_priority=match_priority,
            match_case_type=match_case_type,
            approver_role=approver_role,
        )
    if approvers:
        rule.approvers.set(approvers)
    return rule


@pytest.mark.django_db
class TestRuleMatching:
    def test_inactive_rule_does_not_match(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_a, is_active=False, match_priority="Urgent")
        assert find_matching_rule(case) is None

    def test_priority_filter(self, admin_user, org_a):
        urgent = _make_case(org_a, admin_user, name="U", priority="Urgent")
        normal = _make_case(org_a, admin_user, name="N", priority="Normal")
        rule = _make_rule(org_a, match_priority="Urgent")
        assert find_matching_rule(urgent) == rule
        assert find_matching_rule(normal) is None

    def test_specificity_tiebreak(self, admin_user, org_a):
        case = _make_case(
            org_a,
            admin_user,
            priority="Urgent",
            case_type="Incident",
        )
        broad = _make_rule(org_a, name="broad", match_priority="Urgent")
        specific = _make_rule(
            org_a,
            name="specific",
            match_priority="Urgent",
            match_case_type="Incident",
        )
        chosen = find_matching_rule(case)
        assert chosen == specific, (
            f"More-specific rule should win, got: {chosen.name if chosen else None}"
        )
        # Unused but covers the broader rule still being a candidate.
        assert broad.matches(case) is True

    def test_cross_org_does_not_match(self, admin_user, org_a, org_b):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_b, match_priority="Urgent")
        assert find_matching_rule(case) is None


@pytest.mark.django_db
class TestCloseGate:
    def test_close_blocked_when_rule_matches(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_a, match_priority="Urgent")
        case.status = "Closed"
        case.closed_on = timezone.localdate()
        with pytest.raises(ValidationError) as exc:
            case.clean()
        assert "approval" in str(exc.value).lower()

    def test_close_allowed_with_approved_request(
        self, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="approved",
        )
        case.status = "Closed"
        case.closed_on = timezone.localdate()
        case.clean()  # should not raise

    def test_close_unaffected_when_no_rule(self, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        case.status = "Closed"
        case.closed_on = timezone.localdate()
        case.clean()  # should not raise

    def test_pending_approval_does_not_satisfy_gate(
        self, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="pending",
        )
        case.status = "Closed"
        case.closed_on = timezone.localdate()
        with pytest.raises(ValidationError):
            case.clean()


@pytest.mark.django_db
class TestRequestApprovalEndpoint:
    def test_auto_resolves_rule(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_a, match_priority="Urgent")
        res = admin_client.post(
            f"/api/cases/{case.id}/request-approval/",
            data={"note": "please review"},
            format="json",
        )
        assert res.status_code == 201, res.content
        assert res.data["state"] == "pending"
        assert res.data["case_summary"]["id"] == str(case.id)

        activity = Activity.objects.filter(
            entity_id=case.id, action="APPROVAL_REQUESTED"
        ).first()
        assert activity is not None
        assert activity.metadata["approval_id"] == res.data["id"]

    def test_rejects_when_no_rule_matches(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Normal")
        res = admin_client.post(
            f"/api/cases/{case.id}/request-approval/", data={}, format="json"
        )
        assert res.status_code == 400
        assert "no active" in str(res.data).lower()

    def test_double_request_returns_409(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="pending",
        )
        res = admin_client.post(
            f"/api/cases/{case.id}/request-approval/", data={}, format="json"
        )
        assert res.status_code == 409
        assert "approval_id" in res.data


@pytest.mark.django_db
class TestStateTransitions:
    def _setup(self, org, requester_profile, creator_user, *, approvers=None):
        case = _make_case(org, creator_user, priority="Urgent")
        rule = _make_rule(org, match_priority="Urgent", approvers=approvers)
        approval = Approval.objects.create(
            org=org,
            case=case,
            rule=rule,
            requested_by=requester_profile,
            state="pending",
        )
        return case, rule, approval

    def test_approve_records_activity(
        self, admin_client, admin_profile, user_profile, regular_user, org_a
    ):
        # Requested by the USER, approved by the admin. A requester and an
        # approver who are different people, which the rule now requires.
        case, rule, approval = self._setup(org_a, user_profile, regular_user)
        res = admin_client.post(
            f"/api/cases/approvals/{approval.id}/approve/",
            data={"note": "ok"},
            format="json",
        )
        assert res.status_code == 200
        assert res.data["state"] == "approved"
        approval.refresh_from_db()
        assert approval.approver_id == admin_profile.id
        assert approval.decided_at is not None
        assert Activity.objects.filter(entity_id=case.id, action="APPROVED").exists()

    def test_reject_requires_reason(
        self, admin_client, admin_profile, user_profile, regular_user, org_a
    ):
        case, rule, approval = self._setup(org_a, user_profile, regular_user)
        bare = admin_client.post(
            f"/api/cases/approvals/{approval.id}/reject/", data={}, format="json"
        )
        assert bare.status_code == 400

        ok = admin_client.post(
            f"/api/cases/approvals/{approval.id}/reject/",
            data={"reason": "needs more triage"},
            format="json",
        )
        assert ok.status_code == 200
        approval.refresh_from_db()
        assert approval.state == "rejected"
        assert approval.reason == "needs more triage"
        assert Activity.objects.filter(entity_id=case.id, action="REJECTED").exists()

    def test_cannot_approve_own_request(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        # The admin files the request AND is in the ADMIN approver pool, but
        # separation of duties forbids approving your own request (the bug this
        # guard closes: an admin used to be able to rubber-stamp their own).
        _, _, approval = self._setup(org_a, admin_profile, admin_user)
        res = admin_client.post(
            f"/api/cases/approvals/{approval.id}/approve/", data={}, format="json"
        )
        assert res.status_code == 403
        assert "your own" in str(res.data).lower()
        approval.refresh_from_db()
        assert approval.state == "pending"  # untouched

    def test_cannot_reject_own_request(
        self, admin_client, admin_profile, admin_user, org_a
    ):
        _, _, approval = self._setup(org_a, admin_profile, admin_user)
        res = admin_client.post(
            f"/api/cases/approvals/{approval.id}/reject/",
            data={"reason": "on second thought"},
            format="json",
        )
        assert res.status_code == 403
        approval.refresh_from_db()
        assert approval.state == "pending"

    def test_cancel_requires_requester_or_admin(
        self,
        admin_client,
        admin_user,
        admin_profile,
        user_client,
        regular_user,
        user_profile,
        org_a,
    ):
        # Approval requested by `user_profile`, attempted cancel by ADMIN succeeds.
        case = _make_case(org_a, regular_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        approval = Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=user_profile,
            state="pending",
        )

        # Admin cancels (allowed).
        res = admin_client.post(
            f"/api/cases/approvals/{approval.id}/cancel/", data={}, format="json"
        )
        assert res.status_code == 200, res.content
        approval.refresh_from_db()
        assert approval.state == "cancelled"
        assert Activity.objects.filter(
            entity_id=case.id, action="APPROVAL_CANCELLED"
        ).exists()

    def test_user_cannot_cancel_others_request(
        self,
        admin_client,
        admin_user,
        admin_profile,
        user_client,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        approval = Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,  # admin requested it
            state="pending",
        )
        # The member can open the case, so they can see the approval; one they
        # cannot see answers 404 (`test_approval_authz.py`). This is the cancel
        # rule itself: not the requester, not an admin.
        case.assigned_to.add(user_profile)
        res = user_client.post(
            f"/api/cases/approvals/{approval.id}/cancel/", data={}, format="json"
        )
        assert res.status_code == 403

    def test_non_approver_cannot_approve(
        self,
        user_client,
        regular_user,
        user_profile,
        admin_user,
        admin_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        # Rule restricts to ADMIN role only.
        rule = _make_rule(org_a, match_priority="Urgent", approver_role="ADMIN")
        approval = Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="pending",
        )
        # Visible through the case, so this reaches the approver-pool check.
        case.assigned_to.add(user_profile)
        res = user_client.post(
            f"/api/cases/approvals/{approval.id}/approve/", data={}, format="json"
        )
        assert res.status_code == 403

    def test_double_approve_rejected(
        self, admin_client, admin_profile, user_profile, regular_user, org_a
    ):
        _, _, approval = self._setup(org_a, user_profile, regular_user)
        a = admin_client.post(
            f"/api/cases/approvals/{approval.id}/approve/",
            data={},
            format="json",
        )
        assert a.status_code == 200
        b = admin_client.post(
            f"/api/cases/approvals/{approval.id}/approve/",
            data={},
            format="json",
        )
        assert b.status_code == 400
        assert "already" in str(b.data).lower()


@pytest.mark.django_db
class TestInbox:
    def test_mine_filters_to_actionable_rows(
        self,
        admin_client,
        admin_user,
        admin_profile,
        user_client,
        regular_user,
        user_profile,
        org_a,
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule_admin_only = _make_rule(
            org_a,
            name="admins-only",
            match_priority="Urgent",
            approver_role="ADMIN",
        )
        rule_user_explicit = _make_rule(
            org_a,
            name="explicit-user",
            match_case_type="Incident",
            # Use MANAGER so admin's role match doesn't sweep this row in too.
            approver_role="MANAGER",
            approvers=[user_profile],
        )

        # Both approvals exist. The admin-only one is requested by the USER so
        # the admin can actually act on it (a requester can't act on their own).
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule_admin_only,
            requested_by=user_profile,
            state="pending",
        )
        case2 = _make_case(
            org_a, admin_user, name="C2", priority="Low", case_type="Incident"
        )
        Approval.objects.create(
            org=org_a,
            case=case2,
            rule=rule_user_explicit,
            requested_by=admin_profile,
            state="pending",
        )

        # Admin sees both via mine=true (admin role + admin-only rule).
        admin_view = admin_client.get("/api/cases/approvals/?mine=true&state=pending")
        assert admin_view.status_code == 200
        admin_ids = {a["id"] for a in admin_view.data["approvals"]}
        # Admin matches the ADMIN-role rule but isn't in the explicit-user pool.
        assert len(admin_ids) == 1

        # Regular user sees only the explicit pool entry.
        user_view = user_client.get("/api/cases/approvals/?mine=true&state=pending")
        assert user_view.status_code == 200
        user_ids = {a["id"] for a in user_view.data["approvals"]}
        assert len(user_ids) == 1

    def test_can_act_and_is_own_request_flags(
        self, admin_client, admin_profile, user_profile, regular_user, admin_user, org_a
    ):
        """The per-row flags the queue's button guard reads must match the
        approve endpoint: can_act only when actionable-and-not-yours."""
        rule = _make_rule(org_a, match_priority="Urgent", approver_role="ADMIN")
        own = Approval.objects.create(
            org=org_a,
            case=_make_case(org_a, admin_user, priority="Urgent"),
            rule=rule,
            requested_by=admin_profile,  # the admin's own request
            state="pending",
        )
        others = Approval.objects.create(
            org=org_a,
            case=_make_case(org_a, regular_user, name="C2", priority="Urgent"),
            rule=rule,
            requested_by=user_profile,  # someone else's request
            state="pending",
        )
        rows = {
            a["id"]: a
            for a in admin_client.get("/api/cases/approvals/?state=pending").data[
                "approvals"
            ]
        }
        # The admin's own request: flagged as theirs, and not actionable.
        assert rows[str(own.id)]["is_own_request"] is True
        assert rows[str(own.id)]["can_act"] is False
        # Someone else's request the admin may decide: actionable, not theirs.
        assert rows[str(others.id)]["is_own_request"] is False
        assert rows[str(others.id)]["can_act"] is True


@pytest.mark.django_db
class TestRuleCRUD:
    def test_admin_creates_rule(self, admin_client, org_a):
        res = admin_client.post(
            "/api/cases/approval-rules/",
            data={
                "name": "Urgent close approval",
                "match_priority": "Urgent",
                "approver_role": "ADMIN",
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        assert ApprovalRule.objects.filter(name="Urgent close approval").exists()

    def test_non_admin_cannot_create(self, user_client):
        res = user_client.post(
            "/api/cases/approval-rules/",
            data={"name": "x", "match_priority": "Urgent"},
            format="json",
        )
        assert res.status_code == 403

    def test_put_rejects_cross_org_approver(
        self, admin_client, org_a, org_b, profile_b
    ):
        """PUT must not attach another org's Profile as an approver. POST
        validated this manually; PUT relied on serializer org-scoping that
        wasn't there, so a foreign-tenant Profile could be pinned to the rule."""
        rule = _make_rule(org_a, match_priority="Urgent")
        res = admin_client.put(
            f"/api/cases/approval-rules/{rule.id}/",
            data={"approver_ids": [str(profile_b.id)]},
            format="json",
        )
        assert res.status_code == 400, res.content
        rule.refresh_from_db()
        assert rule.approvers.count() == 0

    def test_put_rejects_cross_org_team(self, admin_client, org_a, org_b):
        """Same hole on the match_team FK."""
        from common.models import Teams

        # Seeded into another tenant: the insert-check policy compares
        # against `app.current_org`, so writing this row means being that
        # tenant for the length of the write.
        with rls_org(org_b):
            team_b = Teams.objects.create(org=org_b, name="Foreign team")
        rule = _make_rule(org_a, match_priority="Urgent")
        res = admin_client.put(
            f"/api/cases/approval-rules/{rule.id}/",
            data={"match_team_id": str(team_b.id)},
            format="json",
        )
        assert res.status_code == 400, res.content
        rule.refresh_from_db()
        assert rule.match_team_id is None

    def test_put_accepts_same_org_approver(self, admin_client, org_a, user_profile):
        """The allowed side, a same-org approver PUTs fine, proving the two
        rejections above are org-scoping and not a blanket block on the field."""
        rule = _make_rule(org_a, match_priority="Urgent")
        res = admin_client.put(
            f"/api/cases/approval-rules/{rule.id}/",
            data={"approver_ids": [str(user_profile.id)]},
            format="json",
        )
        assert res.status_code == 200, res.content
        rule.refresh_from_db()
        assert user_profile in rule.approvers.all()

    def test_delete_with_history_soft_disables(
        self, admin_client, admin_user, admin_profile, org_a
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, match_priority="Urgent")
        Approval.objects.create(
            org=org_a,
            case=case,
            rule=rule,
            requested_by=admin_profile,
            state="approved",
        )
        res = admin_client.delete(f"/api/cases/approval-rules/{rule.id}/")
        # PROTECT FK + history present → endpoint soft-disables instead of 204.
        assert res.status_code == 200
        rule.refresh_from_db()
        assert rule.is_active is False


# ---------------------------------------------------------------------------
# Rule-list analytics: pending_count per rule + org totals, read from the
# Approval log (state="pending").
# ---------------------------------------------------------------------------

RULES_URL = "/api/cases/approval-rules/"


@pytest.mark.django_db
class TestApprovalRuleAnalytics:
    def _list(self, client):
        resp = client.get(RULES_URL)
        assert resp.status_code == 200
        return resp.json()

    def _row(self, body, name):
        return next((r for r in body["rules"] if r["name"] == name), None)

    def _pending(self, org, rule, requester, n):
        for _ in range(n):
            case = _make_case(org, requester.user)
            Approval.objects.create(
                org=org, case=case, rule=rule, requested_by=requester, state="pending"
            )

    def test_pending_count_and_total(self, admin_client, admin_profile, org_a):
        a = _make_rule(org_a, name="Rule A")
        b = _make_rule(org_a, name="Rule B")
        self._pending(org_a, a, admin_profile, 2)
        self._pending(org_a, b, admin_profile, 1)
        body = self._list(admin_client)
        assert self._row(body, "Rule A")["pending_count"] == 2
        assert self._row(body, "Rule B")["pending_count"] == 1
        assert body["totals"]["pending"] == 3

    def test_only_pending_state_counts(self, admin_client, admin_profile, org_a):
        rule = _make_rule(org_a, name="Rule A")
        self._pending(org_a, rule, admin_profile, 1)
        for terminal in ("approved", "rejected", "cancelled"):
            case = _make_case(org_a, admin_profile.user)
            Approval.objects.create(
                org=org_a,
                case=case,
                rule=rule,
                requested_by=admin_profile,
                state=terminal,
            )
        body = self._list(admin_client)
        assert self._row(body, "Rule A")["pending_count"] == 1
        assert body["totals"]["pending"] == 1

    def test_totals_count_and_active(self, admin_client, admin_profile, org_a):
        _make_rule(org_a, name="Live 1", is_active=True)
        _make_rule(org_a, name="Live 2", is_active=True)
        _make_rule(org_a, name="Off", is_active=False)
        totals = self._list(admin_client)["totals"]
        assert totals["count"] == 3
        assert totals["active"] == 2

    def test_cross_org_pending_isolated(
        self, admin_client, admin_profile, org_a, org_b, profile_b
    ):
        rule_a = _make_rule(org_a, name="Rule A")
        rule_b = _make_rule(org_b, name="Rule B")
        self._pending(org_a, rule_a, admin_profile, 1)
        self._pending(org_b, rule_b, profile_b, 2)
        body = self._list(admin_client)  # org_a admin
        assert self._row(body, "Rule A")["pending_count"] == 1
        assert self._row(body, "Rule B") is None  # org_b rule invisible
        assert body["totals"]["pending"] == 1  # only org_a's pending


@pytest.mark.django_db
class TestDetailNamesTheGatingRule:
    """`approval_rule` on the case detail: the rule a request would bind to.

    Clients offer "Request approval" only when it is set, so it must be set
    exactly when `request-approval/` with no `rule_id` can find a rule.
    """

    def _rule_on_detail(self, client, case):
        res = client.get(f"/api/cases/{case.id}/")
        assert res.status_code == 200, res.content
        return res.data["approval_rule"]

    def test_a_matching_rule_is_named(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        rule = _make_rule(org_a, name="Urgent close", match_priority="Urgent")
        assert self._rule_on_detail(admin_client, case) == {
            "id": str(rule.id),
            "name": "Urgent close",
        }

    def test_no_matching_rule_is_null(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Normal")
        _make_rule(org_a, match_priority="Urgent")
        assert self._rule_on_detail(admin_client, case) is None

    def test_an_inactive_rule_is_null(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_a, match_priority="Urgent", is_active=False)
        assert self._rule_on_detail(admin_client, case) is None

    def test_another_orgs_rule_is_never_named(
        self, admin_client, admin_user, org_a, org_b
    ):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_b, match_priority="Urgent")
        assert self._rule_on_detail(admin_client, case) is None

    def test_it_agrees_with_the_request_endpoint(self, admin_client, admin_user, org_a):
        case = _make_case(org_a, admin_user, priority="Urgent")
        _make_rule(org_a, name="Older", match_priority="Urgent")
        specific = _make_rule(
            org_a, name="Specific", match_priority="Urgent", match_case_type="Problem"
        )
        case.case_type = "Problem"
        case.save(update_fields=["case_type"])
        named = self._rule_on_detail(admin_client, case)
        res = admin_client.post(
            f"/api/cases/{case.id}/request-approval/", data={}, format="json"
        )
        assert res.status_code == 201, res.content
        assert named["id"] == res.data["rule_summary"]["id"] == str(specific.id)
