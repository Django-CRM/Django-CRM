"""Deterministic, org-scoped lead duplicate lookup.

This module returns candidates; it never merges, deletes or changes a lead.
Ambiguous phone matches fail closed so a shared number cannot silently choose
the wrong person.
"""

import re
from dataclasses import dataclass

from leads.models import Lead


@dataclass(frozen=True)
class DuplicateResult:
    lead: Lead | None
    matched_by: str | None
    ambiguous: bool = False


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) >= 8 else ""


def find_duplicate_lead(*, org, email="", phone=""):
    normalized_email = (email or "").strip().lower()
    if normalized_email:
        existing = Lead.objects.filter(org=org, email__iexact=normalized_email).first()
        if existing is not None:
            return DuplicateResult(existing, "email")

    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return DuplicateResult(None, None)

    matches = [
        item
        for item in Lead.objects.filter(org=org).only("id", "phone")
        if normalize_phone(item.phone) == normalized_phone
    ]
    if len(matches) == 1:
        return DuplicateResult(matches[0], "phone")
    return DuplicateResult(None, None, ambiguous=len(matches) > 1)
