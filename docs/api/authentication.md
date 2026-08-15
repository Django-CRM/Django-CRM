# Authentication

This page documents the calls a client makes to sign in and stay signed in: what to send, what
comes back, and what each failure mode means. For the design behind these endpoints. Token
lifetimes, claims, why rotation is implemented by hand, why personal access tokens and organization
API keys exist alongside the JWT. See [Architecture: Authentication](../architecture/authentication.md),
which this page does not repeat or contradict. For a guided walkthrough of picking a sign-in path
locally, see [First sign-in](../getting-started/first-sign-in.md).

All three interactive sign-in paths below, Google OAuth, Google ID token, and both magic-link
verify endpoints, mint tokens through the same `OrgAwareRefreshToken.for_user_and_org()` helper and
are public: `permission_classes = []`, `authentication_classes = []`
(`backend/common/views/auth_views.py`). None of them require an existing token to call.

Everything on this page signs in somebody on **your** side of the org. A customer signing in to the
support portal uses a different set of endpoints and a different credential entirely, and neither
realm's token works against the other; see [Customer portal](customer-portal.md).

## Google OAuth (web)

`POST /api/auth/google/callback/` (`GoogleOAuthCallbackView`,
`backend/common/views/auth_views.py:52-198`) completes a PKCE authorization-code exchange for the
SvelteKit frontend.

Request body:

```json
{
  "code": "<authorization code from Google's redirect>",
  "code_verifier": "<PKCE verifier the frontend generated>",
  "redirect_uri": "<the redirect_uri used in the initial authorize request>"
}
```

`redirect_uri` is taken from this field, not from any server-side setting; there is no
server-side redirect-URI setting to configure. See
[Google OAuth](../self-hosting/google-oauth.md) for the settings that do matter.
The view exchanges these with Google, decodes the returned ID token, and rejects the sign-in
outright if Google's own `email_verified` claim is not `true`. An unverified Google address never
reaches the database. On success:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "user": {"id": "<uuid>", "email": "user@example.com"}
}
```

The token is minted with no org claim (`OrgAwareRefreshToken.for_user_and_org(user, None)`,
`:190`), both Google flows always hand back an org-less token (the magic-link flows differ; see
[Magic links](#magic-links) below), so the client's next call is normally
[switching organization](#switching-organization).

Failure responses: `400` for a missing `code`/`code_verifier`/`redirect_uri`, an invalid ID token,
a missing email, or an unverified email; `502` if Google's token endpoint could not be reached at
all (`requests.RequestException`, `:110-114`); `403` if the resulting user account is deactivated.

## Google ID token (mobile)

`POST /api/auth/google/` (`GoogleIdTokenView`, `backend/common/views/auth_views.py:201-319`) is the
mobile equivalent: the client has already obtained a Google ID token itself and hands it over
directly, rather than a code the server exchanges.

Request body:

```json
{"idToken": "<google id token>"}
```

The server verifies it against Google (`google.oauth2.id_token.verify_oauth2_token`, checked
against `GOOGLE_CLIENT_ID`) and applies the same `email_verified` check as the web flow. On success:

```json
{
  "JWTtoken": "<jwt access token>",
  "refresh_token": "<jwt>",
  "user": {"id": "<uuid>", "email": "user@example.com", "name": "user", "profileImage": ""},
  "organizations": [{"id": "<uuid>", "name": "Acme", "role": "ADMIN"}]
}
```

Note the access token field here is `JWTtoken`, not `access_token`. The two Google flows do not
share a response shape, so a client that supports both cannot read the access token the same way
from each. This response also includes `organizations` directly (the caller's existing memberships),
which the web callback response above does not; a mobile client can offer org selection without an
extra `GET /api/auth/me/` round trip. Failure responses mirror the web flow: `400` for a missing or
invalid `idToken`, a missing email, or an unverified email; `403` for a deactivated account.

## Magic links

Passwordless sign-in, in two deliveries, across three endpoints:

`POST /api/auth/magic-link/request/` (`MagicLinkRequestView`,
`backend/common/views/auth_views.py:586-655`):

```json
{"email": "user@example.com", "delivery": "link"}
```

`delivery` is optional and defaults to `"link"`; the only other value is `"code"`. This endpoint
**always returns `200`** with the same body, regardless of whether the address has an account,
whether it failed validation (a malformed or disposable-domain email), or whether it was rate
limited (five tokens per address per hour), every one of those cases falls through to the same
generic response:

```json
{"message": "If this email is valid, you will receive a sign-in link."}
```

This is deliberate, see [First sign-in](../getting-started/first-sign-in.md#magic-links), and
means a client cannot distinguish "sent" from "rejected" from this response; there is no `400` to
handle here at all. A generated token expires after 10 minutes.

`POST /api/auth/magic-link/verify/` (`MagicLinkVerifyView`,
`backend/common/views/auth_views.py:658-768`) exchanges the token from a link-delivery email:

```json
{"token": "<64-character hex token from the emailed link>"}
```

`POST /api/auth/magic-link/verify-code/` (`MagicLinkVerifyCodeView`,
`backend/common/views/auth_views.py:771-899`) exchanges a code-delivery OTP instead:

```json
{"email": "user@example.com", "code": "123456"}
```

A code is checked under `select_for_update` so two concurrent verify attempts can't both observe
the same attempt count, and is burned (marked used) after 5 failed attempts even if the correct
code is submitted later. Both verify endpoints return the same success shape:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "user": {
    "id": "<uuid>",
    "email": "user@example.com",
    "profile_pic": "",
    "is_active": true,
    "organizations": [{"id": "<uuid>", "name": "Acme", "role": "ADMIN", "is_organization_admin": true, "has_sales_access": true, "has_marketing_access": true}]
  }
}
```

