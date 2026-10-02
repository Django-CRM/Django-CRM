# Environment variables

This page is the exhaustive table: every environment variable `backend/crm/settings.py` reads,
its real default, and what it's for. [Self-hosting: Environment variables](../self-hosting/environment-variables.md)
is the narrative companion. How configuration loading actually works (`load_dotenv()`, the
`.env.docker` / `.env.docker.local` layering), and the smaller, curated list of variables an
operator actually has to touch to run a working, non-default install. Read that page first if
you're setting up a deployment; come back here when you need the complete list or an exact default.

## How to set them

Every variable in the table below is read with `os.environ.get("NAME", <default>)` (or the
equivalent `os.getenv`) directly in `crm/settings.py`, at process import time: `manage.py
runserver`, `migrate`, a Gunicorn/Uvicorn worker, a Celery worker, and every management command all
read the same values the same way. A `.env` file next to `manage.py` (for a manual install) or
`.env.docker`/`.env.docker.local` (for the Docker Compose stack) is loaded into the process
environment before any of these calls run, so an unset variable falls back to the default in the
**Default** column rather than crashing immediately. That's true even for `SECRET_KEY`: its own
`os.environ.get` call always succeeds, but the value it produces then feeds a separate check a few
lines later that raises `ValueError` once `ENV_TYPE` is anything other than `dev` (see the
`SECRET_KEY` row below): a default that satisfies `os.environ.get` doesn't necessarily satisfy
everything downstream of it. The variables with genuinely no default in code. Read with plain
`os.environ[...]`, so a missing one is an immediate `KeyError` rather than a fallback. Live outside
`crm/settings.py` entirely and are covered in the list below instead of the table.

Three groups of real, project-read environment variables are **not** read by `crm/settings.py`
itself, so they aren't in the table below:

- **`ADMIN_PASSWORD`** is read directly by the `create_default_admin` management command (see
  [Management commands](management-commands.md#create_default_admin)). It still gets its own row
  below because it's part of the same Docker bootstrap flow as `ADMIN_EMAIL`, which *is* read in
  `settings.py`.
- **`AWS_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `SENTRY_DSN`** are read with
  plain `os.environ[...]` (no default) inside `backend/crm/server_settings.py`, which
  `crm/settings.py` only imports when `ENV_TYPE=prod`. A missing one is a `KeyError` at import
  time. The process refuses to start rather than starting half-configured. `ENV_TYPE` itself
  follows the same rule: it's read in `settings.py`, but it's the internal dev/prod switch this
  project's own tests exclude from the table by design, not a self-hoster-facing setting on its
  own. See [Production deployment](../self-hosting/production-deploy.md#required-settings) and
  [Security hardening](../self-hosting/security-hardening.md#secrets) for what `ENV_TYPE=prod`
  actually requires and why it shouldn't be set casually.
- **`BCRM_MCP_ENABLED`** and **`BCRM_BASE_URL`** are no longer read anywhere. They gated the
  MCP server that used to be mounted at `/mcp`, which has been removed in favour of the REST API.
  Both are safe to delete from an existing deployment's environment. See
  [AI agents](../integrations/ai-agents.md) for how an agent connects now.

## Reference

### Django core

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `SECRET_KEY` | `django-insecure-dev-key-please-change-in-production` | Yes, once `ENV_TYPE` is anything other than `dev` (see above) | Django's cryptographic signing key. It also *is* the JWT `SIGNING_KEY`: `SIMPLE_JWT["SIGNING_KEY"]` is set to `SECRET_KEY` directly, so there is no separate JWT signing key setting anywhere in this codebase, and rotating `SECRET_KEY` invalidates every outstanding access and refresh token. **It must be at least 32 bytes.** HS256 signs the JWTs, and RFC 7518 section 3.2 requires an HMAC key at least as long as the hash output; a shorter one raises at import time outside `dev` and warns inside it. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. See [Architecture: Authentication](../architecture/authentication.md#token-model) and [Security hardening](../self-hosting/security-hardening.md#secrets). |
| `DEBUG` | `False` | No | Django debug mode. Also the sole gate on the `devlogin` management command. See [Management commands](management-commands.md#devlogin). |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Yes, once serving traffic under a real hostname | Comma-separated list, split on `,` into Django's `ALLOWED_HOSTS`. |
| `TRUST_PROXY_SSL_HEADER` | `False` | Yes, if TLS is terminated by a proxy in front of Django | Set to `true` (case-insensitive) to set Django's `SECURE_PROXY_SSL_HEADER` to `("HTTP_X_FORWARDED_PROTO", "https")`. Without it, a request arriving from nginx or an ALB over plain HTTP makes `request.is_secure()` false, and `SecurityMiddleware` then emits **no** `Strict-Transport-Security` header at all, so the `SECURE_HSTS_SECONDS` / `INCLUDE_SUBDOMAINS` / `PRELOAD` settings are silently inert. Only turn it on when that proxy is the sole route to the app **and** it overwrites `X-Forwarded-Proto` rather than passing through a client-supplied one, because otherwise any caller reaching Django directly can make `request.is_secure()` return true. The http-to-https redirect belongs on the proxy; `SECURE_SSL_REDIRECT` is deliberately not set. |
| `NUM_PROXIES` | unset | Only when `REMOTE_ADDR` is the proxy (gunicorn behind nginx: `1`). Leave unset under uvicorn `--proxy-headers` | How many proxies in front of Django append to `X-Forwarded-For` (nginx with `$proxy_add_x_forwarded_for` is one). Sets DRF's `NUM_PROXIES`, read by `common.request_meta.client_ip`, which supplies every recorded client IP (security audit log, web form submissions, estimate acceptance, magic-link and portal sign-in tokens) and every per-IP throttle (web form submit, help center, calendar feed, magic-link and portal sign-in). It uses the entry that many places from the right, the address the outermost proxy saw; entries to its left are whatever the caller sent and are ignored, so rotating the header neither picks a throttle bucket nor forges a recorded address. Unset or `0` uses `REMOTE_ADDR`. That is correct when the app server already resolves the client from trusted proxies, as uvicorn does with `--proxy-headers --forwarded-allow-ips=127.0.0.1`; setting `NUM_PROXIES` on top of that counts the proxy twice. Either way a request a SvelteKit server relays for a visitor arrives from the relay; `RELAY_SECRET` is what tells those visitors apart. When `REMOTE_ADDR` is the proxy itself (gunicorn behind nginx), unset means every visitor shares one throttle bucket, so set it to the number of proxies. Set it higher than the real number of proxies and callers can choose the IP again. |
| `RELAY_SECRET` | unset | Recommended wherever a SvelteKit server relays anonymous visitors (the web app's sign-in pages, help center and estimate portal, the marketing site's contact form) | Shared secret with those SvelteKit servers. A request whose `X-BottleCRM-Relay-Secret` header matches it (constant-time compare) is believed about the visitor address in `X-BottleCRM-Client-IP`, so each visitor gets their own per-IP throttle bucket and recorded address instead of the relay's. Without a match both headers are ignored and `NUM_PROXIES` decides as above; a visitor address that is not an IP falls back the same way. Set the same value as `RELAY_SECRET` in the web app's environment (private, never `PUBLIC_`), and run that app with `ADDRESS_HEADER=X-Forwarded-For` and `XFF_DEPTH=1` behind nginx so `getClientAddress()` is the visitor rather than nginx, and with `HOST=127.0.0.1` so it cannot be reached around nginx with a forged `X-Forwarded-For`. The relays also send the visitor in `X-Forwarded-For` for APIs before 1.12.0, which read its first entry; this release ignores that header unless `NUM_PROXIES` is set. At least 32 characters: `crm/settings.py` refuses to start with a shorter value, and `client_ip` never matches one. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Anyone holding it can choose the address they are throttled and recorded as, so treat it like a password; its header is scrubbed from Sentry events. |
| `DJANGO_ORG_API_KEY_AUTH` | `true` | No | Set to `false`, `0`, `no` or `off` to refuse the organization API key (`Token: <org.api_key>`) as an authentication method entirely, on every endpoint. The key is one non-expiring credential per tenant that resolves to an arbitrary active ADMIN, so it cannot be revoked per-integration and it reads every record in the org. It is read-only and barred from the credential endpoints even when enabled (see [Tokens and API keys](../api/tokens-and-api-keys.md)), but a deployment whose integrations have all moved to personal access tokens should turn it off. Left on by default so an upgrade breaks nothing. |

### Database

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `DBNAME` | `crm_db` | No | PostgreSQL database name. |
| `DBUSER` | `postgres` | Yes, before anyone but you can reach the instance | PostgreSQL role. The default is the PostgreSQL superuser account on most installations, and a superuser silently bypasses every RLS policy this project relies on for tenant isolation. See [PostgreSQL and RLS: Creating the application role](../self-hosting/postgresql-and-rls.md#creating-the-application-role). |
| `DBPASSWORD` | `postgres` | Yes, alongside `DBUSER` | PostgreSQL password. |
| `DBHOST` | `localhost` | No | PostgreSQL host. |
| `DBPORT` | `5432` | No | PostgreSQL port. |

### Connection pooling

Pooling uses psycopg 3's `psycopg_pool` through Django's PostgreSQL backend. It is **off by default**, so upgrading does not silently change an existing deployment's connection behaviour. Turning it on also enables `CONN_HEALTH_CHECKS`; `CONN_MAX_AGE` is pinned to `0`, because Django raises `ImproperlyConfigured` ("Pooling doesn't support persistent connections") if a pool is configured alongside persistent connections.

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `DB_POOL_ENABLED` | `False` | No | `"true"` (case-insensitive) puts a `psycopg_pool.ConnectionPool` in front of the default database and installs the RLS reset callback described below. Anything else leaves the connection behaviour unchanged. |
| `DB_POOL_MIN_SIZE` | `2` | No | Connections the pool keeps open per process even when idle. |
| `DB_POOL_MAX_SIZE` | `10` | No | Ceiling on concurrent connections per process. This is the limit that was missing when the API exhausted PostgreSQL: under ASGI every in-flight request otherwise takes its own connection with no upper bound. |

**Size this against `max_connections`, and remember the pool is per process, not per host.** Peak usage is roughly `DB_POOL_MAX_SIZE × (uvicorn workers + Celery prefork children)`, plus `DB_POOL_MIN_SIZE` held idle by each of those processes. `docker-compose.yml` runs `celery -A crm worker` with no `--concurrency`, so each worker forks one child per CPU. On an 8-core host with 3 uvicorn workers and the defaults, that is `10 × (3 + 8) = 110` connections at peak against PostgreSQL's default `max_connections` of 100.

**Why the reset callback matters.** RLS context lives in `app.current_org`, a session-scoped setting that outlives the transaction and the request. `psycopg_pool` does not clear session state when a connection is returned; it only rolls back an open transaction. A pool without `common.rls.pool.reset_rls_context` therefore hands the next borrower a connection still scoped to the previous tenant. Enabling `DB_POOL_ENABLED` wires that callback in automatically, and `common/tests/test_pool_rls_isolation.py` proves both that it clears the context and that it leaves the connection in the `IDLE` state `psycopg_pool` requires. Do not configure a pool by hand without it.

### Email

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` | No | Django email backend. The default prints outbound mail to stdout instead of sending it. Setting `ENV_TYPE=prod` overrides this unconditionally to `django_ses.SESBackend`, regardless of what this variable is set to. See [Email and Celery](../self-hosting/email-and-celery.md#amazon-ses). |
| `DEFAULT_FROM_EMAIL` | `noreply@localhost` | No | `From` address on outbound mail. |
| `ADMIN_EMAIL` | `admin@localhost` | No | Assigned to `settings.ADMIN_EMAIL` but not consumed anywhere else in `settings.py` itself. The one place it's read outside this module and `create_default_admin` (see below) is `common/views/user_views.py`, which returns it as `admin_email` in the user-list API response context. `create_default_admin` also reads `ADMIN_EMAIL` a second time, independently and with the same default, to name the bootstrap Django superuser account. |
| `AWS_SES_REGION_NAME` | `ap-south-1` | No, unless `ENV_TYPE=prod` | Only read when `"django_ses"` appears in `EMAIL_BACKEND`. True if you set `EMAIL_BACKEND=django_ses.SESBackend` directly, or unconditionally true once `ENV_TYPE=prod` forces that backend. Under `ENV_TYPE=prod`, `server_settings.py` requires this variable outright (`os.environ[...]`, no default) before `settings.py`'s own conditional read ever runs, so the default above is effectively unreachable in that mode. |
| `AWS_SES_REGION_ENDPOINT` | `email.<AWS_SES_REGION_NAME>.amazonaws.com` | No, unless `ENV_TYPE=prod` | Same condition and the same `ENV_TYPE=prod`-makes-it-required caveat as `AWS_SES_REGION_NAME` above. |

### Celery and Redis

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Yes, before anyone but you can reach the instance | Redis URL Celery uses as its message broker. |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Yes, alongside `CELERY_BROKER_URL` | Redis URL Celery uses to store task results. |

### Cache and rate limiting

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `CACHE_URL` | *(empty, falls back to a per-process `LocMemCache`)* | Yes, if you run more than one worker and want rate limiting to be real | Redis URL for Django's default cache, e.g. `redis://localhost:6379/1`. DRF throttling stores its counters here. Left unset, each Gunicorn worker keeps its own counters, so the effective limit is roughly the configured rate times the worker count and it resets on every restart. |
| `WEBFORM_THROTTLE_IP` | `10/hour` | No | Rate limit for public web form submissions, per client IP per form. The client IP is `common.request_meta.client_ip` (see `NUM_PROXIES`), which a caller cannot choose, but a sender with many addresses gets many buckets, so treat this as the layer that stops ordinary flooding rather than a determined attacker. |
| `WEBFORM_THROTTLE_GLOBAL` | `200/day` | No | Rate limit for public web form submissions, per form across all clients. No number of addresses evades this one, so it is the real backstop. Raise it for a form on a high-traffic page. |
| `HELP_CENTER_THROTTLE_IP` | `600/hour` | No | Rate limit for the public help center API (`/api/public/help/`), per client IP. With `RELAY_SECRET` set on the API and the web app, the web app sends each visitor's address signed, so each reader and crawler gets its own bucket; without it they all share the web app's. The IP is `common.request_meta.client_ip`; the pages it protects are public by the org's choice. |
| `HELP_CENTER_THROTTLE_GLOBAL` | `10000/hour` | No | Rate limit for the public help center API, per help center across all clients. No number of addresses evades this one, so it is the backstop behind `HELP_CENTER_THROTTLE_IP`. It is bucketed per help center, so traffic to one org's pages never limits another's. Raise it for a busy help center. |
| `CALENDAR_FEED_THROTTLE_IP` | `1000/hour` | No | Rate limit for the task calendar feed (`/api/public/calendar/<token>.ics`), per client IP. Generous because Google, Microsoft and Apple fetch subscribed calendars from shared addresses that carry many users' feeds at once. The IP is `common.request_meta.client_ip`. |
| `MAGIC_LINK_THROTTLE_IP` | `30/hour` | No | Rate limit for magic-link sign-in requests (`/api/auth/magic-link/request/`), per client IP. The web sign-in page calls the API from its server, so without `RELAY_SECRET` every web sign-in shares one bucket; the default leaves room for that and for an office behind one NAT. Each address is separately capped at five links an hour. |
| `MAGIC_LINK_THROTTLE_GLOBAL` | `300/hour` | No | Rate limit for magic-link sign-in requests across all clients, the backstop against a sender spread over many addresses using the instance to mail arbitrary inboxes. Reaching it pauses sign-in email for everyone until the hour rolls over, so keep it well above real sign-in traffic. |
| `PORTAL_LOGIN_THROTTLE_IP` | `30/hour` | No | Rate limit for customer portal sign-in code requests (`/api/portal/login/<org_id>/request/`), per client IP across all orgs. Same `RELAY_SECRET` caveat as `MAGIC_LINK_THROTTLE_IP`. |
| `PORTAL_LOGIN_THROTTLE_ORG` | `100/hour` | No | Rate limit for customer portal sign-in code requests, per org across all clients. Reaching it pauses portal sign-in for that org only. |
| `CALENDAR_FEED_THROTTLE_TOKEN` | `60/hour` | No | Rate limit for the task calendar feed, per feed URL across all clients. Bucketed on the token's hash, never the raw token. A calendar app polls every few minutes at most, so this is only reached by something hammering one URL. |

### CORS and CSRF

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `CORS_ALLOW_ALL` | `False` | No | Sets Django's `CORS_ORIGIN_ALLOW_ALL`. Note the name doesn't match the Django setting it controls. `"true"` (case-insensitive) allows every origin; anything else leaves it off. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5181,http://127.0.0.1:5181` | Yes, once the frontend is served from a real domain | Comma-separated list of allowed CORS origins; each entry is stripped of surrounding whitespace and empty entries are dropped. |
| `CSRF_TRUSTED_ORIGINS` | *(empty, no trusted origins)* | Yes, before anyone but you can reach the instance | Comma-separated list, same stripping rule as `CORS_ALLOWED_ORIGINS`. Each entry needs a scheme, e.g. `https://crm.example.com`. A bare hostname is silently useless to Django's CSRF check. |

### URLs

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `DOMAIN_NAME` | `http://localhost:8000` | **Yes**, once self-hosted under a real domain (from django-crm 1.13.0) | The **backend's** public origin, for example `https://api.example.com`. From django-crm 1.13.0 it is the base of every URL the API hands out for use somewhere else (`common.links.api_url`): the task calendar feed URL a calendar app subscribes to, and a web form's embed snippets and the submit URL inside the embedded form. They used to be built from the incoming request, so the web app, which asks from its own server at `PUBLIC_DJANGO_API_URL` (`http://backend:8000` in Docker), showed a host nobody outside could reach, and `http://` unless `TRUST_PROXY_SSL_HEADER` was set. When `ENV_TYPE` is anything other than `dev`, `crm/settings.py` raises at import if this points at a loopback host (`localhost`, `127.0.0.1`, `0.0.0.0`, `::1`) or is not an absolute `http`/`https` URL, the same check `FRONTEND_URL` gets. A trailing slash is stripped. It no longer reaches any emailed link: those come from `FRONTEND_URL`, and the four `domain=` kwargs still passed to invoice Celery tasks (`invoices/tasks.py`, `invoices/api_views.py`) are ignored by those tasks. |
| `FRONTEND_URL` | `http://localhost:5181` | **Yes**, once self-hosted under a real domain | Base URL of the SvelteKit frontend, and the single source for every link that leaves the system in an email: the welcome email and magic-link sign-in URL (`common/tasks.py`), the emailed invoice and estimate portal links, the CSAT survey link, and the "assigned to you" invoice link (all via `common.links.frontend_url`). The customer-facing ones go to people outside the organization, so leaving the `localhost` default in a real deployment emails dead links to customers. **That is now refused rather than emailed:** when `ENV_TYPE` is anything other than `dev`, `crm/settings.py` raises at import if this points at a loopback host (`localhost`, `127.0.0.1`, `0.0.0.0`, `::1`) or is not an absolute `http`/`https` URL, so the deploy fails to start instead of failing silently on somebody else's inbox. Plain HTTP against a real host is allowed, for a deployment behind its own TLS terminator. A trailing slash is stripped, so `https://app.example.com` and `https://app.example.com/` behave the same. |

### Inbound email

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `INBOUND_SNS_ACCOUNT_IDS` | *(empty, no account allowed)* | No. Without it, an admin enters each mailbox's SNS Topic ARN by hand | Comma-separated 12-digit AWS account ids, for example `123456789012,210987654321` (whitespace around each is ignored). New in django-crm 1.13.0. An [inbound mailbox](../integrations/inbound-email.md#securing-the-endpoint) that has no Topic ARN yet pins the topic of the first signature-verified SNS `SubscriptionConfirmation` it receives **only when that topic belongs to one of these accounts**; a confirmation from any other account is refused with `403` and pins nothing, and so is every notification until a pin exists. SNS lets any AWS account subscribe any HTTPS endpoint to its own topic, so before 1.13.0 whoever subscribed a mailbox's URL first owned it. Set it to the account that owns your SES receipt-rule topics (a hosted platform sets its own account). A mailbox whose admin entered a Topic ARN ignores this setting: only that exact ARN is accepted. `crm/settings.py` raises at import, in every environment, if any entry is not exactly 12 ASCII digits, so a typo fails the deploy instead of leaving every mailbox silently unpinned. |

### Google OAuth

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `GOOGLE_CLIENT_ID` | *(empty)* | No, leaving it blank disables Google sign-in | OAuth client ID sent to Google's token endpoint and used as the expected audience when verifying a mobile ID token. See [Google OAuth](../self-hosting/google-oauth.md#backend-configuration). |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | No, leaving it blank disables Google sign-in | OAuth client secret, alongside `GOOGLE_CLIENT_ID` above. |

### Docker bootstrap (not read by `crm/settings.py`)

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `ADMIN_PASSWORD` | *(empty string)* | No, but leaving it unset is a real security gap | Read directly by the `create_default_admin` management command. An empty value doesn't mean "no password", the command falls back to the literal password `admin` and prints a warning. `create_default_admin` runs automatically on every Docker container start (`docker/backend/entrypoint.sh`, right after `migrate`), so an unset `ADMIN_PASSWORD` in a real deployment means the bootstrap Django superuser's password is the literal string `admin` until someone changes it. See [Management commands](management-commands.md#create_default_admin). |
