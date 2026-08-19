"""Contract tests for the fail-closed CRM privacy inventory."""

import pytest

from accounts.models import Account
from common.models import Activity, Attachments
from contacts.models import Contact
from leads.models import (
    DataSubjectRequest,
    DataSubjectRequestEvent,
    Lead,
    LeadAttributionTouch,
    LeadConversion,
)
from leads.privacy_inventory import (
    ATTRIBUTION_EXPORT_FIELDS,
    INVENTORY,
    LEAD_EXPORT_FIELDS,
    assert_inventory_ready_for_deletion,
)
from opportunity.models import Opportunity
from tasks.models import Task


def test_inventory_fields_exist_and_exclude_secrets_and_internal_digests():
    models = {
        "lead": Lead,
        "lead_attribution_touch": LeadAttributionTouch,
        "data_subject_request": DataSubjectRequest,
        "data_subject_request_event": DataSubjectRequestEvent,
        "contact": Contact,
        "account": Account,
        "opportunity": Opportunity,
        "lead_conversion": LeadConversion,
        "task": Task,
        "attachment_metadata": Attachments,
        "activity": Activity,
    }
    prohibited = {
        "idempotency_key_digest",
        "request_digest",
        "subject_ref_digest",
        "evidence_ref_digest",
        "actor_ref_digest",
        "consent_evidence_ref",
    }
    for name, entry in INVENTORY.items():
        available = {field.name for field in models[name]._meta.get_fields()}
        assert set(entry.export_fields) <= available
        assert not (set(entry.export_fields) & prohibited)
        assert entry.deletion_mode != "automatic_delete"


def test_export_contract_uses_the_inventory_allowlists():
    assert INVENTORY["lead"].export_fields == LEAD_EXPORT_FIELDS
    assert (
        INVENTORY["lead_attribution_touch"].export_fields == ATTRIBUTION_EXPORT_FIELDS
    )


def test_deletion_remains_blocked_until_legal_approval():
    with pytest.raises(RuntimeError, match="Retention approval missing"):
        assert_inventory_ready_for_deletion()
