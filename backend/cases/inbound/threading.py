"""Match an inbound email to an existing Case if possible."""

from __future__ import annotations

import re
import uuid
from typing import NamedTuple, Optional

from django.db import connection

from cases.models import Case, EmailMessage

from .parser import ParsedEmail

# Subject-line fallback: `[Case #<short-id>]` where short-id is the first
# 8 hex chars of the case UUID. Used only when RFC headers can't thread.
_SUBJECT_FALLBACK_RE = re.compile(r"\[Case #([0-9a-f]{8})(?:[0-9a-f-]*)?\]", re.I)


def _follow_merge(case: Optional[Case]) -> Optional[Case]:
    """If `case` was merged into another, return the primary; else return as-is.

    Merge chains are forbidden at the API (see CaseMergeView), so a single hop
    is sufficient.
    """
    if case is None:
        return None
    if case.merged_into_id:
        return case.merged_into or case
    return case


class ThreadMatch(NamedTuple):
    """The case an email threads onto, and how it was found.

    `by_header` is True when an In-Reply-To or References id matched a message
    or thread id this org has on record. It is False for a match on the
    subject tag alone, which anybody who has seen one subject line can copy.
    """

    case: Case
    by_header: bool


def find_existing_case(parsed: ParsedEmail, org) -> Optional[ThreadMatch]:
    """Return the ThreadMatch for this email, or None to create a new case.

    Matching priority (highest first):
      1. `In-Reply-To` matches an `EmailMessage.message_id` for this org.
      2. Any id in `References` matches an `EmailMessage.message_id`.
      3. Any id in `References` (or In-Reply-To) matches a `Case.external_thread_id`
         OR `Case.alt_thread_ids` (inherited from a merged duplicate).
      4. Subject line contains `[Case #<short-id>]` and a Case with that
         id-prefix exists in this org.

    In every branch, if the matched case has been merged into a primary,
    the primary is returned instead. Branches 1 to 3 report `by_header=True`,
    branch 4 reports `by_header=False`.
    """
    candidate_ids: list[str] = []
    if parsed.in_reply_to:
        candidate_ids.append(parsed.in_reply_to)
    candidate_ids.extend(parsed.references)
    # de-dupe while preserving order
    seen: set[str] = set()
    candidate_ids = [c for c in candidate_ids if not (c in seen or seen.add(c))]

    if candidate_ids:
        match = (
            EmailMessage.objects.filter(org=org, message_id__in=candidate_ids)
            .select_related("case", "case__merged_into")
            .order_by("-received_at")
            .first()
        )
        if match and match.case_id:
            return ThreadMatch(_follow_merge(match.case), True)

        case_via_thread = (
            Case.objects.select_related("merged_into")
            .filter(org=org, external_thread_id__in=candidate_ids)
            .first()
        )
        if case_via_thread:
            return ThreadMatch(_follow_merge(case_via_thread), True)

        # alt_thread_ids on a primary inherits merged duplicates' thread ids.
        # JSONField `__contains` is Postgres-only; SQLite tests fall back to a
        # narrow Python scan over cases that actually have a non-empty array.
        if connection.vendor == "postgresql":
            for cid in candidate_ids:
                case_via_alt = (
                    Case.objects.select_related("merged_into")
                    .filter(org=org, alt_thread_ids__contains=cid)
                    .first()
                )
                if case_via_alt:
                    return ThreadMatch(_follow_merge(case_via_alt), True)
        else:
            candidate_set = set(candidate_ids)
            for case in (
                Case.objects.select_related("merged_into")
                .filter(org=org)
                .exclude(alt_thread_ids=[])
                .only("id", "alt_thread_ids", "merged_into")
            ):
                if any(tid in candidate_set for tid in (case.alt_thread_ids or [])):
                    return ThreadMatch(_follow_merge(case), True)

    # Subject-line fallback. We need a real prefix because Case.id is a UUID.
    subject_match = _SUBJECT_FALLBACK_RE.search(parsed.subject or "")
    if subject_match:
        prefix = subject_match.group(1).lower()
        # Every UUID starting with the prefix lies in this range, and UUIDs
        # order the same way on Postgres and SQLite, so it is one indexed
        # lookup. Scanning the org's cases instead only ever looked at 200 of
        # them, which missed the tag on most tickets of any real org.
        case = (
            Case.objects.select_related("merged_into")
            .filter(
                org=org,
                id__gte=uuid.UUID(prefix + "0" * 24),
                id__lte=uuid.UUID(prefix + "f" * 24),
            )
            .order_by("created_at")
            .first()
        )
        if case is not None:
            return ThreadMatch(_follow_merge(case), False)

    return None


# A tiny helper used by the pipeline when a brand-new Case is created. The
# Case.external_thread_id should be the Message-ID of the email that birthed it.
def short_case_id(case: Case) -> str:
    """8-char prefix used in subject-line fallback threading."""
    return str(case.id).replace("-", "")[:8]


__all__ = ["ThreadMatch", "find_existing_case", "short_case_id"]
