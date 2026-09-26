"""The account page lists only related records the viewer may open.

A non-admin who could open an account (its creator or an assignee) used to
receive every deal, ticket, task and invoice on it, and every open lead in the
whole org, including records the detail endpoints of those modules refuse
them. Each list now applies its own module's read rule. The nested
``account_obj.opportunities`` / ``cases`` / ``tasks`` lists, which the mobile
detail screen reads, follow the same rules.

``leads`` and ``emails`` are gone from the response: neither client read them.
"""

import pytest
from rest_framework import status

from accounts.models import Account
from cases.models import Case
from invoices.models import Invoice
from leads.models import Lead
from opportunity.models import Opportunity
from tasks.models import Task

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(org_a, admin_user, user_profile):
    """An account the non-admin is assigned to, with one record of each kind
    they may open and one they may not."""
    account = Account.objects.create(name="Analytical Engines", org=org_a)
    Account.objects.filter(pk=account.pk).update(created_by=admin_user)
    account.assigned_to.add(user_profile)

    def related(model, visible, **fields):
        obj = model.objects.create(org=org_a, account=account, **fields)
        model.objects.filter(pk=obj.pk).update(created_by=admin_user)
        if visible:
            obj.assigned_to.add(user_profile)
        return obj

    for visible, label in ((True, "Mine"), (False, "Hidden")):
        related(Opportunity, visible, name=f"{label} deal", amount="10")
        related(Case, visible, name=f"{label} ticket", status="New")
        related(Task, visible, title=f"{label} task", status="New", priority="Low")
        related(
            Invoice,
            visible,
            invoice_title=f"{label} invoice",
            invoice_number=f"{label}-1",
        )

    # The creator half of each rule, not only the assignee half.
    regular_user = user_profile.user
    created = Opportunity.objects.create(
        org=org_a, account=account, name="Created deal", amount="1"
    )
    Opportunity.objects.filter(pk=created.pk).update(created_by=regular_user)

    lead = Lead.objects.create(
        first_name="Stranger", last_name="Lead", org=org_a, status="assigned"
    )
    Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
    return account


def _names(data):
    obj = data["account_obj"]
    return {
        "deals": {d["name"] for d in data["opportunity_list"]},
        "tickets": {c["name"] for c in data["cases"]},
        "tasks": {t["title"] for t in data["tasks"]},
        "invoices": {i["invoice_title"] for i in data["invoices"]},
        "nested_deals": {d["name"] for d in obj["opportunities"]},
        "nested_tickets": {c["name"] for c in obj["cases"]},
        "nested_tasks": {t["title"] for t in obj["tasks"]},
    }


def test_non_admin_sees_only_related_records_they_may_open(user_client, account):
    response = user_client.get(f"/api/accounts/{account.pk}/")
    assert response.status_code == status.HTTP_200_OK
    assert _names(response.data) == {
        "deals": {"Mine deal", "Created deal"},
        "tickets": {"Mine ticket"},
        "tasks": {"Mine task"},
        "invoices": {"Mine invoice"},
        "nested_deals": {"Mine deal", "Created deal"},
        "nested_tickets": {"Mine ticket"},
        "nested_tasks": {"Mine task"},
    }


def test_admin_sees_every_related_record(admin_client, account):
    response = admin_client.get(f"/api/accounts/{account.pk}/")
    assert response.status_code == status.HTTP_200_OK
    every_deal = {"Mine deal", "Hidden deal", "Created deal"}
    assert _names(response.data) == {
        "deals": every_deal,
        "tickets": {"Mine ticket", "Hidden ticket"},
        "tasks": {"Mine task", "Hidden task"},
        "invoices": {"Mine invoice", "Hidden invoice"},
        "nested_deals": every_deal,
        "nested_tickets": {"Mine ticket", "Hidden ticket"},
        "nested_tasks": {"Mine task", "Hidden task"},
    }


def test_hidden_records_are_refused_by_their_own_endpoints(user_client, account):
    """The two doors must agree: what the account page withholds, the
    module's own detail route refuses too."""
    hidden_deal = Opportunity.objects.get(name="Hidden deal")
    hidden_task = Task.objects.get(title="Hidden task")
    assert user_client.get(f"/api/opportunities/{hidden_deal.pk}/").status_code == 403
    assert user_client.get(f"/api/tasks/{hidden_task.pk}/").status_code == 403


def test_org_wide_leads_and_unread_emails_are_not_served(user_client, account):
    response = user_client.get(f"/api/accounts/{account.pk}/")
    assert response.status_code == status.HTTP_200_OK
    assert "leads" not in response.data
    assert "emails" not in response.data


def test_comment_response_narrows_the_nested_lists_too(user_client, account):
    """`post` answers with the same `account_obj`, so it follows the same rules."""
    response = user_client.post(
        f"/api/accounts/{account.pk}/", {"comment": "Called them"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    obj = response.data["account_obj"]
    assert {d["name"] for d in obj["opportunities"]} == {"Mine deal", "Created deal"}
    assert {c["name"] for c in obj["cases"]} == {"Mine ticket"}
    assert {t["title"] for t in obj["tasks"]} == {"Mine task"}
