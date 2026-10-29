"""A queryset update does not run `save()`, so each core call site on the main
record kinds stamps `updated_at` itself; a change feed reads that time."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import Account
from cases.models import Case
from contacts.models import Contact
from interactions.models import Interaction
from interactions.service import refresh_lead_response
from leads.models import Lead
from leads.services import _move_interactions

LONG_AGO = timezone.now() - timedelta(days=30)


def _age(obj):
    type(obj).objects.filter(pk=obj.pk).update(updated_at=LONG_AGO)


def _bulk_delete_case(admin_client, org_a, admin_profile):
    case = Case.objects.create(
        name="Old",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_profile.user,
    )
    _age(case)
    resp = admin_client.post(
        "/api/cases/bulk/delete/", {"ids": [str(case.id)]}, format="json"
    )
    assert resp.status_code == 200
    return case


def _lead_response(admin_client, org_a, admin_profile):
    lead = Lead.objects.create(org=org_a, first_name="Resp")
    Interaction.objects.create(
        org=org_a,
        home="lead",
        lead=lead,
        kind="call",
        logged_by=admin_profile,
        occurred_at=timezone.now() + timedelta(minutes=1),
    )
    _age(lead)
    assert refresh_lead_response(lead)
    return lead


def _lead_interaction_moves(admin_client, org_a, admin_profile):
    lead = Lead.objects.create(org=org_a, first_name="Move")
    account = Account.objects.create(org=org_a, name="Acme")
    interaction = Interaction.objects.create(
        org=org_a,
        home="lead",
        lead=lead,
        kind="note",
        logged_by=admin_profile,
        occurred_at=timezone.now(),
    )
    _age(interaction)
    _move_interactions(lead, None, account, None)
    return interaction


def _merge_repoints_interactions(admin_client, org_a, admin_profile):
    keeper = Contact.objects.create(org=org_a, first_name="Keep", last_name="Er")
    loser = Contact.objects.create(org=org_a, first_name="Lose", last_name="Er")
    interaction = Interaction.objects.create(
        org=org_a,
        home="contact",
        contact=loser,
        kind="note",
        logged_by=admin_profile,
        occurred_at=timezone.now(),
    )
    _age(interaction)
    resp = admin_client.post(
        f"/api/contacts/{keeper.id}/merge/",
        {"merge_id": str(loser.id)},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    return interaction


@pytest.mark.django_db
@pytest.mark.parametrize(
    "trigger",
    [
        _bulk_delete_case,
        _lead_response,
        _lead_interaction_moves,
        _merge_repoints_interactions,
    ],
)
def test_queryset_update_moves_updated_at(trigger, admin_client, org_a, admin_profile):
    obj = trigger(admin_client, org_a, admin_profile)
    obj.refresh_from_db()
    assert obj.updated_at > LONG_AGO + timedelta(days=1)
