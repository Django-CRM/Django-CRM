"""Hermetic tests for encrypted LGPD export creation."""

import json
import os
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import Account
from common.models import Activity, Attachments
from contacts.models import Contact
from leads.models import (
    DataSubjectRequest,
    DataSubjectRequestEvent,
    Lead,
    LeadConversion,
)
from leads.privacy_export import (
    MAGIC,
    PrivacyExportError,
    prepare_encrypted_lead_export,
)
from opportunity.models import Opportunity
from tasks.models import Task

pytestmark = pytest.mark.django_db


def _request(
    org, user, lead, *, request_type="access", verified=True, legal_hold=False
):
    row = DataSubjectRequest.objects.create(
        org=org,
        subject_ref_digest=__import__("hashlib")
        .sha256(f"{org.id}:{lead.id}".encode())
        .hexdigest(),
        request_type=request_type,
        status="verified" if verified else "submitted",
        due_at=timezone.now() + timedelta(days=15),
        legal_hold=legal_hold,
        idempotency_key_digest="a" * 64,
        request_digest="b" * 64,
        created_by=user,
    )
    DataSubjectRequestEvent.objects.create(
        request=row,
        org=org,
        sequence=1,
        event_type="submitted",
        actor_ref_digest="c" * 64,
        created_by=user,
    )
    return row


def test_export_is_encrypted_private_expiring_and_audited(tmp_path, org_a, admin_user):
    os.chmod(tmp_path, 0o700)
    lead = Lead.objects.create(
        org=org_a, first_name="Synthetic", email="synthetic@example.invalid"
    )
    contact = Contact.objects.create(
        org=org_a,
        first_name="Linked",
        last_name="Contact",
        email="linked@example.invalid",
    )
    lead.contacts.add(contact)
    account = Account.objects.create(
        org=org_a,
        name="Synthetic account",
        email="account@example.invalid",
        description="account free text must not be exported",
    )
    converted_contact = Contact.objects.create(
        org=org_a,
        first_name="Converted",
        last_name="Contact",
        email="converted@example.invalid",
        account=account,
    )
    opportunity = Opportunity.objects.create(
        org=org_a,
        name="Synthetic opportunity",
        account=account,
        amount="1250.00",
        stage="QUALIFICATION",
        description="opportunity free text must not be exported",
    )
    LeadConversion.objects.create(
        org=org_a,
        lead=lead,
        account=account,
        contact=converted_contact,
        opportunity=opportunity,
        account_created=True,
        contact_created=True,
        opportunity_created=True,
    )
    Task.objects.create(
        org=org_a,
        lead=lead,
        title="Follow up",
        status="New",
        priority="High",
    )
    Attachments.objects.create(
        org=org_a,
        content_type=ContentType.objects.get_for_model(Lead),
        object_id=lead.id,
        file_name="synthetic.txt",
        attachment=SimpleUploadedFile("synthetic.txt", b"binary-must-not-be-exported"),
    )
    Activity.objects.create(
        org=org_a,
        action="VIEW",
        entity_type="Lead",
        entity_id=lead.id,
        entity_name="must-not-be-exported",
        description="private free text must not be exported",
    )
    request = _request(org_a, admin_user, lead)
    key = bytes(range(32))
    result = prepare_encrypted_lead_export(
        request_id=request.id,
        lead_id=lead.id,
        org=org_a,
        actor=admin_user,
        encryption_key=key,
        export_root=tmp_path,
        now=timezone.now(),
    )
    raw = result["artifact_path"].read_bytes()
    assert b"synthetic@example.invalid" not in raw
    assert result["artifact_path"].stat().st_mode & 0o777 == 0o600
    nonce = raw[len(MAGIC) : len(MAGIC) + 12]
    plaintext = AESGCM(key).decrypt(nonce, raw[len(MAGIC) + 12 :], result["aad"])
    payload = json.loads(plaintext)
    assert payload["records"]["lead"]["email"] == "synthetic@example.invalid"
    assert {item["email"] for item in payload["records"]["contacts"]} == {
        "linked@example.invalid",
        "converted@example.invalid",
    }
    assert payload["records"]["accounts"][0]["id"] == str(account.id)
    assert payload["records"]["opportunities"][0]["id"] == str(opportunity.id)
    lineage = payload["records"]["lead_conversions"][0]
    assert lineage["lead_id"] == str(lead.id)
    assert lineage["account_id"] == str(account.id)
    assert lineage["contact_id"] == str(converted_contact.id)
    assert lineage["opportunity_id"] == str(opportunity.id)
    assert payload["records"]["tasks"][0]["title"] == "Follow up"
    assert payload["records"]["attachment_metadata"][0]["file_name"] == "synthetic.txt"
    assert payload["records"]["activities"][0]["action"] == "VIEW"
    assert b"binary-must-not-be-exported" not in plaintext
    assert b"private free text must not be exported" not in plaintext
    assert b"account free text must not be exported" not in plaintext
    assert b"opportunity free text must not be exported" not in plaintext
    event = DataSubjectRequestEvent.objects.get(event_type="export_prepared")
    assert result["artifact_ref"] not in event.evidence_ref_digest
    assert DataSubjectRequest.objects.get().version == 2


@pytest.mark.parametrize(
    ("request_type", "verified", "legal_hold"),
    [("deletion", True, False), ("access", False, False), ("access", True, True)],
)
def test_export_fails_closed_for_ineligible_requests(
    tmp_path, org_a, admin_user, request_type, verified, legal_hold
):
    os.chmod(tmp_path, 0o700)
    lead = Lead.objects.create(org=org_a, first_name="Synthetic")
    request = _request(
        org_a,
        admin_user,
        lead,
        request_type=request_type,
        verified=verified,
        legal_hold=legal_hold,
    )
    with pytest.raises(PrivacyExportError):
        prepare_encrypted_lead_export(
            request_id=request.id,
            lead_id=lead.id,
            org=org_a,
            actor=admin_user,
            encryption_key=bytes(range(32)),
            export_root=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_export_rejects_weak_key_and_symlink_root(tmp_path, org_a, admin_user):
    os.chmod(tmp_path, 0o700)
    lead = Lead.objects.create(org=org_a, first_name="Synthetic")
    request = _request(org_a, admin_user, lead)
    alias = tmp_path.parent / "export-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PrivacyExportError):
        prepare_encrypted_lead_export(
            request_id=request.id,
            lead_id=lead.id,
            org=org_a,
            actor=admin_user,
            encryption_key=b"weak",
            export_root=tmp_path,
        )
    with pytest.raises(PrivacyExportError):
        prepare_encrypted_lead_export(
            request_id=request.id,
            lead_id=lead.id,
            org=org_a,
            actor=admin_user,
            encryption_key=bytes(range(32)),
            export_root=alias,
        )
