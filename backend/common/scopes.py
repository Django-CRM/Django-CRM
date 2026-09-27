"""Scope vocabulary and request matcher for non-interactive credentials.

Two credentials reach this API without a human at the keyboard: a personal
access token (``bcrm_pat_…``, authenticates as its owning profile) and the
organization API key (``Token: <org.api_key>``, authenticates as an arbitrary
active ADMIN of that org). Both inherited the full permissions of the identity
they resolved to, which made a leaked one equivalent to a leaked session.

This module is the whole boundary. It is deliberately a pure function over
``(scopes, method, path)`` with no ORM and no request object, so it can be
tested exhaustively and reasoned about in one screen. Enforcement lives in
``common.middleware.get_company.GetProfileAndOrg``, the single choke point every
``/api/`` request already passes through, so a new view cannot forget to opt in.

THE GRAMMAR
    A scope is ``<resource>:<action>``.
    ``resource`` is an API root segment (the first path segment after ``/api/``)
    or ``*`` for all of them. ``action`` is ``read`` or ``write``.
    ``read`` covers GET/HEAD/OPTIONS; ``write`` covers POST/PUT/PATCH/DELETE,
    except a POST to one of the exact paths in ``READ_ONLY_POST_PATHS``, which
    is ``read``. The action is decided by method and path together, never by
    anything in the body. A path in ``CROSS_RESOURCE_PATHS`` acts on a second
    resource's records and needs that resource's scope as well.

``write`` does NOT imply ``read``. A token that may create leads but not list
them is a coherent thing to want, and a scope whose name understates what it
grants is how a boundary quietly stops being one.

AN EMPTY SCOPE LIST MEANS UNRESTRICTED
    Every token issued before this module existed carries ``scopes=[]``, and the
    documented behaviour has always been "inherits the owning profile's role".
    Treating ``[]`` as "nothing" would revoke every live token on deploy.
    Treating it as "everything" preserves exactly today's behaviour for them,
    and the credential deny-list below still applies.

THE DENY-LIST IS NOT A SCOPE
    ``CREDENTIAL_PATHS`` is checked first and cannot be satisfied by any scope,
    including ``[]``. A credential that can mint another credential cannot be
    revoked: revoke the token, and whatever it already created lives on. A
    credential that can read the org API key upgrades itself into a permanent
    org-admin key that outlives its own revocation. Both chains are closed here
    rather than in each view, because the view that forgets is the one that
    matters.

    ``/api/auth/`` is deliberately absent. Every view under it either pins
    ``authentication_classes = [JWTAuthentication]`` (MeView, OrgSwitchView,
    ProfileDetailView) or takes no authentication at all (the OAuth and
    magic-link entry points), so a bearer token never authenticates there. An
    entry that can never fire reads like a protection and is not one.
"""

import re

# Every first path segment under `api/` that the project serves. Hand-maintained
# so a change to what a token can reach shows up in a diff, and guarded by
# `test_api_resources_covers_the_live_urlconf`, which walks the real URLconf.
API_RESOURCES = frozenset(
    {
        "accounts",
        "activities",
        "api-settings",
        # Downloading a file attached to a record. Read-only in practice: the
        # only view under this root is the download, and it is gated by the
        # parent record's own read predicate.
        "attachments",
        "auth",
        "boards",
        "business-hours",
        "cases",
        "contacts",
        "custom-fields",
        "dashboard",
        "documents",
        "invoices",
        "leads",
        "macros",
        "notifications",
        "opportunities",
        "org",
        "packs",
        # The customer self-service portal. Listed to keep the vocabulary
        # honest against the live URLconf, not because a non-interactive
        # credential can reach it: every view under this root pins
        # `authentication_classes = (PortalContactAuthentication,)`, which
        # refuses anything that is not a portal token, so a PAT is answered 401
        # and an org API key 403 whatever scopes they carry. The reverse
        # direction is closed in `GetProfileAndOrg`, which refuses a portal
        # token on any path outside `/api/portal/`.
        "portal",
        "profile",
        "public",
        # A profile's own saved list filters (G29).
        "saved-views",
        "schema",
        "search",
        "tags",
        "tasks",
        "teams",
        "time-entries",
        "user",
        "users",
        # Web form management (issue #634). `webforms:write` is a strong scope:
        # a form is an anonymous endpoint that writes leads into the org, so
        # creating one is closer to minting a credential than to editing a
        # record. It is deliberately still a scope rather than an entry in
        # CREDENTIAL_PATHS, because the views underneath it also require
        # `is_org_admin`, so a non-admin's token is refused there regardless of
        # what scopes it carries.
        #
        # The anonymous submit and embed routes are not reachable through this
        # vocabulary at all: they live under `/api/public/`, which is its own
        # resource and takes no credential.
        "webforms",
        # Outbound webhooks. Listed so the vocabulary matches the URLconf, but
        # no scope reaches it: the whole root is in CREDENTIAL_PATHS below.
        "webhooks",
    }
)

