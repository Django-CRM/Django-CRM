# Tokens and API keys

Two credential types exist alongside the JWT session described in
[Authentication](authentication.md), for callers that need to authenticate without a browser or an
interactive sign-in: personal access tokens and the per-org API key. This page documents the CRUD
surface for both. For what each one is, how it's resolved, and how its blast radius compares to the
others, see [Architecture: Authentication](../architecture/authentication.md#personal-access-tokens)
and [Security hardening](../self-hosting/security-hardening.md#token-surfaces).

## Personal access tokens

A personal access token (`bcrm_pat_…`, `PersonalAccessToken` in `backend/common/models.py`)
authenticates as the profile that created it and inherits that profile's org and role. Its `scopes`
narrow that down, and are enforced.

### Scopes

A scope is `<resource>:<action>`. `resource` is an API root segment (the first path segment after
`/api/`, so `leads`, `contacts`, `invoices`, `cases`, …) or `*` for all of them. `action` is `read`
or `write`. `read` covers `GET`/`HEAD`/`OPTIONS`; `write` covers `POST`/`PUT`/`PATCH`/`DELETE`,
with one exception. From django-crm 1.13.0 a `POST` to one of the exact paths in
`READ_ONLY_POST_PATHS` counts as `read`: `/api/leads/duplicates/`, `/api/contacts/duplicates/` and
`/api/accounts/duplicates/`, the duplicate search a create form runs, which writes nothing and takes
its email, phone and name in a body only to keep them out of URLs and access logs. The match is
exact: a path with a segment more or less, or without its trailing slash, is still `write`. The
action is decided by method and path, never by anything in the body.

Two macro endpoints act on a ticket, so from django-crm 1.13.0 they need the `cases` scope as well
as their own. `POST /api/macros/{id}/apply/` changes the ticket and needs `macros:write` **and**
`cases:write`; before, a `macros:write` token could create a personal macro and apply it to a
ticket it had no `cases:write` for. `POST /api/macros/{id}/render/` writes no record and returns the
ticket's subject and its contact's name and email, so it counts as `read` and needs `macros:read`
**and** `cases:read`. A token missing either is answered `403` naming the missing scope. `*:write`
covers apply and `*:read` covers render, so the org API key can render but not apply. Both paths
are matched in full, with the id as any single segment the router accepts.

`write` does not imply `read`. A token that may create leads but not list them is a coherent thing
to want, and a scope whose name understates what it grants stops being a boundary.

**An empty scope list means unrestricted.** Every token issued before enforcement existed carries
`[]`, and the behaviour has always been "inherits the owning profile's role", so treating `[]` as
"nothing" would have revoked every live token on deploy. Omit `scopes` and you get exactly the old
behaviour.

Scopes are validated when the token is created. An unknown resource or action is a `400`, because a
caller who misspells a resource would otherwise believe they had restricted a token and receive one
that matches nothing at all.

The vocabulary and the matcher are in `backend/common/scopes.py`; enforcement is in
`GetProfileAndOrg`, the middleware every `/api/` request already passes through, so a new view
cannot forget to opt in. An out-of-scope request is refused with `403` before the view runs:

```json
{"detail": "This token is not scoped for write access to leads. It needs the leads:write scope."}
```

A path outside `/api/<resource>/` names no resource, so the message there is
`"This token is not scoped for <action> access to this endpoint."` with no scope to suggest.

### What no token may do, whatever its scopes

`/api/profile/tokens/`, `/api/org/tokens/`, `/api/org/api-key/`, `/api/webhooks/`,
`/api/org/audit-log/` and, from django-crm 1.13.0, `/api/profile/calendar-feed/` are refused for any
personal access token and for the organization API key, including a token with an empty
(unrestricted) scope list. Sign in to manage credentials. A calendar feed URL is itself a credential
that outlives the token that minted it, so it is on the list for the same reason (see
[Tasks](tasks.md#calendar-feed)).

The reason is that both chains defeat revocation. A token that can mint another token cannot be
revoked, since revoking it leaves whatever it already created working. A token that can read the org
API key upgrades itself into a permanent org-wide credential that outlives its own revocation.

`GET /api/profile/tokens/` (`PersonalAccessTokenListCreateView`,
`backend/common/views/pat_views.py:33-47`) lists the caller's own tokens, filtered on
`profile=request.profile` as well as `org=request.profile.org`, since this table carries no RLS
policy of its own (it's looked up by `token_hash` before any org context exists, so the explicit
filter here is the only tenant boundary, not a belt over RLS's braces):

```json
{
  "error": false,
  "tokens": [
    {
      "id": "<uuid>",
      "name": "reporting script",
      "token_prefix": "bcrm_pat_abc1",
      "scopes": [],
      "expires_at": null,
      "last_used_at": "2026-07-30T12:00:00Z",
      "created_at": "2026-07-01T09:00:00Z",
      "revoked_at": null
    }
  ]
}
```

`token_hash` is never serialized (`PersonalAccessTokenListSerializer`,
`backend/common/serializer.py:1054-1067`, whose `fields` is the exhaustive list above), only
`token_prefix` identifies a token in any listing, on this endpoint or the admin one below.

## Creating and revoking

`POST /api/profile/tokens/` (same view, `:49-69`) creates a token for the caller:

```json
{"name": "reporting script", "scopes": [], "expires_at": null}
```

`name` is required (non-empty, at most 255 characters); `scopes`, if present, must be a list of at
most 32 `<resource>:<action>` strings drawn from the vocabulary above, and is returned
canonicalised (lowercased, deduplicated); `expires_at` is optional, but if present must be in the
future;
`validate_expires_at` rejects a past timestamp with `"expires_at must be in the future."`
(`backend/common/serializer.py:1092-1097`). On success (`201`):

```json
{
  "error": false,
  "id": "<uuid>",
  "name": "reporting script",
  "token_prefix": "bcrm_pat_abc1",
  "scopes": [],
  "expires_at": null,
  "last_used_at": null,
  "created_at": "2026-08-02T10:00:00Z",
  "revoked_at": null,
  "token": "bcrm_pat_abc1...<the full raw token>"
}
```

The `token` field is the only time the raw value is ever returned. It is generated, hashed, and
the plaintext discarded; there is no way to retrieve it again later, only to revoke it and create a
new one. A validation failure returns `400` with `{"error": true, "errors": {...}}`, the same shape
described in [Errors](errors.md#validation-errors).

`DELETE /api/profile/tokens/{id}/` (`PersonalAccessTokenDetailView`, `:72-86`) revokes a token.
Sets `revoked_at`, it does not delete the row. It is scoped to `org=request.profile.org` **and**
`profile=request.profile` in the same lookup, so another user's token id returns `404`, not `403`:
revoking is unavailable to anyone but the token's own owner, and the lookup gives no sign a
different user's token even exists at that id. Revoking an already-revoked token is a no-op that
still returns `200`.

## Organization token oversight

`GET /api/org/tokens/` (`OrgAccessTokenListView`, `backend/common/views/pat_views.py:89-152`) is a
separate, admin-only endpoint: `permission_classes = (IsAuthenticated, HasOrgContext,
IsOrgAdmin)`: that lists every personal access token in the org, not just the caller's own. It is
deliberately kept apart from `profile/tokens/` above rather than folded into it with an
admin-widened filter, so the "a user manages only their own tokens" guard on the self-service
endpoint is never the thing that has to be loosened to support oversight:

```json
{
  "error": false,
  "tokens": [
    {
      "id": "<uuid>",
      "name": "reporting script",
      "token_prefix": "bcrm_pat_abc1",
      "scopes": [],
      "expires_at": null,
      "last_used_at": "2026-07-30T12:00:00Z",
      "created_at": "2026-07-01T09:00:00Z",
      "revoked_at": null,
      "owner": {"id": "<uuid>", "name": "Jane Doe", "role": "USER", "is_active": false},
      "is_live": true
    }
  ],
  "totals": {"count": 12, "live": 9, "orphaned": 2, "unused_90d": 3}
}
```

`owner.is_active: false` with `is_live: true` is the specific case this view exists to surface: a
token belonging to a profile that has since been deactivated. Deactivating a profile invalidates its
tokens as a side effect, not through any separate revocation step,
`resolve_valid_pat` (`backend/common/pat_auth.py:29-46`) checks `pat.profile.is_active` (and
`pat.org.is_active`) on every single use, so the token in the example above cannot actually
authenticate again unless the owner's profile is reactivated first. `"orphaned"` in `totals` counts
exactly these: still unrevoked (`is_live`), but dormant because their owner is gone, which is what an
admin doing offboarding cleanup would revoke explicitly with the endpoint below.

`DELETE /api/org/tokens/{id}/` (`OrgAccessTokenDetailView`, `:155-176`) revokes any token in the
admin's own org, filtered only on `org=request.profile.org` (no `profile=` filter, unlike the
self-service delete above). An admin can revoke a colleague's token, deactivated or not. A pk from
another org still `404`s. Revoking an already-revoked token is a no-op `200`, same as the
self-service endpoint.

## Organization API keys

Each org has exactly one API key (`Org.api_key`), managed through `GET`/`POST /api/org/api-key/`
(`OrgApiKeyView`, `backend/common/views/organization_views.py:244-326`), admin-only in both
directions, checked by hand in `_admin_org_or_error` (`:255-275`) rather than an `IsOrgAdmin`
permission class, but the same rule: `request.profile.role == "ADMIN"` or
`request.profile.is_organization_admin`.

Unlike a personal access token, this key doesn't authenticate as any particular person: a request
bearing it in the `Token` header resolves to the org's first active `ADMIN` profile
(`backend/common/middleware/get_company.py`). The org is always taken from `request.profile.org`,
never from anything client-supplied, so a caller can only ever read or rotate their own org's key.

Two limits apply to every request made with this key, enforced in middleware before any view runs:

- **Read-only.** It is evaluated as the scope list `("*:read",)`, so an unsafe method is `403`,
  apart from the three duplicate-search `POST`s above, which count as `read`.
- **No credential access.** The deny-list above applies, so the key cannot read itself, rotate
  itself, or mint a personal access token owned by the admin whose identity it borrowed.

Set `DJANGO_ORG_API_KEY_AUTH=false` to refuse this key as an authentication method entirely. It is
on by default so an upgrade breaks nothing. Even read-only, one non-expiring key per tenant that
reads every record and cannot be revoked per integration is a blunt instrument; a scoped personal
access token is the better answer for anything new.

`GET /api/org/api-key/`:

```json
{"error": false, "api_key": "<the current key>"}
```

`POST /api/org/api-key/` rotates it, generates a new key and immediately overwrites the old one:

```json
{
  "error": false,
  "message": "API key rotated. The previous key is no longer valid.",
  "api_key": "<the new key>"
}
```

The message is not just cosmetic: the old key stops authenticating the moment this call returns,
with no grace period. A non-admin caller gets `403` with
`{"error": true, "errors": "Only organization admins can access the API key"}` from either method;
a caller with no org context at all gets `400` with
`{"error": true, "errors": "Organization context required"}`.
