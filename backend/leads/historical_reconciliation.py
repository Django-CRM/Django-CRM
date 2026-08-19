"""Read-only historical lead-conversion reconciliation.

This module deliberately has no mutation API.  It produces bounded, opaque
review candidates; only a separately designed gate may ever persist lineage.
"""

import hashlib
import hmac
from collections import Counter

from django.db.models import Q

from leads.models import Lead, LeadConversion
from opportunity.models import Opportunity

MAX_RECORDS = 10_000
CLASSIFICATIONS = (
    "proven",
    "ambiguous",
    "no_evidence",
    "cross_org_conflict",
)


class HistoricalReconciliationError(ValueError):
    pass


def _opaque_ref(key: bytes, namespace: str, value) -> str:
    body = f"{namespace}:{value}".encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _validate_key(reference_key: bytes) -> None:
    if not isinstance(reference_key, bytes) or len(reference_key) < 32:
        raise HistoricalReconciliationError(
            "A caller-supplied reference key of at least 256 bits is required."
        )


def build_historical_conversion_report(
    *, reference_key: bytes, max_records=MAX_RECORDS
):
    """Classify converted leads without changing database state."""

    _validate_key(reference_key)
    if not isinstance(max_records, int) or not 1 <= max_records <= MAX_RECORDS:
        raise HistoricalReconciliationError(
            f"max_records must be between 1 and {MAX_RECORDS}."
        )

    queryset = Lead.objects.filter(status="converted").order_by("created_at", "id")
    total = queryset.count()
    cases = []
    counts = Counter({name: 0 for name in CLASSIFICATIONS})

    for lead in queryset[:max_records]:
        lineage = LeadConversion.objects.filter(lead=lead).first()
        if lineage is not None:
            lineage_orgs = {
                lineage.org_id,
                lineage.lead.org_id,
                lineage.account.org_id,
                lineage.contact.org_id if lineage.contact_id else lead.org_id,
                (lineage.opportunity.org_id if lineage.opportunity_id else lead.org_id),
            }
            if lineage_orgs != {lead.org_id}:
                classification = "cross_org_conflict"
                reason = "LINEAGE_ORG_MISMATCH"
            else:
                classification = "proven"
                reason = "IMMUTABLE_LINEAGE_PRESENT"
            contact_count = int(lineage.contact_id is not None)
            account_count = 1
            opportunity_count = int(lineage.opportunity_id is not None)
        else:
            contacts = list(lead.contacts.all().only("id", "org_id", "account_id"))
            if any(contact.org_id != lead.org_id for contact in contacts):
                classification = "cross_org_conflict"
                reason = "EXPLICIT_CONTACT_ORG_MISMATCH"
                contact_count = len(contacts)
                account_count = 0
                opportunity_count = 0
            else:
                account_ids = {item.account_id for item in contacts if item.account_id}
                opportunities = list(
                    Opportunity.objects.filter(
                        Q(account_id__in=account_ids) | Q(contacts__in=contacts)
                    )
                    .distinct()
                    .only("id", "org_id", "account_id")
                )
                if any(item.org_id != lead.org_id for item in opportunities):
                    classification = "cross_org_conflict"
                    reason = "EXPLICIT_OPPORTUNITY_ORG_MISMATCH"
                elif contacts or account_ids or opportunities:
                    classification = "ambiguous"
                    reason = "EXPLICIT_RELATIONS_WITHOUT_CONVERSION_LEDGER"
                else:
                    classification = "no_evidence"
                    reason = "NO_PERSISTED_RELATIONAL_EVIDENCE"
                contact_count = len(contacts)
                account_count = len(account_ids)
                opportunity_count = len(opportunities)

        counts[classification] += 1
        cases.append(
            {
                "case_ref": _opaque_ref(reference_key, "lead", lead.id),
                "classification": classification,
                "reason_code": reason,
                "relation_counts": {
                    "contacts": contact_count,
                    "accounts": account_count,
                    "opportunities": opportunity_count,
                },
                "write_authorized": False,
            }
        )

    return {
        "schema_version": "lead-conversion-reconciliation/1.0",
        "mode": "dry_run",
        "write_authorized": False,
        "total_converted": total,
        "scanned": len(cases),
        "truncated": total > len(cases),
        "counts": dict(counts),
        "cases": cases,
    }
