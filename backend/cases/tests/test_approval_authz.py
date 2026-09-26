"""Approvals disclose the case behind them, so they need the case's read rule.

``ApprovalSerializer.case_summary`` carries a case's name, status, priority and
account name. ``ApprovalInboxView`` listed every approval in the org with no
visibility filter at all, so any member could read that summary for cases
``cases.access`` would refuse to open, and ``?state=all`` returned the lot.
``CaseRequestApprovalView`` had the same gap on a single case: org membership
was the only check, so a member could file a request against a case they cannot
see and read the summary back out of the 201.

The rule cannot simply be ``visible_cases_qs``. An approver is routinely
somebody with no stake in the case, and handing them the decision is the whole
point of the queue. So a row is visible when the caller is an admin, or may
read the case, or is in the rule's approver pool, or filed the request.

Every one of those four is asserted here, along with the member who satisfies
none of them. A filter that hides rows from their own approver is an outage,
and one that shows them to everybody is the bug.
"""

from __future__ import annotations

import pytest
from crum import impersonate
from rest_framework import status

from cases.approvals import Approval, ApprovalRule
from cases.models import Case, CaseWatcher
from common.models import Profile

INBOX = "/api/cases/approvals/"


def _case(org, creator, **kwargs):
    defaults = {"name": "Someone else's case", "status": "New", "priority": "Normal"}
    defaults.update(kwargs)
    with impersonate(creator):
        return Case.objects.create(org=org, **defaults)


def _rule(org, *, approvers=None, approver_role="", name="Closure approval"):
    rule = ApprovalRule.objects.create(
        org=org, name=name, is_active=True, approver_role=approver_role
    )
    if approvers:
        rule.approvers.set(approvers)
    return rule


def _approval(org, case, rule, requested_by):
    return Approval.objects.create(
        org=org, case=case, rule=rule, requested_by=requested_by
    )


@pytest.fixture
def outsider_profile(org_a, django_user_model):
    """A member of org_a with no connection to the case under test."""
    user = django_user_model.objects.create_user(
        email="outsider@example.com", password="x"
    )
    return Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)


@pytest.fixture
def outsider_client(outsider_profile, org_a):
    from conftest import _make_authenticated_client

    return _make_authenticated_client(outsider_profile.user, org_a, outsider_profile)


@pytest.fixture
def hidden_case(org_a, admin_user):
    """A case the outsider did not create, is not assigned to, is not watching."""
    return _case(org_a, admin_user, name="Confidential escalation")


@pytest.mark.django_db
class TestInboxHidesUnrelatedApprovals:
    def test_an_unrelated_member_sees_nothing(
        self, outsider_client, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        response = outsider_client.get(INBOX)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["approvals"] == [], (
            "a member with no relation to the case read its name, status, "
            "priority and account out of the approval queue"
        )

    def test_state_all_does_not_widen_it(
        self, outsider_client, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        response = outsider_client.get(f"{INBOX}?state=all")
        assert response.data["approvals"] == []

    def test_the_case_name_is_not_in_the_body(
        self, outsider_client, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        body = outsider_client.get(f"{INBOX}?state=all").content.decode()
        assert "Confidential escalation" not in body


@pytest.mark.django_db
class TestInboxStillShowsTheFourWhoMaySee:
    """The True directions. Each of these would be an outage if filtered out."""

    def test_an_eligible_approver_sees_the_row(
        self, outsider_client, outsider_profile, org_a, admin_profile, hidden_case
    ):
        """Named on the rule, no relation to the case. This is the queue's job."""
        rule = _rule(org_a, approvers=[outsider_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        response = outsider_client.get(INBOX)
        assert len(response.data["approvals"]) == 1

    def test_an_approver_by_role_sees_the_row(
        self, outsider_client, outsider_profile, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approver_role="USER")
        _approval(org_a, hidden_case, rule, admin_profile)

        response = outsider_client.get(INBOX)
        assert len(response.data["approvals"]) == 1

    def test_the_requester_sees_their_own_request(
        self, outsider_client, outsider_profile, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, outsider_profile)

        response = outsider_client.get(INBOX)
        assert len(response.data["approvals"]) == 1

    def test_someone_who_can_read_the_case_sees_it(
        self, outsider_client, outsider_profile, org_a, admin_profile, hidden_case
    ):
        """Watching a case is one of the three read routes in cases.access."""
        CaseWatcher.objects.create(
            case=hidden_case, profile=outsider_profile, org=org_a
        )
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        response = outsider_client.get(INBOX)
        assert len(response.data["approvals"]) == 1

    def test_an_admin_still_sees_everything(
        self, admin_client, org_a, admin_profile, hidden_case
    ):
        rule = _rule(org_a, approvers=[admin_profile])
        _approval(org_a, hidden_case, rule, admin_profile)

        response = admin_client.get(INBOX)
        assert len(response.data["approvals"]) == 1


@pytest.mark.django_db
class TestRequestingApprovalNeedsCaseAccess:
    def test_an_unrelated_member_is_refused(self, outsider_client, org_a, hidden_case):
        """404, as for a missing case: the member cannot open this one."""
        _rule(org_a, approver_role="ADMIN")
        response = outsider_client.post(
            f"/api/cases/{hidden_case.id}/request-approval/", {}, format="json"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not Approval.objects.filter(case=hidden_case).exists()

    def test_the_response_does_not_leak_the_case_name(
        self, outsider_client, org_a, hidden_case
    ):
        _rule(org_a, approver_role="ADMIN")
        response = outsider_client.post(
            f"/api/cases/{hidden_case.id}/request-approval/", {}, format="json"
        )
        assert "Confidential escalation" not in response.content.decode()

    def test_an_assignee_can_still_request(
        self, outsider_client, outsider_profile, org_a, hidden_case
    ):
        """The True direction: the agent working the case files the request."""
        hidden_case.assigned_to.add(outsider_profile)
        _rule(org_a, approver_role="ADMIN")
        response = outsider_client.post(
            f"/api/cases/{hidden_case.id}/request-approval/", {}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Approval.objects.filter(case=hidden_case).count() == 1

    def test_an_admin_can_still_request(self, admin_client, org_a, hidden_case):
        _rule(org_a, approver_role="ADMIN")
        response = admin_client.post(
            f"/api/cases/{hidden_case.id}/request-approval/", {}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
