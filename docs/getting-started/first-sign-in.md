# First sign-in

BottleCRM issues sessions as JWT access/refresh pairs (`rest_framework_simplejwt`), not
server-side sessions or cookies set by a traditional username/password login endpoint. There is
no such endpoint. `backend/common/urls.py` registers exactly three ways to obtain a token pair.

## The three sign-in paths

| Path | Where it's implemented | Typical use |
|---|---|---|
| `devlogin` management command | `backend/common/management/commands/devlogin.py` | Local development only |
| Google OAuth | `GoogleOAuthCallbackView` (web, PKCE) and `GoogleIdTokenView` (mobile ID token), both in `backend/common/views/auth_views.py` | Self-hosted deployments with Google OAuth configured |
| Magic links (passwordless email) | `MagicLinkRequestView`, `MagicLinkVerifyView`, `MagicLinkVerifyCodeView`, all in `backend/common/views/auth_views.py` | Any deployment. Needs only outbound email |

All three ultimately mint tokens through the same `OrgAwareRefreshToken.for_user_and_org()` helper,
so the resulting access/refresh pair behaves identically regardless of which path produced it.

## Local development: devlogin

`devlogin` mints an access/refresh token pair for an existing user directly from the command
line, with no browser round-trip and no OAuth provider involved. It **refuses to run unless
`settings.DEBUG` is `True`**. The very first thing the command does is raise a `CommandError` if
`DEBUG` is falsy. That guard is also what makes the command safe to ship in the same codebase that
runs in production: `DEBUG=False` there, and the command becomes a no-op.

Invocation:

```bash
uv run python manage.py devlogin <email> --org <name-or-uuid>
```

Run from `backend/` (or, against a Docker stack, as
`docker compose exec backend python manage.py devlogin <email> --org <name>`).

It prints three things: an **access token**, a **refresh token**, and, when `--org` is given,
the **org's UUID** it bound the token to.

A few specifics worth knowing:

- `--org` accepts either a name or a UUID, and resolves by **id first, then name**: it tries an
  exact `Org.objects.get(id=org_arg)` lookup first, and only if that fails, not found, or the
  string isn't a valid UUID at all (`Org.objects.get(id=...)` raises Django's `ValidationError`,
  not `ValueError`, for a non-UUID string, so both are caught alongside `Org.DoesNotExist`),
  falls back to matching on `Org.name`. If more than one org shares that name, it refuses to guess
  and asks you to pass an ID instead.
- The target **user must already have an active `Profile` in that org**; `devlogin` does not
  create one. If it doesn't find one, it raises a `CommandError` naming the missing
  user/org pair rather than silently proceeding without org context. (Passing `--create` will
  create the *user* if it doesn't exist yet, but that's independent of the profile requirement.
  A freshly created user still needs a `Profile` in the org before `--org` will succeed for them.)