with one addition: if the user has **any** active org membership, the token is minted bound to
one of them and the response also includes:

```json
{"current_org": {"id": "<uuid>", "name": "Acme"}}
```

This is not gated on having exactly one membership; `Profile.objects.filter(user=user,
is_active=True).first()` (`backend/common/views/auth_views.py:740,745-746` for verify, `:874,878-879`
for verify-code) picks whichever active profile sorts first, and `Profile`'s default ordering is
`("-created_at",)` (`backend/common/models.py:238`), so for a user in more than one org this binds
to their **most recently created** membership, not necessarily the one they intend to use next. A
client for a user who might belong to multiple orgs cannot assume magic-link verification always
leaves org selection to a later step. It has to check whether `current_org` is present and let the
user switch away from it if it's the wrong one, via
[switching organization](#switching-organization). If there is no active org membership at all, the
token comes back org-less, same as the OAuth flows.
Failure responses: `400` with `{"error": "Invalid request"}` if the request body itself fails
validation (missing token, malformed email, code not exactly 6 digits); `400` with
`{"error": "Invalid or expired link"}` / `{"error": "Invalid or expired code"}` if the token or code
doesn't match an unused, unexpired row; `403` for a deactivated account. Verifying creates the user
if the email has never signed in before, exactly like the OAuth flows do.

## Using an access token

Send the access token as `Authorization: Bearer <access_token>`. It is valid for one hour. See
[Token model](../architecture/authentication.md#token-model) for why, and what happens after it
expires.

`GET /api/auth/me/` (`MeView`, `backend/common/views/auth_views.py:322-336`) returns the signed-in
user and every organization they have an active profile in:

```json
{
  "id": "<uuid>",
  "email": "user@example.com",
  "profile_pic": "",
  "is_active": true,
  "organizations": [
    {"id": "<uuid>", "name": "Acme", "role": "ADMIN", "is_organization_admin": true, "has_sales_access": true, "has_marketing_access": true}
  ]
}
```

Two things worth knowing about this specific endpoint: it sets `authentication_classes =
[JWTAuthentication]` explicitly (`:327`), so unlike most endpoints it does not also accept a
personal access token or an organization API key, only a JWT works here. And its exact path is one
of a short list `GetProfileAndOrg` skips org-context resolution for entirely
(`backend/common/middleware/get_company.py:29-38`). The middleware returns immediately without
trying to set `request.profile`/`request.org`, so this endpoint never requires an `org_id` claim.
That is what makes it work with the org-less token an org-less sign-in above produces, and exactly
what makes it useful for letting a freshly-authenticated client list organizations to choose from.

## Refreshing

`POST /api/auth/refresh-token/` (`OrgAwareTokenRefreshView`,
`backend/common/views/auth_views.py:339-448`) exchanges a refresh token for a new access/refresh
pair:

```json
{"refresh": "<refresh token>"}
```

```json
{"access": "<new access token>", "refresh": "<new refresh token>"}
```

Note the field names here, `access` and `refresh`, do not match `access_token`/`refresh_token`
used by the sign-in endpoints above; this is the one response on this page that uses the shorter
pair. Failure responses: `400` if `refresh` is missing; `401` if the token is expired, malformed,
or already blacklisted (`TokenError`); `403` if the user account has been deactivated since the
token was issued; `403` if the token carries an `org_id` the user no longer has an active profile
in. See [Refresh and rotation](../architecture/authentication.md#refresh-and-rotation) for what
happens to the old token on a successful call, and why.

## Switching organization

`POST /api/auth/switch-org/` (`OrgSwitchView`, `backend/common/views/auth_views.py:451-583`) moves
an authenticated caller to a different org and re-mints both tokens bound to it. Unlike most
endpoints it only requires `IsAuthenticated`, not org context, because it has to work for a token
that has none yet:

```json
{
  "org_id": "<uuid of an org the caller has an active profile in>",
  "refresh": "<optional: the refresh token being replaced>"
}
```

```json
{
  "access_token": "<jwt bound to org_id>",
  "refresh_token": "<jwt bound to org_id>",
  "current_org": {"id": "<uuid>", "name": "Acme"},
  "profile": {"id": "<uuid>", "role": "ADMIN", "is_organization_admin": true}
}
```

`refresh` is optional; when it is sent and belongs to the caller, it is blacklisted as part of the
switch so the org-less (or previous-org) token stops working immediately rather than staying valid
for the rest of its 14-day life. See
[Organization switching](../architecture/authentication.md#organization-switching) for why the
ownership check on that field matters. Failure responses: `400` if `org_id` is missing or not a
valid UUID; `403` if the caller has no active profile in the requested org.

## Local development

For local development against a running backend with `DEBUG=True`, the fastest way to obtain a
token pair is the `devlogin` management command rather than any of the endpoints above. It needs
no OAuth provider and no outbound email. It refuses to run at all unless `DEBUG` is `True`. See
[First sign-in: Local development](../getting-started/first-sign-in.md) for its exact invocation,
flags, and org-resolution behavior; this page's endpoints are what `devlogin` is a shortcut around,
and the tokens it prints work identically against every endpoint described above.
