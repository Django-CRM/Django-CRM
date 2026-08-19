"""Transactional and provenance guarantees for lead conversion."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection

from accounts.models import Account
from contacts.models import Contact
from leads.models import Lead, LeadConversion
from leads.services import convert_lead_to_account
from opportunity.models import Opportunity

pytestmark = pytest.mark.django_db(transaction=True)


def _lead(org, suffix="one"):
    return Lead.objects.create(
        org=org,
        first_name="Synthetic",
        last_name="Buyer",
        email=f"{suffix}@example.invalid",
        company_name=f"Synthetic {suffix}",
        opportunity_amount="1250.00",
        status="in process",
    )


def _request(profile):
    return SimpleNamespace(profile=profile)


def test_conversion_persists_exact_relational_lineage(org_a, admin_profile):
    lead = _lead(org_a)

    account, contact, opportunity = convert_lead_to_account(
        lead, _request(admin_profile)
    )

    lineage = LeadConversion.objects.get(lead=lead)
    assert lineage.org_id == org_a.id
    assert lineage.account_id == account.id
    assert lineage.contact_id == contact.id
    assert lineage.opportunity_id == opportunity.id
    assert lineage.account_created is True
    assert lineage.contact_created is True
    assert lineage.opportunity_created is True
    assert lineage.conversion_method == "crm_service_v1"


def test_existing_account_and_contact_are_recorded_without_claiming_creation(
    org_a, admin_profile, admin_user
):
    lead = _lead(org_a, "existing")
    account = Account.objects.create(
        org=org_a, name=lead.company_name, created_by=admin_user
    )
    contact = Contact.objects.create(
        org=org_a,
        first_name="Existing",
        last_name="Contact",
        email=lead.email,
        account=account,
        created_by=admin_user,
    )

    result_account, result_contact, _ = convert_lead_to_account(
        lead, _request(admin_profile)
    )

    lineage = LeadConversion.objects.get(lead=lead)
    assert result_account == account
    assert result_contact == contact
    assert lineage.account_created is False
    assert lineage.contact_created is False


def test_lineage_failure_rolls_back_all_conversion_effects(org_a, admin_profile):
    lead = _lead(org_a, "rollback")

    with (
        patch.object(
            LeadConversion.objects,
            "create",
            side_effect=RuntimeError("synthetic fault"),
        ),
        pytest.raises(RuntimeError, match="synthetic fault"),
    ):
        convert_lead_to_account(lead, _request(admin_profile))

    lead.refresh_from_db()
    assert lead.status == "in process"
    assert not Account.objects.filter(org=org_a, name=lead.company_name).exists()
    assert not Contact.objects.filter(org=org_a, email=lead.email).exists()
    assert not Opportunity.objects.filter(org=org_a).exists()
    assert not LeadConversion.objects.filter(lead=lead).exists()


def test_cross_org_conversion_fails_before_writes(org_a, org_b, admin_profile):
    lead = _lead(org_b, "wrong-org")

    with pytest.raises(ValueError, match="organization mismatch"):
        convert_lead_to_account(lead, _request(admin_profile))

    assert not LeadConversion.objects.filter(lead=lead).exists()
    assert not Account.objects.filter(org=org_a, name=lead.company_name).exists()


def test_lineage_is_append_only(org_a, admin_profile):
    lead = _lead(org_a, "immutable")
    convert_lead_to_account(lead, _request(admin_profile))
    lineage = LeadConversion.objects.get(lead=lead)

    lineage.conversion_method = "tampered"
    with pytest.raises(ValidationError, match="append-only"):
        lineage.save()
    with pytest.raises(ValidationError, match="append-only"):
        lineage.delete()


@pytest.mark.postgres_only
def test_concurrent_conversion_creates_one_lineage_and_one_opportunity(
    org_a, admin_profile
):
    if connection.vendor != "postgresql":
        pytest.skip("row locks require PostgreSQL")
    lead = _lead(org_a, "concurrent")

    def convert_once():
        close_old_connections()
        try:
            from common.models import Profile

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.current_org', %s, false)",
                    [str(org_a.id)],
                )
            profile = Profile.objects.get(pk=admin_profile.pk)
            convert_lead_to_account(Lead.objects.get(pk=lead.pk), _request(profile))
            return "converted"
        except ValueError as exc:
            assert "already been converted" in str(exc)
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: convert_once(), range(2)))

    assert outcomes == ["converted", "rejected"]
    assert LeadConversion.objects.filter(lead=lead).count() == 1
    assert Opportunity.objects.filter(org=org_a).count() == 1


@pytest.mark.postgres_only
def test_database_trigger_rejects_direct_lineage_mutation(org_a, admin_profile):
    if connection.vendor != "postgresql":
        pytest.skip("append-only trigger requires PostgreSQL")
    lead = _lead(org_a, "db-trigger")
    convert_lead_to_account(lead, _request(admin_profile))

    with pytest.raises(DatabaseError, match="append-only"):
        LeadConversion.objects.filter(lead=lead).update(conversion_method="tampered")
