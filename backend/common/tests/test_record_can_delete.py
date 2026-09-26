"""`can_delete` on the lead, contact, account and deal detail responses, and
`can_edit` / `can_delete` on a knowledge-base article.

The clients show Delete only when this is true. It is the module's own delete
rule (`may_delete_lead` and friends), asked for the caller: an admin or the
record's creator, never an assignee who may only open and edit it. The DELETE
endpoint asks the same rule, so the two cannot disagree.
"""

import pytest

from accounts.models import Account
from common.models import Profile, User
from contacts.models import Contact
from leads.models import Lead


@pytest.fixture
def other_user(org_a):
    user = User.objects.create_user(email="other@test.com", password="x")
    Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    return user


def _records(org, creator, assignee):
    lead = Lead.objects.create(org=org, first_name="L", created_by=creator)
    contact = Contact.objects.create(
        org=org, first_name="C", last_name="D", created_by=creator
    )
    account = Account.objects.create(org=org, name="Acme", created_by=creator)
    for record in (lead, contact, account):
        record.assigned_to.add(assignee)
    return {"leads": lead, "contacts": contact, "accounts": account}


@pytest.mark.parametrize("module", ["leads", "contacts", "accounts"])
def test_an_assignee_may_open_but_is_not_offered_delete(
    user_client, org_a, other_user, user_profile, module
):
    record = _records(org_a, other_user, user_profile)[module]
    response = user_client.get(f"/api/{module}/{record.id}/")
    assert response.status_code == 200
    assert response.json()["can_delete"] is False
    # And the DELETE agrees.
    assert user_client.delete(f"/api/{module}/{record.id}/").status_code == 403


@pytest.mark.parametrize("module", ["leads", "contacts", "accounts"])
def test_the_creator_is_offered_delete(
    user_client, org_a, regular_user, user_profile, module
):
    record = _records(org_a, regular_user, user_profile)[module]
    assert user_client.get(f"/api/{module}/{record.id}/").json()["can_delete"] is True
    assert user_client.delete(f"/api/{module}/{record.id}/").status_code == 200


@pytest.mark.parametrize("module", ["leads", "contacts", "accounts"])
def test_an_admin_is_offered_delete(
    admin_client, org_a, other_user, admin_profile, module
):
    record = _records(org_a, other_user, admin_profile)[module]
    assert admin_client.get(f"/api/{module}/{record.id}/").json()["can_delete"] is True


# Deals and knowledge-base articles carry the same facts, so the phone gates
# on the server's answer there too.


def _deal(org, creator, assignee):
    from opportunity.models import Opportunity

    deal = Opportunity.objects.create(org=org, name="Deal", created_by=creator)
    deal.assigned_to.add(assignee)
    return deal


def test_deal_can_delete_follows_the_delete_rule(
    user_client, admin_client, org_a, other_user, regular_user, user_profile
):
    theirs = _deal(org_a, other_user, user_profile)
    mine = _deal(org_a, regular_user, user_profile)
    url = "/api/opportunities/{}/"
    assert user_client.get(url.format(theirs.id)).json()["can_delete"] is False
    assert user_client.delete(url.format(theirs.id)).status_code == 403
    assert user_client.get(url.format(mine.id)).json()["can_delete"] is True
    assert admin_client.get(url.format(theirs.id)).json()["can_delete"] is True


def _article(org, creator):
    from cases.models import Solution

    return Solution.objects.create(
        org=org, title="Reset", description="Steps", created_by=creator
    )


def test_solution_can_edit_and_can_delete_follow_the_write_rule(
    user_client, admin_client, org_a, other_user, regular_user
):
    theirs = _article(org_a, other_user)
    mine = _article(org_a, regular_user)
    url = "/api/cases/solutions/{}/"

    body = user_client.get(url.format(theirs.id)).json()
    assert (body["can_edit"], body["can_delete"]) == (False, False)
    assert (
        user_client.patch(
            url.format(theirs.id), {"title": "x"}, format="json"
        ).status_code
        == 403
    )
    assert user_client.delete(url.format(theirs.id)).status_code == 403

    body = user_client.get(url.format(mine.id)).json()
    assert (body["can_edit"], body["can_delete"]) == (True, True)

    body = admin_client.get(url.format(theirs.id)).json()
    assert (body["can_edit"], body["can_delete"]) == (True, True)


def test_solution_facts_fail_closed_without_a_requester(org_a, other_user):
    from cases.solution_serializers import SolutionDetailSerializer

    data = SolutionDetailSerializer(_article(org_a, other_user)).data
    assert (data["can_edit"], data["can_delete"]) == (False, False)
