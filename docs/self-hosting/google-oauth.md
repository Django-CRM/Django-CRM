# Google OAuth

BottleCRM supports signing in with Google from both the web app (an authorization-code exchange
with PKCE) and the mobile app (a Google ID token posted directly to the backend). Both are optional:
the deployment works without them, using [magic-link email sign-in](../getting-started/first-sign-in.md)
instead, until you configure a Google OAuth client.

## Registering an OAuth client

Create an OAuth 2.0 Client ID of type "Web application" in the Google Cloud Console. The redirect
URI it needs authorized is built by the frontend's login page
(`frontend/src/routes/(no-layout)/login/+page.server.js`) as `${GOOGLE_LOGIN_DOMAIN}/login`;
`GOOGLE_LOGIN_DOMAIN` is a **frontend** environment variable (`frontend/.env.example` defaults it to
`http://localhost:5181`), so the value to register with Google is `http://localhost:5181/login` for
local development, or `https://<your-frontend-host>/login` in production. There is no separate
redirect registration needed for the mobile flow. It doesn't use a redirect at all (see
[Mobile ID tokens](#mobile-id-tokens) below).

The frontend needs its own copy of the client ID; `frontend/.env.example` sets `GOOGLE_CLIENT_ID`
(client ID only; the comment in that file is explicit that "secret is on backend"), because it
builds the Google authorization URL client-side (`client_id`, `redirect_uri`, PKCE `code_challenge`,
and a CSRF `state` parameter). The client secret only ever lives on the backend.

## Backend configuration

`backend/crm/settings.py` reads two Google-related variables, both defaulting to an empty string:

```python
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
```

They are what the backend sends to Google's own token endpoint when exchanging an authorization code
(see below) and what it uses as the expected audience when verifying a mobile ID token. Leaving
either blank means every such exchange or verification fails, so sign-in with Google is effectively
off until both are set. `.env.docker` and `.env.example` both ship them blank, under an "optional.
Leave blank to disable" comment.

There is deliberately no redirect-URI setting. The redirect URI used in the token exchange is the
one the frontend sends in the request body on each call (see
[The PKCE callback flow](#the-pkce-callback-flow)). A `GOOGLE_REDIRECT_URI` setting did exist for a
while, read into Django and referenced nowhere, which cost operators time configuring something
inert; it was removed rather than documented.

## The PKCE callback flow

The web flow is the standard OAuth Authorization Code flow with PKCE, split across the frontend and
`GoogleOAuthCallbackView`:

1. `login/+page.server.js` generates a PKCE `code_verifier`/`code_challenge` pair and a random
   `state`, stores the verifier and state in short-lived httpOnly cookies, and redirects the browser
   to Google's authorization endpoint with the `code_challenge`.
2. Google redirects back to `/login?code=...&state=...`. The frontend checks the returned `state`
   against the cookie (CSRF protection) and reads back the stored `code_verifier`, then makes a
   server-to-server call: `POST /api/auth/google/callback/`
   (`GoogleOAuthCallbackView`, wired at `backend/common/urls.py:67`), with `code`, `code_verifier`
   and `redirect_uri` in the body.
3. `GoogleOAuthCallbackView` exchanges the code for tokens directly with Google
   (`https://oauth2.googleapis.com/token`), sending `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, the
   `redirect_uri` and `code_verifier` it received. This is the step that requires the client secret,
   which is why it has to happen on the backend rather than in the browser.
4. The view decodes the `id_token` Google returns (its own code comment notes this is safe without a
   signature check specifically because the token came directly from Google over HTTPS in the
   previous step, not from an untrusted client) and requires `email_verified` to be true before
   trusting the email address at all, an unverified address never provisions or signs in to an
   account.
5. It gets-or-creates a `User` by email, rejects with 403 if the account is deactivated
   (`user.is_active`), and issues BottleCRM's own JWT pair via
   `OrgAwareRefreshToken.for_user_and_org(user, None)`. The `None` org means this token carries no
   `org_id` claim: the user still has to pick an organization ([Org
   switching](../getting-started/first-sign-in.md#choosing-an-organization)) before any org-scoped
   request will work.

## Mobile ID tokens

The mobile app skips the redirect entirely: Google Sign-In on the device returns a Google-issued ID
token, and the app posts it straight to `POST /api/auth/google/`
(`GoogleIdTokenView`, wired at `backend/common/urls.py:69`) as `{"idToken": "..."}`. Unlike the web
callback above, this view does cryptographically verify the token, `google.oauth2.id_token.
verify_oauth2_token(id_token_str, google_requests.Request(), settings.GOOGLE_CLIENT_ID)`, checking
the signature against Google's public keys and the audience against the backend's own
`GOOGLE_CLIENT_ID`, so a token issued for a different client is rejected. It applies the same
`email_verified` check and inactive-account check as the web flow, then returns `JWTtoken`,
`refresh_token` and the list of organizations the user already has a `Profile` in
(`{"id", "name", "role"}` per org) so the mobile app can present an org picker without a second
round trip.