SCOPE_ACTIONS = frozenset({"read", "write"})

WILDCARD = "*"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# POSTs that write nothing. The duplicate search on a record that is not saved
# yet (`common.views.duplicate_views.DuplicateCheckView`) takes an email, a
# phone number and a name, and takes them in a body only to keep them out of
# access logs, so it needs the resource's `read` scope. Matched exactly: a path
# with a segment more or less, or without its trailing slash, is a write.
READ_ONLY_POST_PATHS = frozenset(
    {
        "/api/leads/duplicates/",
        "/api/contacts/duplicates/",
        "/api/accounts/duplicates/",
    }
)

# The macro endpoints that act on a ticket. The first path segment scopes them
# as `macros`, which alone let a token holding only `macros:write` change a
# ticket it has no `cases:write` for (create a personal macro, then apply it),
# and let a macros token read a ticket's subject and its contact's name and
# email through render. Render writes no record (it bumps the macro's usage
# counter), so like the duplicate search it is a read. The id segment matches
# whatever the `<uid:pk>` converter routes (any one segment, see
# `common.converters`), not only the canonical UUID spelling, so an id written
# as bare hex or in braces cannot route around the rule. Matched in full.
_MACRO_BY_ID = r"/api/macros/[^/]+/"
MACRO_RENDER_PATH = re.compile(_MACRO_BY_ID + "render/")
MACRO_APPLY_PATH = re.compile(_MACRO_BY_ID + "apply/")

READ_ONLY_POST_PATTERNS = (MACRO_RENDER_PATH,)

# Paths that also need a second resource's scope, on top of their own.
CROSS_RESOURCE_PATHS = (
    (MACRO_RENDER_PATH, "cases:read"),
    (MACRO_APPLY_PATH, "cases:write"),
)

# Read or write access to a credential, from a credential. See the module
# docstring: no scope opens these, and neither does an empty scope list.
CREDENTIAL_PATHS = (
    "/api/profile/tokens/",
    "/api/org/tokens/",
    "/api/org/api-key/",
    # A webhook is a standing export of the org's records to a URL of the
    # caller's choosing, and it keeps sending after the token that created it
    # is revoked. Same persistence argument as minting a token, so webhooks are
    # managed from a signed-in session only.
    "/api/webhooks/",
    # The security audit log records who signed in from where and what was
    # refused. Read from a signed-in admin session only, like webhooks: a
    # leaked token should not also reveal what the org has noticed about it.
    "/api/org/audit-log/",
    # The task calendar feed URL is itself a credential: it reads the member's
    # tasks with no other check, and it keeps working after the token that
    # minted it is revoked. Managed from a signed-in session only.
    "/api/profile/calendar-feed/",
)

# What the organization API key is worth once this module is enforcing. It reads,
# it does not write, and the deny-list above still applies to it. Expressed as a
# scope list so the org key and a read-only PAT go through one code path.
ORG_API_KEY_SCOPES = ("*:read",)


