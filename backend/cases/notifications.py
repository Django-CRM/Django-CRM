"""Cases-side notification producer.

Fans a single comment out to:
    - mentioned profiles → ``case.mentioned`` (rate-limited per spec)
    - all watchers (minus actor and mentioned)  → ``case.commented``

Mention parsing follows ``docs/cases/tier2/watchers-mentions.md``:
    - regex ``@([a-zA-Z0-9._-]+)`` with no-leading-alphanumeric guard so
      we don't match the local-part of an email address
    - resolution = "username == local-part of an active profile's email
      in the same org"
    - every resolved mention also auto-adds the user as a watcher
      (``subscribed_via='mention'``) unless they already have a row
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Iterable

from django.utils import timezone

from cases.models import CaseWatcher
from common import notifications as notif_mod
from common.models import Notification, Profile

logger = logging.getLogger(__name__)

# `@username` with no leading alphanumeric (so emails are skipped).
MENTION_RE = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z0-9._-]+)")

# Per-recipient/case throttle for `case.mentioned`.
MENTION_RATE_LIMIT_SECONDS = 60

EXCERPT_LENGTH = 200


def case_link(case_id) -> str:
    """Client-side path for a case.

    The model and this app call them cases; every client calls them tickets
    and routes them at `/tickets/<id>`. This used to emit `/cases/<id>`, which
    no client has ever served. The SvelteKit app has no `/cases` route at
    all, and its notification panel assigns `link` straight to
    `window.location.href`, so every notification ever sent landed on a 404.

    It lives in one function because the fix belongs here rather than in the
    readers: the same rows are consumed by the web panel, the notifications
    page and the Flutter client, and a rewrite in each of them is three places
    to forget. `Notification.link` is a client path, not an API path. Do not
    prefix it with `/api/`.
    """
    return f"/tickets/{case_id}"


def parse_mentions(body: str) -> list[str]:
    """Return unique, lower-cased usernames found in ``body``."""
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in MENTION_RE.findall(body):
        u = raw.lower()
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def resolve_mentions(usernames: Iterable[str], org_id) -> list[Profile]:
    """Match each username against the email local-part of an active
    profile in the same org. Unresolved usernames are silently dropped.
    """
    usernames = list(usernames)
    if not usernames:
        return []
    profiles = (
        Profile.objects.filter(org_id=org_id, is_active=True)
        .select_related("user")
        .exclude(user__isnull=True)
    )
    by_local: dict[str, Profile] = {}
    for p in profiles:
        email = (p.user.email or "").lower()
        local = email.split("@", 1)[0]
        if local and local not in by_local:
            by_local[local] = p
    return [by_local[u] for u in usernames if u in by_local]


def _was_recently_mentioned(profile, case_id) -> bool:
    cutoff = timezone.now() - timedelta(seconds=MENTION_RATE_LIMIT_SECONDS)
    return Notification.objects.filter(
        recipient=profile,
        verb="case.mentioned",
        entity_id=case_id,
        created_at__gte=cutoff,
    ).exists()


def _ensure_watcher(case, profile, *, via="mention") -> bool:
    """Auto-add ``profile`` as a watcher of ``case`` if not already.

    Returns True when a row was created. An app member never becomes one.
    """
    if profile.is_app:
        return False
    _, created = CaseWatcher.objects.get_or_create(
        case=case,
        profile=profile,
        defaults={"org": case.org, "subscribed_via": via},
    )
    return created


def _excerpt(body: str) -> str:
    body = (body or "").strip()
    if len(body) <= EXCERPT_LENGTH:
        return body
    return body[: EXCERPT_LENGTH - 1].rstrip() + "…"


def dispatch_for_comment(comment, case, actor: Profile | None = None) -> dict:
    """Fan a freshly-created comment out as in-app notifications.

    Returns a dict ``{"mentioned": [...], "watchers_notified": [...]}`` for
    test introspection.
    """
    if case is None:
        return {"mentioned": [], "watchers_notified": []}

    actor_id = actor.id if actor is not None else None
    body = getattr(comment, "comment", "") or ""

    mentioned_profiles = resolve_mentions(parse_mentions(body), case.org_id)
    mentioned: list[Profile] = []
    for p in mentioned_profiles:
        if actor_id is not None and p.id == actor_id:
            continue
        if not p.is_active:
            continue
        # Auto-watch first so they receive future fan-outs even if the
        # mention itself was rate-limited.
        _ensure_watcher(case, p, via="mention")
        if _was_recently_mentioned(p, case.id):
            continue
        notif_mod.create(
            p,
            "case.mentioned",
            actor=actor,
            entity=case,
            entity_name=case.name,
            link=case_link(case.id),
            data={"comment_excerpt": _excerpt(body)},
        )
        mentioned.append(p)

    # Fan out `case.commented` to remaining watchers, exclude actor and
    # anyone who just got `case.mentioned` (they don't need both for the
    # same comment).
    skip_ids = {p.id for p in mentioned}
    if actor_id is not None:
        skip_ids.add(actor_id)
    watcher_profiles = (
        Profile.objects.filter(case_watching=case, is_active=True)
        .exclude(id__in=skip_ids)
        .distinct()
    )
    watchers_notified: list[Profile] = []
    for p in watcher_profiles:
        notif_mod.create(
            p,
            "case.commented",
            actor=actor,
            entity=case,
            entity_name=case.name,
            link=case_link(case.id),
            data={"comment_excerpt": _excerpt(body)},
        )
        watchers_notified.append(p)

    return {"mentioned": mentioned, "watchers_notified": watchers_notified}
