"""Versioned, fail-closed data inventory for NEXTTHOUSE CRM privacy workflows."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InventoryEntry:
    entity: str
    classification: str
    purpose: str
    subject_link: str
    export_fields: tuple[str, ...]
    retention_proposal: str
    retention_approved: bool
    deletion_mode: str
    dependencies: tuple[str, ...]


LEAD_EXPORT_FIELDS = (
    "id",
    "title",
    "salutation",
    "first_name",
    "last_name",
    "email",
    "phone",
    "job_title",
    "company_name",
    "website",
    "linkedin_url",
    "status",
    "source",
    "description",
    "created_at",
    "updated_at",
)

ATTRIBUTION_EXPORT_FIELDS = (
    "touch_type",
    "occurred_at",
    "source",
    "medium",
    "campaign_key",
    "content_key",
    "term_key",
    "landing_page_ref",
    "referrer_domain",
    "lawful_basis",
    "privacy_notice_version",
)

CONTACT_EXPORT_FIELDS = (
    "id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "organization",
    "title",
    "department",
    "do_not_call",
    "linkedin_url",
    "address_line",
    "city",
    "state",
    "postcode",
    "country",
    "description",
    "created_at",
    "updated_at",
)

ACCOUNT_EXPORT_FIELDS = (
    "id",
    "name",
    "email",
    "phone",
    "website",
    "industry",
    "address_line",
    "city",
    "state",
    "postcode",
    "country",
    "created_at",
    "updated_at",
)

OPPORTUNITY_EXPORT_FIELDS = (
    "id",
    "name",
    "stage",
    "opportunity_type",
    "currency",
    "amount",
    "probability",
    "closed_on",
    "lead_source",
    "created_at",
    "updated_at",
)

CONVERSION_EXPORT_FIELDS = (
    "id",
    "account_created",
    "contact_created",
    "opportunity_created",
    "conversion_method",
    "created_at",
)

TASK_EXPORT_FIELDS = (
    "id",
    "title",
    "status",
    "priority",
    "due_date",
    "description",
    "created_at",
    "updated_at",
)

ATTACHMENT_METADATA_FIELDS = ("id", "file_name", "created_at")
ACTIVITY_EXPORT_FIELDS = ("id", "action", "entity_type", "created_at")

INVENTORY_VERSION = "crm-privacy-inventory/1.0"

INVENTORY = {
    "lead": InventoryEntry(
        entity="lead",
        classification="direct_identifier_and_sales_profile",
        purpose="lead_management_and_sales_follow_up",
        subject_link="direct",
        export_fields=LEAD_EXPORT_FIELDS,
        retention_proposal="RELATIONSHIP_END_PLUS_LEGAL_PERIOD",
        retention_approved=False,
        deletion_mode="blocked_pending_dependency_reconciliation",
        dependencies=(
            "contacts",
            "accounts",
            "opportunities",
            "tasks",
            "attachments",
            "activities",
        ),
    ),
    "lead_attribution_touch": InventoryEntry(
        entity="lead_attribution_touch",
        classification="marketing_attribution_pseudonymous",
        purpose="source_measurement_and_campaign_attribution",
        subject_link="lead_foreign_key",
        export_fields=ATTRIBUTION_EXPORT_FIELDS,
        retention_proposal="LAST_TOUCH_PLUS_13_MONTHS",
        retention_approved=False,
        deletion_mode="blocked_pending_measurement_and_consent_review",
        dependencies=("lead", "marketing_campaign", "consent_evidence_store"),
    ),
    "contact": InventoryEntry(
        entity="contact",
        classification="direct_identifier_and_relationship_profile",
        purpose="contact_management",
        subject_link="explicit_lead_contacts_many_to_many",
        export_fields=CONTACT_EXPORT_FIELDS,
        retention_proposal="RELATIONSHIP_END_PLUS_LEGAL_PERIOD",
        retention_approved=False,
        deletion_mode="blocked_pending_account_and_opportunity_reconciliation",
        dependencies=("accounts", "opportunities", "tasks", "attachments"),
    ),
    "account": InventoryEntry(
        entity="account",
        classification="business_and_contact_profile",
        purpose="customer_relationship_management",
        subject_link="lead_conversion_account_foreign_key",
        export_fields=ACCOUNT_EXPORT_FIELDS,
        retention_proposal="RELATIONSHIP_END_PLUS_LEGAL_PERIOD",
        retention_approved=False,
        deletion_mode="blocked_pending_contract_and_financial_reconciliation",
        dependencies=("lead_conversion", "contacts", "opportunities", "invoices"),
    ),
    "opportunity": InventoryEntry(
        entity="opportunity",
        classification="sales_pipeline_and_financial_profile",
        purpose="sales_opportunity_management",
        subject_link="lead_conversion_opportunity_foreign_key",
        export_fields=OPPORTUNITY_EXPORT_FIELDS,
        retention_proposal="RELATIONSHIP_END_PLUS_LEGAL_PERIOD",
        retention_approved=False,
        deletion_mode="blocked_pending_financial_reconciliation",
        dependencies=("lead_conversion", "account", "orders", "invoices"),
    ),
    "lead_conversion": InventoryEntry(
        entity="lead_conversion",
        classification="relationship_provenance",
        purpose="conversion_lineage_and_accountability",
        subject_link="one_to_one_lead_foreign_key",
        export_fields=CONVERSION_EXPORT_FIELDS,
        retention_proposal="AUDIT_POLICY_PENDING",
        retention_approved=False,
        deletion_mode="append_only_no_automatic_delete",
        dependencies=("lead", "account", "contact", "opportunity"),
    ),
    "task": InventoryEntry(
        entity="task",
        classification="sales_workflow_and_free_text",
        purpose="sales_follow_up",
        subject_link="direct_lead_foreign_key_only",
        export_fields=TASK_EXPORT_FIELDS,
        retention_proposal="PARENT_RELATIONSHIP_POLICY",
        retention_approved=False,
        deletion_mode="blocked_pending_parent_reconciliation",
        dependencies=("lead", "attachments", "activities"),
    ),
    "attachment_metadata": InventoryEntry(
        entity="attachment_metadata",
        classification="file_metadata",
        purpose="document_linkage",
        subject_link="generic_relation_to_lead",
        export_fields=ATTACHMENT_METADATA_FIELDS,
        retention_proposal="PARENT_RELATIONSHIP_POLICY",
        retention_approved=False,
        deletion_mode="binary_content_out_of_scope",
        dependencies=("lead", "storage_backend", "malware_and_rights_review"),
    ),
    "activity": InventoryEntry(
        entity="activity",
        classification="audit_metadata",
        purpose="crm_accountability",
        subject_link="entity_type_lead_and_entity_id",
        export_fields=ACTIVITY_EXPORT_FIELDS,
        retention_proposal="AUDIT_POLICY_PENDING",
        retention_approved=False,
        deletion_mode="never_automatic",
        dependencies=("lead", "audit_requirements"),
    ),
    "data_subject_request": InventoryEntry(
        entity="data_subject_request",
        classification="privacy_governance_pseudonymous",
        purpose="lgpd_request_accountability",
        subject_link="subject_ref_digest",
        export_fields=(
            "id",
            "request_type",
            "status",
            "due_at",
            "legal_hold",
            "created_at",
        ),
        retention_proposal="REQUEST_CLOSED_PLUS_5_YEARS",
        retention_approved=False,
        deletion_mode="never_automatic",
        dependencies=("data_subject_request_event", "legal_hold"),
    ),
    "data_subject_request_event": InventoryEntry(
        entity="data_subject_request_event",
        classification="security_and_compliance_audit",
        purpose="immutable_accountability",
        subject_link="request_foreign_key",
        export_fields=("sequence", "event_type", "reason_code", "created_at"),
        retention_proposal="REQUEST_CLOSED_PLUS_5_YEARS",
        retention_approved=False,
        deletion_mode="append_only_no_automatic_delete",
        dependencies=("data_subject_request", "legal_hold", "audit_requirements"),
    ),
}


def assert_inventory_ready_for_deletion() -> None:
    """Fail closed until every retention decision has explicit approval."""

    pending = sorted(
        name for name, entry in INVENTORY.items() if not entry.retention_approved
    )
    if pending:
        raise RuntimeError(f"Retention approval missing for: {', '.join(pending)}")


def serialize_model_fields(instance, fields: tuple[str, ...]) -> dict:
    result = {}
    for name in fields:
        value = getattr(instance, name)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[name] = value
    return result
