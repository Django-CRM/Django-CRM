"""Authenticated, tenant-scoped acquisition API tests."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from leads.models import (
    DataSubjectRequest,
    DataSubjectRequestEvent,
    Lead,
    LeadAttributionTouch,
    MarketingCampaign,
)

pytestmark = pytest.mark.django_db

CAMPAIGNS_URL = "/api/leads/campaigns/"
ATTRIBUTION_URL = "/api/leads/attribution/"
IDEMPOTENCY_KEY = "idempotency_key_for_synthetic_test_001"
PRIVACY_URL = "/api/leads/privacy-requests/"


def create_lead(org):
    return Lead.objects.create(org=org, first_name="Synthetic")


def campaign_payload(code="future-campaign"):
    return {
        "code": code,
        "name": "Future campaign",
        "status": "active",
        "primary_channel": "organic_search",
    }


def attribution_payload(lead_obj, campaign=None):
    payload = {
        "lead_ref": str(lead_obj.id),
        "touch_type": "first",
        "occurred_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
        "source": "google",
        "medium": "organic",
        "campaign_key": "future-campaign",
        "landing_page_ref": "page_opaque_01",
        "referrer_domain": "nextthouse.com.br",
        "lawful_basis": "consent",
        "privacy_notice_version": "privacy-v1",
        "consent_evidence_ref": "evidence_opaque_01",
    }
    if campaign:
        payload["campaign_ref"] = str(campaign.id)
    return payload


def post_attribution(client, payload, key=IDEMPOTENCY_KEY):
    return client.post(
        ATTRIBUTION_URL,
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_admin_can_create_and_list_campaign(admin_client, org_a):
    created = admin_client.post(CAMPAIGNS_URL, campaign_payload(), format="json")
    assert created.status_code == 201
    assert created.json()["code"] == "future-campaign"

    listed = admin_client.get(CAMPAIGNS_URL)
    assert listed.status_code == 200
    assert [row["code"] for row in listed.json()["results"]] == ["future-campaign"]
    assert MarketingCampaign.objects.get().org_id == org_a.id


def test_campaign_rejects_unknown_and_read_only_input(admin_client):
    payload = campaign_payload()
    payload["id"] = "00000000-0000-0000-0000-000000000001"
    payload["provider_payload"] = {"private": "not allowed"}
    response = admin_client.post(CAMPAIGNS_URL, payload, format="json")
    assert response.status_code == 400
    assert set(response.json()) == {"id", "provider_payload"}
    assert not MarketingCampaign.objects.exists()


def test_non_admin_and_unauthenticated_are_denied(user_client, unauthenticated_client):
    assert user_client.get(CAMPAIGNS_URL).status_code == 403
    assert unauthenticated_client.get(CAMPAIGNS_URL).status_code == 403
    assert not MarketingCampaign.objects.exists()


def test_campaign_list_does_not_disclose_another_org(admin_client, org_b_client, org_a):
    MarketingCampaign.objects.create(
        org=org_a,
        code="private-campaign",
        name="Private",
        primary_channel="direct",
    )
    assert org_b_client.get(CAMPAIGNS_URL).json()["results"] == []


def test_attribution_create_is_idempotent_and_does_not_expose_digests(
    admin_client, org_a
):
    lead_obj = create_lead(org_a)
    payload = attribution_payload(lead_obj)

    first = post_attribution(admin_client, payload)
    replay = post_attribution(admin_client, payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert LeadAttributionTouch.objects.count() == 1
    assert "idempotency_key_digest" not in first.json()
    assert "request_digest" not in first.json()
    assert "consent_evidence_ref" not in first.json()
    assert first.json()["consent_evidence_present"] is True


def test_idempotency_key_cannot_be_reused_for_different_command(admin_client, org_a):
    lead_obj = create_lead(org_a)
    payload = attribution_payload(lead_obj)
    assert post_attribution(admin_client, payload).status_code == 201
    payload["source"] = "newsletter"

    conflict = post_attribution(admin_client, payload)

    assert conflict.status_code == 409
    assert conflict.json() == {"code": "idempotency_conflict"}
    assert LeadAttributionTouch.objects.count() == 1


def test_missing_or_weak_idempotency_key_fails_before_write(admin_client, org_a):
    payload = attribution_payload(create_lead(org_a))
    response = post_attribution(admin_client, payload, key="weak")
    assert response.status_code == 400
    assert not LeadAttributionTouch.objects.exists()


def test_second_first_touch_is_rejected_without_server_error(admin_client, org_a):
    lead_obj = create_lead(org_a)
    payload = attribution_payload(lead_obj)
    assert post_attribution(admin_client, payload).status_code == 201

    response = post_attribution(
        admin_client,
        payload,
        key="different_idempotency_key_for_test_002",
    )

    assert response.status_code == 400
    assert "touch_type" in response.json()
    assert LeadAttributionTouch.objects.count() == 1


def test_cross_org_lead_and_campaign_refs_are_indistinguishable_from_missing(
    admin_client, org_a, org_b
):
    foreign_lead = create_lead(org_b)
    foreign_campaign = MarketingCampaign.objects.create(
        org=org_b,
        code="foreign",
        name="Foreign",
        primary_channel="direct",
    )
    payload = attribution_payload(foreign_lead, foreign_campaign)

    response = post_attribution(admin_client, payload)

    assert response.status_code == 400
    assert response.json() == {"lead_ref": ["Lead not found."]}
    assert not LeadAttributionTouch.objects.filter(org=org_a).exists()


def test_raw_url_and_unknown_field_fail_closed(admin_client, org_a):
    payload = attribution_payload(create_lead(org_a))
    payload["source"] = "https://tracker.invalid"
    payload["provider_payload"] = {"private": "not allowed"}

    response = post_attribution(admin_client, payload)

    assert response.status_code == 400
    assert not LeadAttributionTouch.objects.exists()


def test_attribution_list_is_org_scoped(admin_client, org_a):
    lead_obj = create_lead(org_a)
    assert (
        post_attribution(admin_client, attribution_payload(lead_obj)).status_code == 201
    )
    listed = admin_client.get(ATTRIBUTION_URL)
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1
    assert listed.json()["results"][0]["lead_ref"] == str(lead_obj.id)


def test_campaign_cursor_paginates_without_cross_org_disclosure(
    admin_client, org_a, org_b
):
    for index in range(55):
        MarketingCampaign.objects.create(
            org=org_a,
            code=f"campaign-{index}",
            name=f"Campaign {index}",
            primary_channel="direct",
        )
    MarketingCampaign.objects.create(
        org=org_b, code="foreign", name="Foreign", primary_channel="direct"
    )
    first = admin_client.get(CAMPAIGNS_URL).json()
    assert len(first["results"]) == 50
    second = admin_client.get(first["next"]).json()
    ids = {row["id"] for row in first["results"] + second["results"]}
    assert len(ids) == 55
    assert all(row["code"] != "foreign" for row in first["results"] + second["results"])


def test_privacy_request_is_idempotent_pseudonymous_and_never_executes_deletion(
    admin_client, org_a
):
    lead_obj = create_lead(org_a)
    payload = {
        "lead_ref": str(lead_obj.id),
        "request_type": "deletion",
        "due_at": (timezone.now() + timedelta(days=15)).isoformat(),
    }
    key = "privacy_request_idempotency_key_000001"
    first = admin_client.post(
        PRIVACY_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    replay = admin_client.post(
        PRIVACY_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["status"] == "submitted"
    assert first.json()["legal_hold"] is False
    assert "lead_ref" not in first.json()
    row = DataSubjectRequest.objects.get()
    assert row.subject_ref_digest != str(lead_obj.id)
    assert len(row.subject_ref_digest) == 64
    assert Lead.objects.filter(pk=lead_obj.id).exists()


def test_privacy_request_cross_org_and_mutation_fields_fail_closed(admin_client, org_b):
    payload = {
        "lead_ref": str(create_lead(org_b).id),
        "request_type": "access",
        "due_at": (timezone.now() + timedelta(days=15)).isoformat(),
        "status": "completed",
    }
    response = admin_client.post(
        PRIVACY_URL,
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="privacy_request_idempotency_key_000002",
    )
    assert response.status_code == 400
    assert not DataSubjectRequest.objects.exists()


def test_privacy_request_workflow_is_versioned_and_evidence_is_digest_only(
    admin_client, org_a
):
    lead_obj = create_lead(org_a)
    created = admin_client.post(
        PRIVACY_URL,
        {
            "lead_ref": str(lead_obj.id),
            "request_type": "access",
            "due_at": (timezone.now() + timedelta(days=15)).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="privacy_request_idempotency_key_000003",
    ).json()
    action_url = f"{PRIVACY_URL}{created['id']}/actions/"
    verified = admin_client.post(
        action_url,
        {
            "action": "verify_identity",
            "expected_version": 1,
            "evidence_ref": "opaque_identity_evidence_01",
        },
        format="json",
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    assert verified.json()["version"] == 2
    events = list(DataSubjectRequestEvent.objects.order_by("sequence"))
    assert [event.event_type for event in events] == ["submitted", "identity_verified"]
    assert events[1].evidence_ref_digest != "opaque_identity_evidence_01"
    assert len(events[1].evidence_ref_digest) == 64

    stale = admin_client.post(
        action_url,
        {"action": "place_legal_hold", "expected_version": 1},
        format="json",
    )
    assert stale.status_code == 409
    assert DataSubjectRequestEvent.objects.count() == 2


def test_privacy_request_has_no_approval_completion_export_or_delete_action(
    admin_client, org_a
):
    lead_obj = create_lead(org_a)
    created = admin_client.post(
        PRIVACY_URL,
        {
            "lead_ref": str(lead_obj.id),
            "request_type": "deletion",
            "due_at": (timezone.now() + timedelta(days=15)).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="privacy_request_idempotency_key_000004",
    ).json()
    action_url = f"{PRIVACY_URL}{created['id']}/actions/"
    for forbidden in ("approve", "complete", "export_data", "delete"):
        response = admin_client.post(
            action_url,
            {"action": forbidden, "expected_version": 1},
            format="json",
        )
        assert response.status_code == 400
    assert Lead.objects.filter(pk=lead_obj.id).exists()
    assert DataSubjectRequest.objects.get().status == "submitted"


def test_privacy_request_events_are_append_only_at_model_boundary(admin_client, org_a):
    lead_obj = create_lead(org_a)
    admin_client.post(
        PRIVACY_URL,
        {
            "lead_ref": str(lead_obj.id),
            "request_type": "correction",
            "due_at": (timezone.now() + timedelta(days=15)).isoformat(),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="privacy_request_idempotency_key_000005",
    )
    event = DataSubjectRequestEvent.objects.get()
    event.reason_code = "OUT_OF_SCOPE"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()