def normalize_scope(raw):
    """Return the canonical form of one scope string, or raise ``ValueError``.

    Used by the create serializer to reject a scope that means nothing, so a
    caller cannot believe they restricted a token when they only misspelled a
    resource. Case and surrounding whitespace are forgiven; anything else is not.
    """
    candidate = (raw or "").strip().lower()
    if not candidate:
        raise ValueError("A scope cannot be empty.")
    parts = candidate.split(":")
    if len(parts) != 2:
        raise ValueError(f"Scope {raw!r} is not in <resource>:<action> form.")
    resource, action = parts
    if resource != WILDCARD and resource not in API_RESOURCES:
        raise ValueError(f"Unknown scope resource {resource!r}.")
    if action not in SCOPE_ACTIONS:
        raise ValueError(f"Unknown scope action {action!r}. Use 'read' or 'write'.")
    return f"{resource}:{action}"


def resource_for_path(path):
    """Return the API root segment for ``path``, or ``None``.

    ``None`` means "no scope can match this", which the matcher turns into a
    denial for any scoped token. That is the fail-closed direction: a path this
    module does not recognise is not a path a restricted token should reach.
    """
    if not path.startswith("/api/"):
        return None
    segment = path[len("/api/") :].split("/")[0]
    return segment or None


def action_for(method, path):
    """``read`` or ``write`` for this request; see THE GRAMMAR above."""
    method = (method or "").upper()
    if method in SAFE_METHODS:
        return "read"
    if method == "POST" and (
        path in READ_ONLY_POST_PATHS
        or any(pattern.fullmatch(path) for pattern in READ_ONLY_POST_PATTERNS)
    ):
        return "read"
    return "write"


def required_scopes(method, path):
    """Every scope this request needs, or ``None`` when no scope can match it."""
    resource = resource_for_path(path)
    if resource is None:
        return None
    needed = [f"{resource}:{action_for(method, path)}"]
    needed += [
        scope for pattern, scope in CROSS_RESOURCE_PATHS if pattern.fullmatch(path)
    ]
    return needed


def _first_missing(held, needed):
    """The first scope in ``needed`` that ``held`` does not grant, else ``None``."""
    for scope in needed:
        action = scope.split(":")[1]
        if scope not in held and f"{WILDCARD}:{action}" not in held:
            return scope
    return None


def _parsed(scopes):
    """Canonicalise a stored scope list, dropping anything unparseable.

    Rows created before `validate_scopes` enforced the vocabulary can hold any
    string at all. Dropping junk (rather than raising) keeps one bad row from
    500-ing every request that token makes, and dropping it (rather than
    ignoring the whole list) keeps junk from ever granting access: a token left
    with no usable scope matches nothing and is refused.
    """
    out = set()
    for raw in scopes or []:
        try:
            out.add(normalize_scope(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


def scopes_allow(scopes, method, path):
    """True when a credential holding ``scopes`` may make this request."""
    if not scopes:
        return True  # unrestricted; see the module docstring
    held = _parsed(scopes)
    if not held:
        return False
    needed = required_scopes(method, path)
    if needed is None:
        return False
    return _first_missing(held, needed) is None


def credential_path_denial(path):
    """Return a denial reason when ``path`` manages credentials, else ``None``."""
    if any(path.startswith(prefix) for prefix in CREDENTIAL_PATHS):
        return (
            "API tokens and organization API keys cannot read or manage other "
            "credentials. Sign in to manage them."
        )
    return None


def check_request(scopes, method, path):
    """Return a denial reason for this request, or ``None`` when it is allowed.

    The single entry point the middleware calls, for both credential kinds.
    """
    denial = credential_path_denial(path)
    if denial is not None:
        return denial
    if not scopes_allow(scopes, method, path):
        needed = required_scopes(method, path)
        if needed is None:
            action = action_for(method, path)
            return f"This token is not scoped for {action} access to this endpoint."
        # `scopes_allow` refused, so some needed scope is missing, or every
        # stored scope was unparseable and the first one needed is named.
        scope = _first_missing(_parsed(scopes), needed)
        resource, action = scope.split(":")
        return (
            f"This token is not scoped for {action} access to {resource}. "
            f"It needs the {scope} scope."
        )
    return None
