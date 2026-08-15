# Authentication

BottleCRM has three separate credential types, a short-lived JWT for interactive clients, a
personal access token for agents and scripts, and a per-org API key for server-to-server
integrations, and this page describes how each one proves identity and how the server decides
what it's allowed to do. The rule underneath all three is the same one stated in
[Multi-tenancy and RLS](multi-tenancy-and-rls.md): identity, org, and role are facts the server
derives and verifies, never values a client supplies. Nothing in a request body, query string, or
custom header changes who you are authenticated as.

## Token model

Interactive sign-in (Google OAuth and passwordless magic-link/OTP) issues a JSON Web Token pair
built on `djangorestframework-simplejwt`. `SIMPLE_JWT` in `backend/crm/settings.py` sets:

```python
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    ...
}
```

Access tokens live one hour; refresh tokens live 14 days. Both are signed with `SECRET_KEY` itself.
There is no separate JWT signing key setting anywhere in this codebase, so rotating `SECRET_KEY`
invalidates every outstanding token immediately. See
[Security hardening](../self-hosting/security-hardening.md#secrets) for the operational
consequences of that. `rest_framework_simplejwt.token_blacklist` is in `INSTALLED_APPS`
specifically so that `RefreshToken.blacklist()` exists to call: without that app installed,
`BLACKLIST_AFTER_ROTATION` would be a silent no-op, which is exactly the kind of failure mode this
codebase's own comment on the `INSTALLED_APPS` entry calls out.

Tokens are minted by `OrgAwareRefreshToken` (`backend/common/serializer.py`), a `RefreshToken`
subclass whose `for_user_and_org(user, org, profile=None)` classmethod embeds org and role claims
directly in the token payload (see [Claims](#claims)) so the frontend doesn't need a follow-up
`/api/auth/profile/` call just to learn which org or role it's operating as.

Two other credential types exist alongside the JWT, covered later on this page:
[personal access tokens](#personal-access-tokens) (`bcrm_pat_…`, self-service, scoped to one
profile) and [organization API keys](#organization-api-keys) (one per org, admin-managed,
equivalent to an admin session). See
[Security hardening](../self-hosting/security-hardening.md#token-surfaces) for how their blast
radii compare.

## Claims

`OrgAwareRefreshToken.for_user_and_org` embeds, beyond simplejwt's own default claims
(`token_type`, `exp`, `iat`, `jti`, and the user id under `USER_ID_CLAIM = "user_id"`):

- `user_email`, `user_name` (derived from the email, since `User` has no first/last name fields),
  `user_profile_pic`
- `org_id`, `org_name`, only when an org is present; a token minted before the caller has picked
  an org (see [Organization switching](#organization-switching)) carries neither
- `org_settings`: `default_currency`, `currency_symbol`, `default_country`
- `role`, only when a `Profile` was supplied, i.e. only once an org is known

`org_id` is the claim that matters most: it's what `GetProfileAndOrg`
(`backend/common/middleware/get_company.py`) reads to resolve `request.org`, and by extension it's
what `RequireOrgContext` uses to set the PostgreSQL session variable every RLS policy checks. See
[Overview](overview.md#request-lifecycle). Critically, the middleware does not simply trust the
claim: it re-validates it against an active `Profile` row on every single request:

```python
profile = Profile.objects.select_related("org").get(
    user_id=user_id, org_id=org_id, is_active=True
)
```

So a token minted while a user was a member of an org keeps working only as long as that
membership stays active. If the membership is revoked after the token was issued, this lookup
raises `Profile.DoesNotExist` and the middleware turns that into a 403 asking the user to log in
again, rather than trusting a claim that's gone stale. `role` in the token is a display
convenience, not the source of truth for authorization, every permission check in
[Permissions and roles](permissions-and-roles.md) reads `request.profile.role` fresh from the
database-backed `Profile` the middleware just re-validated, not from the JWT payload.

## Refresh and rotation

The old documentation for this project asserted refresh-token rotation and server-side tracking
without pointing at code. Here is what the code actually does. `POST /api/auth/refresh-token/`
(`OrgAwareTokenRefreshView`, `backend/common/views/auth_views.py`, wired at
`backend/common/urls.py:58-62`) is the only refresh endpoint. There's no separate route using
simplejwt's stock `TokenRefreshView`, so the `ROTATE_REFRESH_TOKENS`/`BLACKLIST_AFTER_ROTATION`
settings shown above describe the intent, but the actual rotation logic is hand-written in this
view rather than delegated to simplejwt's default serializer:

```python
with transaction.atomic():
    token.blacklist()
    new_token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)
```

The presented refresh token is decoded and validated, its `org_id` claim (if any) is re-checked
against an active `Profile` exactly as in [Claims](#claims) above. A revoked membership gets a 403
here too, not a fresh token, and then, in one atomic transaction, the presented token is
blacklisted and a replacement is minted carrying the same org context. Both writes share a
transaction specifically so a failure can never leave the caller tokenless while the old token
remains usable. This is real rotation with real server-side state: `RefreshToken.blacklist()`
writes to simplejwt's own `OutstandingToken`/`BlacklistedToken` tables (from the
`token_blacklist` app), which is what a stolen refresh token being replayed after the legitimate
client has already refreshed would hit. The blacklisted token fails validation on reuse. Those
bookkeeping rows aren't kept forever; `flush_expired_refresh_tokens`
(`backend/common/tasks.py`) deletes `OutstandingToken` rows once their own `expires_at` has passed,
scheduled nightly. See [Background jobs](background-jobs.md).

An expired or already-blacklisted refresh token fails with `TokenError`, which this view turns into
a 401. A refresh token for a `User` whose account has since been deactivated (`user.is_active`)
also fails, explicitly, with a 403.

## Organization switching

A signed-in user can belong to more than one org, and a single JWT only ever carries one `org_id`.
`POST /api/auth/switch-org/` (`OrgSwitchView`) is how a client moves to a different one: it
requires only `IsAuthenticated` (not `HasOrgContext`), because it has to work for a token that has
no org claim at all yet, the state a fresh Google OAuth login is in, since
`GoogleOAuthCallbackView`/`GoogleIdTokenView` both mint their initial token with `org=None` and
leave org selection to a separate step.

The view re-validates membership the same way the refresh endpoint does;
`Profile.objects.get(user=request.user, org_id=org_id, is_active=True)`, and 403s
(with an audit-logged `permission_denied` entry) if the caller has no active profile in the
requested org. It then optionally retires the refresh token the client is switching away from:

```python
if str(token.get("user_id")) != str(request.user.id):
    return
token.blacklist()
```

That ownership check is not incidental. Without it, `refresh` would let any authenticated caller
blacklist *any* refresh token they could observe, turning a convenience parameter into a remote
logout for someone else's session. Retiring the old token is optional by design (existing clients
don't all send it, and failing the switch over a missing optional field would be worse than letting
one token expire on its own 14-day schedule), but when it is sent and does belong to the caller, it
stops working immediately rather than staying valid against the previous org for the rest of its
life. The org switch itself, retiring the old token and minting the replacement, happens inside
one transaction for the same reason the refresh endpoint's does.

## Personal access tokens

Personal access tokens (`bcrm_pat_…`, `PersonalAccessToken` in `backend/common/models.py`) are
for agents and scripts that need to authenticate as a
specific user without going through the JWT/OAuth flow on every call. A token is generated once
(`PersonalAccessToken.generate(profile, name, ...)`), shown to the caller exactly once, and stored
only as a SHA-256 hash (`token_hash`); the plaintext is not recoverable from the database
afterward.

Resolution happens twice per request, deliberately: `GetProfileAndOrg` resolves the PAT first (so
`request.org` (and, downstream, the RLS context) is set before `RequireOrgContext` runs), and
DRF's `PATAuthentication` class (`backend/common/pat_auth.py`) reuses that same resolved token
(stashed as `request._pat`) rather than looking it up a second time. Both share one validation
function, `resolve_valid_pat`, which checks the token isn't revoked or expired
(`PersonalAccessToken.is_valid()`) and that both the owning profile and its org are still active,
so deactivating a profile cuts off its personal access tokens immediately, with no separate
revocation step.

A token authenticates **as** its owning profile and inherits that profile's role and org in full.
`PersonalAccessToken` has a `scopes` field, but read the model's own comment before assuming it
restricts anything:

```python
# NOTE: scopes are stored for forward-compatibility but are NOT enforced in
# Phase 1: a token always inherits the owning profile's full role/permissions.
# Do not treat `scopes` as a trust boundary until enforcement lands.
```

Treat a personal access token as exactly as powerful as the user who created it. An admin's PAT
can do everything that admin can do through the UI. Self-service management lives at
`profile/tokens/` (a user manages only their own); admin oversight of every token in the org,
including a deactivated colleague's, is a deliberately separate, admin-only endpoint at
`org/tokens/`, so the self-service guard is never the thing that has to be loosened to support
oversight. See [Security hardening](../self-hosting/security-hardening.md#token-surfaces) for that
distinction in more depth.

## Organization API keys

Each `Org` has exactly one API key (`Org.api_key`, generated at creation), managed through
`GET`/`POST /api/org/api-key/` (`OrgApiKeyView`), admin-only in both directions. Unlike a personal
access token, this key doesn't authenticate as any particular person: `GetProfileAndOrg`'s
`_process_api_key_auth` resolves a request bearing this key (in a `Token` header) to the org's
**first active `ADMIN` profile**:

```python
profile = Profile.objects.filter(
    org=organization, role="ADMIN", is_active=True
).first()
```

Borrowing a real person's profile is what made this key so dangerous: it never expires, there is one
per tenant so it cannot be revoked per integration, and until scope enforcement landed it could do
anything that admin could, including delete records and mint a personal access token owned by them.

Two limits now apply, both in middleware, before any view runs:

- **It is read-only.** Any unsafe method (`POST`, `PUT`, `PATCH`, `DELETE`) is refused with 403. It
  is evaluated as the scope list `("*:read",)`, so the org key and a read-only personal access token
  go through one implementation (`common/scopes.py`).
- **It cannot reach a credential.** `/api/profile/tokens/`, `/api/org/tokens/` and
  `/api/org/api-key/` are on a deny-list that no scope satisfies, so the key cannot read itself,
  rotate itself, or create a token owned by the admin whose identity it borrowed.

`common.external_auth.APIKeyAuthentication` carries the same two gates. It is an independent second
copy of the key resolution, reached when DRF authenticates a request the middleware skipped, so
guarding one and not the other would leave the DRF layer admitting what the middleware refuses.

`DJANGO_ORG_API_KEY_AUTH=false` turns the whole path off, on every endpoint. It is left on by
default so an upgrade breaks nothing; a deployment whose integrations have all moved to personal
access tokens should set it. Even read-only, this key reads every record in the org, so it remains
the bluntest of the three credential types on this page. Prefer a scoped personal access token.

Rotating it (`POST /api/org/api-key/`) invalidates the previous key immediately, and the response
says so. It's excluded from every nested API representation (`OrganizationSerializer` never embeds
it) and only ever served by this one endpoint, which now requires an interactive session.

## Customer portal credentials

Everything above authenticates somebody on **your** side: a member of the org, or a script acting
with a member's authority. The customer portal (`/api/portal/`) authenticates somebody on the other
side of the ticket, and it is a separate realm on purpose. Its principal is a `Contact`, which is a
record in your CRM rather than an account in your system: no `User` row, no `Profile`, no role, no
org membership.

`mint_portal_token` (`backend/common/portal_auth.py`) issues a 24 hour access token carrying
`typ: "portal"`, `org_id` and `contact_id`. There is no refresh token, so rotation, refresh
revocation and privilege-change invalidation are absent from this surface entirely. Those are the
parts of an auth system that are hardest to get right and worst to get wrong, and a portal visited a
few times per ticket does not need them.

The boundary between the two realms is enforced in three independent places, because a boundary
that depends on every future author remembering it is not a boundary:

1. **`PortalContactAuthentication` is never in `DEFAULT_AUTHENTICATION_CLASSES`.** That setting is
   global, so anything listed there authenticates every view in the project. Portal views name the
   class explicitly instead, which means adding an endpoint that accepts a customer is a deliberate
   act.
2. **The principal reports `is_authenticated = False`.** If a portal token ever does reach an
   internal view, DRF's own `IsAuthenticated` denies it by construction. It reports
   `is_anonymous = True` for the matching reason, which also makes `BaseModel.save` leave
   `created_by` alone rather than trying to stamp a `Contact` into a `User` foreign key.
3. **`GetProfileAndOrg` refuses a portal token on any path outside `/api/portal/`,** before any view
   runs, with a 403. A view written later cannot opt out of this by forgetting a permission class.

Traffic in the other direction needs no special handling. A portal token is minted without a
`user_id` claim, so SimpleJWT's `get_user` refuses it on its own, and a personal access token, an
organization API key and an internal JWT are each refused at the portal. All three directions have
tests.

Sign-in itself is a per-org emailed code, deliberately in its own `PortalLoginToken` table rather
than as a nullable column on `MagicLinkToken`. Sharing that table would mean one verify endpoint
accepting the other realm's credential, and that is a customer signing in to your internal
application. Roughly forty duplicated lines buys a boundary a single mistake cannot cross. See
[API: Customer portal](../api/customer-portal.md) for the endpoints.

## Related configuration

One piece of related configuration to be aware of: there is no redirect-URI setting. The
redirect URI used in the Google OAuth code exchange (`GoogleOAuthCallbackView.post`) comes from the
client's request body on each call (`request.data.get("redirect_uri")`). A `GOOGLE_REDIRECT_URI`
setting used to exist in `backend/crm/settings.py`, defined and never read; it was removed. See
[Google OAuth](../self-hosting/google-oauth.md) for the settings that do matter.
