"""Macro placeholder rendering.

Server-side substitution keeps the supported placeholder set in one place so
clients can't drift. Tokens unknown to this module are left literal so that
agents see the un-substituted form rather than getting silent damage.
"""

from __future__ import annotations

import re
from typing import Iterable

# Match `%token%` where token is letters/digits/underscores. Greedy enough
# to handle adjacent placeholders without eating intermediate text.
_PLACEHOLDER_RE = re.compile(r"%([a-zA-Z_][a-zA-Z0-9_]*)%")


def _profile_full_name(profile) -> str:
    """Best display name for a Profile.

    The project's `User` model has no first_name/last_name fields, only
    email, so the agent display falls back to the email's local part to
    keep replies readable ("alex@…" rather than the full address). An app
    member is named as it was connected instead.
    """
    from common.app_members import app_member_name

    name = app_member_name(profile)
    if name:
        return name
    user = getattr(profile, "user", None)
    if user is None:
        return ""
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return ""
    local = email.split("@", 1)[0]
    return local or email


def _primary_contact(case):
    """Return the contact we treat as 'the customer' for placeholder purposes.

    Cases can have multiple contacts; v1 picks the first by FK order.
    """
    if case is None:
        return None
    contacts_mgr = getattr(case, "contacts", None)
    if contacts_mgr is None:
        return None
    return contacts_mgr.first()


def _contact_full_name(contact) -> str:
    if contact is None:
        return ""
    first = (getattr(contact, "first_name", "") or "").strip()
    last = (getattr(contact, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    return full or (getattr(contact, "email", "") or "")


def _supported_values(case, profile) -> dict[str, str]:
    """Build the substitution table for one render call.

    Anything that resolves to None becomes "" so the placeholder is still
    swapped out (matches user expectation: empty rather than literal).
    """
    contact = _primary_contact(case)
    org = getattr(case, "org", None) or (
        getattr(profile, "org", None) if profile is not None else None
    )
    return {
        "customer_name": _contact_full_name(contact),
        "customer_email": (getattr(contact, "email", "") or "") if contact else "",
        "case_id": str(getattr(case, "id", "") or "") if case else "",
        "case_subject": (getattr(case, "name", "") or "") if case else "",
        "agent_name": _profile_full_name(profile) if profile is not None else "",
        "agent_email": (
            getattr(getattr(profile, "user", None), "email", "") or ""
            if profile is not None
            else ""
        ),
        "org_name": (getattr(org, "name", "") or "") if org is not None else "",
    }


# The placeholders `render_macro` substitutes, in the order the composer shows
# them, each paired with a human description of what it resolves to. This tuple
# is the single source of truth for the supported set: `SUPPORTED_TOKENS` (used
# by both the renderer and `find_unknown_placeholders`) is derived from it, and
# the macros list API returns it verbatim so a web/mobile reference card cannot
# drift from the tokens the server actually expands. Tokens carry their `%...%`
# wrapping so consumers can show them exactly as they appear in a macro body.
SUPPORTED_PLACEHOLDERS: tuple[dict[str, str], ...] = (
    {"token": "%customer_name%", "resolves": "The case's first contact"},
    {"token": "%customer_email%", "resolves": "That contact's email"},
    {"token": "%case_id%", "resolves": "The case number"},
    {"token": "%case_subject%", "resolves": "The case title"},
    {"token": "%agent_name%", "resolves": "You: the local part of your email"},
    {"token": "%agent_email%", "resolves": "Your email"},
    {"token": "%org_name%", "resolves": "The organisation name"},
)

SUPPORTED_TOKENS: Iterable[str] = frozenset(
    p["token"].strip("%") for p in SUPPORTED_PLACEHOLDERS
)


def find_unknown_placeholders(body: str | None) -> list[str]:
    """Return the `%token%` placeholders in `body` that aren't supported.

    Order-preserving and de-duplicated so callers can render them in the
    order they appear. Tokens are returned with their `%...%` wrapping so
    the UI / API consumer can show them verbatim. The result is non-empty
    only when the macro author has typoed (`%custmer_name%`) or used a
    token we don't expand server-side. Same set as `render_macro`.
    """
    if not body:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _PLACEHOLDER_RE.finditer(body):
        token = match.group(1)
        if token in SUPPORTED_TOKENS or token in seen_set:
            continue
        seen.append(f"%{token}%")
        seen_set.add(token)
    return seen


def render_macro(macro, case, profile) -> str:
    """Substitute supported `%token%` placeholders in `macro.body`.

    - Empty body -> empty string.
    - Unknown tokens (e.g. `%priority%`) are left literal so the agent can
      see them and fix the macro rather than silently sending broken text.
    - Missing data (case has no contact, profile has no last name) renders
      as the empty string.
    """
    body = (macro.body or "") if macro is not None else ""
    if not body:
        return ""

    values = _supported_values(case, profile)

    def _sub(match: re.Match) -> str:
        token = match.group(1)
        if token in values:
            return values[token]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body)
