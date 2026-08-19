"""NEXTTHOUSE campaign and privacy-aware attribution model tests."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from leads.models import Lead, LeadAttributionTouch, MarketingCampaign

pytestmark = pytest.mark.django_db


def campaign(org, **overrides):
    values = {
        "org": org,
        "code": "future-campaign",
        "name": "Future campaign",
        "primary_channel": "organic_search",
    }
    values.update(overrides)
    return MarketingCampaign.objects.create(**values)


def lead(org):
    return Lead.objects.create(org=org, first_name="Synthetic", last_name="Lead")


def touch(lead_obj, **overrides):
    digest_marker = "c" if overrides.get("touch_type") == "last" else "a"
    values = {
        "lead": lead_obj,
        "org": lead_obj.org,
        "touch_type": "first",
        "occurred_at": timezone.now() - timedelta(minutes=1),
        "source": "google",
        "medium": "organic",
        "campaign_key": "future-campaign",
        "lawful_basis": "consent",
        "privacy_notice_version": "privacy-v1",
        "consent_evidence_ref": "evidence_opaque_01",
        "idempotency_key_digest": digest_marker * 64,
        "request_digest": "b" * 64,
    }
    values.update(overrides)
    return LeadAttributionTouch.objects.create(**values)


def test_campaign_code_is_normalized_and_unique_per_org(org_a):
    first = campaign(org_a, code="  Future-Campaign  ")
    assert first.code == "future-campaign"

    with pytest.raises(ValidationError):
        campaign(org_a, code="FUTURE-CAMPAIGN", name="Duplicate")


def test_same_campaign_code_is_allowed_in_another_org(org_a, org_b):
    campaign(org_a)
    other = campaign(org_b)
    assert other.org_id == org_b.id


def test_campaign_rejects_reverse_date_window(org_a):
    now = timezone.now()
    with pytest.raises(ValidationError, match="end cannot precede start"):
        campaign(org_a, starts_at=now, ends_at=now - timedelta(seconds=1))


def test_touch_requires_consent_evidence(org_a):
    with pytest.raises(ValidationError):
        touch(lead(org_a), consent_evidence_ref="")


def test_touch_rejects_cross_org_campaign(org_a, org_b):
    foreign_campaign = campaign(org_b)
    with pytest.raises(ValidationError, match="Campaign organization"):
        touch(lead(org_a), campaign=foreign_campaign)


def test_touch_rejects_cross_org_lead(org_a, org_b):
    foreign_lead = lead(org_b)
    with pytest.raises(ValidationError, match="Attribution organization"):
        LeadAttributionTouch.objects.create(
            lead=foreign_lead,
            org=org_a,
            touch_type="first",
            occurred_at=timezone.now() - timedelta(minutes=1),
            source="direct",
            lawful_basis="legitimate_interest",
            privacy_notice_version="privacy-v1",
        )


def test_touch_rejects_raw_url_or_email_in_tracking_fields(org_a):
    with pytest.raises(ValidationError, match="Tracking fields"):
        touch(lead(org_a), source="https://example.invalid/?email=user@example.invalid")


def test_first_and_last_touch_are_unique_per_lead(org_a):
    lead_obj = lead(org_a)
    touch(lead_obj)

    with pytest.raises(ValidationError):
        touch(lead_obj, source="newsletter")

    last = touch(lead_obj, touch_type="last", source="direct")
    assert last.touch_type == "last"


def test_database_constraint_remains_a_race_safety_net(org_a):
    lead_obj = lead(org_a)
    touch(lead_obj)
    duplicate = LeadAttributionTouch(
        lead=lead_obj,
        org=org_a,
        touch_type="first",
        occurred_at=timezone.now() - timedelta(seconds=1),
        source="direct",
        lawful_basis="legitimate_interest",
        privacy_notice_version="privacy-v1",
        idempotency_key_digest="d" * 64,
        request_digest="e" * 64,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        LeadAttributionTouch.objects.bulk_create([duplicate])


def test_no_model_field_stores_raw_provider_payloads():
    forbidden = {"payload", "raw_url", "headers", "cookie", "token", "email"}
    field_names = {field.name for field in LeadAttributionTouch._meta.fields}
    assert field_names.isdisjoint(forbidden)