- A token minted with `--org` **already carries the `org_id` claim**, so there's no follow-up
  org-switch call needed. The frontend can use it immediately. Omit `--org` and you get a token
  with no org bound; you'd then need to pick one interactively (see
  [Choosing an organization](#choosing-an-organization) below) before the app has anything to show.

### Using the tokens in the browser

The snippet `devlogin` prints (`localStorage.setItem('access_token', ...)`,
`localStorage.setItem('refresh_token', ...)`) only writes `localStorage`, correct for
`frontend/src/lib/api.js`'s own `STORAGE_KEYS`, which is what the API client reads for direct,
client-side fetches, but **not enough on its own to sign you in through the SvelteKit app**.
`frontend/src/hooks.server.js` authenticates every server-rendered request from three **cookies**
instead (`event.cookies.get('jwt_access')`, `('jwt_refresh')` and `('org')`), and nothing
server-side reads `localStorage` at all. Without those cookies set, `hooks.server.js` treats the
request as signed out and redirects to `/login`, no matter what `localStorage` holds.

To get past the guard, open the devtools console on `http://localhost:5181`, run the printed
`localStorage.setItem(...)` snippet, and set the matching cookies yourself:

```js
const oneYear = 60 * 60 * 24 * 365;
document.cookie = `jwt_access=${ACCESS_TOKEN}; path=/; max-age=${oneYear}; samesite=lax`;
document.cookie = `jwt_refresh=${REFRESH_TOKEN}; path=/; max-age=${oneYear}; samesite=lax`;
document.cookie = `org=${ORG_UUID}; path=/; max-age=${oneYear}; samesite=lax`;
```

Substitute the printed access token, refresh token, and (if you passed `--org`) org UUID for
`ACCESS_TOKEN` / `REFRESH_TOKEN` / `ORG_UUID`; skip the `org` cookie entirely if you omitted
`--org`. Then reload. The root route `/` **is** the dashboard;
`frontend/src/routes/(app)/+page.svelte` sits directly at the route group's root. There is no
`/dashboard` route anywhere in the route tree.

## Google OAuth

Two endpoints handle Google sign-in, for two different clients:

- **`POST /api/auth/google/callback/`** (`GoogleOAuthCallbackView`), the web flow, used by the
  SvelteKit frontend. It's a PKCE authorization-code exchange: the frontend sends `code`,
  `code_verifier` and `redirect_uri`; the backend exchanges them with Google for tokens, decodes
  the ID token payload, and requires `email_verified` to be true before it will create or sign in
  a user. An unverified Google email is rejected outright.
- **`POST /api/auth/google/`** (`GoogleIdTokenView`). The mobile flow. The client sends an
  already-obtained Google ID token (`idToken`); the backend verifies it against Google
  (`google.oauth2.id_token.verify_oauth2_token`) rather than doing a code exchange, and applies the
  same `email_verified` check.

Both paths get-or-create a `User` by email, reject sign-in for a deactivated account
(`user.is_active is False`), backfill the user's name from the Google profile if it wasn't set
yet, and mint an org-unbound token (no `--org` equivalent here. See
[Choosing an organization](#choosing-an-organization)). Google OAuth is only usable once
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are configured; in the Docker quick start's
`.env.docker` both are blank, which disables it.

## Magic links

Passwordless sign-in by email, in two deliveries:

- **Link delivery**: `POST /api/auth/magic-link/request/` creates a token, emails a sign-in link,
  and `POST /api/auth/magic-link/verify/` (`MagicLinkVerifyView`) exchanges the token in that link
  for an access/refresh pair.
- **Code delivery**: the same request endpoint, with `delivery: "code"`, instead emails a 6-digit
  one-time code. `POST /api/auth/magic-link/verify-code/` (`MagicLinkVerifyCodeView`) checks the
  code (max 5 attempts before the code is burned) and returns tokens. This is the mobile-friendly
  variant, no link-tap round-trip through a browser.

The request endpoint always returns `200` with a generic "if this email is valid…" message,
whether or not the address has an account. This is deliberate, to avoid leaking which emails are
registered. Requests are rate-limited to 5 tokens per email per hour, and each token expires 10
minutes after it's issued. Verifying creates the user if the email doesn't exist yet (same
deactivated-account check as the OAuth paths applies), and, like OAuth, mints an org-unbound
token unless the user already has an active profile in exactly one org, in that case the
response includes that org's tokens and a `current_org` field directly.

In a self-hosted deployment, magic links require a working outbound email backend; in the Docker
quick start's dev configuration, email is printed to the container's stdout instead of actually
sent (a console backend), so you'd read the link or code from the `backend` container logs.

## Choosing an organization

A token minted without an org (any sign-in path except `devlogin --org`) still needs one before
the app has anything to show. Every org-scoped view requires org context. The SvelteKit frontend
handles this at its `/org` route: it calls `GET /api/auth/me/` to list the organizations the
signed-in user has an active profile in, and lets them pick one. Selecting an org calls
`POST /api/auth/switch-org/` (`OrgSwitchView`) with the chosen `org_id`; the backend verifies the
user has an active `Profile` in that org and returns a fresh access/refresh pair with the org
claim bound in, the same shape `devlogin --org` produces directly. If the presented refresh token
is also sent, the backend blacklists it as part of the switch so the old, org-less token can't be
reused afterward.

The same `/api/auth/switch-org/` endpoint is what a user with access to multiple organizations
uses later to move between them. It isn't only a first-sign-in step.
