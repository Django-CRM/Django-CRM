# Security hardening

BottleCRM is multi-tenant: every deployment holds more than one organization's data behind the same
database and the same application process. This page collects the settings and surfaces that matter
most once you're running something other people depend on, most of which are covered in more depth
elsewhere in Self-hosting. This page is the checklist, with pointers to the detail.

## Production checklist

- **`DEBUG=False`.** `crm/settings.py` reads it as `os.environ.get("DEBUG", "False").lower() ==
  "true"`, so it's already `False` by default. The failure mode to avoid is `.env.docker` (which
  ships `DEBUG=True` for local development) carrying into a production environment unchanged.
- **A real `SECRET_KEY`**, not the shipped `django-insecure-...` placeholder. See
  [Secrets](#secrets) below.
- **`ALLOWED_HOSTS`** set to your real hostname(s), and **`CSRF_TRUSTED_ORIGINS`** set with a scheme
  (`https://crm.example.com`, not a bare hostname), both detailed in
  [Production deployment](production-deploy.md#required-settings).
- **`CORS_ALLOW_ALL=False`** (the default) with **`CORS_ALLOWED_ORIGINS`** scoped to your real
  frontend origin(s); `.env.docker` sets `CORS_ALLOW_ALL=True` for local development, which should
  not carry into production either.
- **`SECURE_PROXY_SSL_HEADER`** added yourself if you terminate TLS at a reverse proxy, nothing in
  `crm/settings.py` sets it, so without it Django never considers a proxied request secure and the
  HSTS headers this project does set unconditionally (`SECURE_HSTS_SECONDS`,
  `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`) silently never get sent. See
  [Production deployment](production-deploy.md#reverse-proxy).
- **A non-superuser database role**: see [Database role](#database-role) below.
- **Redact public-link tokens from your reverse proxy's access log.** The application scrubs its
  own logs, but nginx, Caddy or a load balancer in front of it records every path it forwards. See
  [Public-link tokens in logs](#public-link-tokens-in-logs) below.
- **Don't set `ENV_TYPE=prod` casually.** It's not just a flag: it makes `AWS_BUCKET_NAME`,
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SES_REGION_NAME`, `AWS_SES_REGION_ENDPOINT` and
  `SENTRY_DSN` mandatory (a `KeyError` at startup if any is missing), switches email to Amazon SES,
  and hardcodes `SESSION_COOKIE_DOMAIN = ".bottlecrm.io"`, a domain specific to this project's own
  SaaS hosting. See [Production deployment](production-deploy.md#static-files) before setting it.

## Database role

The single highest-leverage setting in this list: `DBUSER` must not be a PostgreSQL superuser, or
every Row-Level Security policy in the schema is bypassed silently, with no error and no log line.
The default (`DBUSER` unset, falling back to `postgres`) is a superuser on most installations. Create
a dedicated role and confirm it isn't one:

```bash
cd backend
uv run python manage.py manage_rls --verify-user
```

Full detail: how to create the role, how RLS policies are applied, and how to prove isolation holds
for real data rather than trusting that it does, is in
[PostgreSQL and RLS](postgresql-and-rls.md).

## Secrets

`SECRET_KEY` does more than Django's usual signing duties here: `SIMPLE_JWT["SIGNING_KEY"]` in
`crm/settings.py` is set to `SECRET_KEY` directly, so it is also the key every access and refresh
token is signed and verified with. There is no separate JWT signing-key setting anywhere in this
codebase, rotating `SECRET_KEY` invalidates every outstanding access and refresh token immediately,
which is worth knowing before you rotate it as a routine maintenance step rather than in response to
an actual compromise.

`crm/settings.py` itself guards against the worst case of forgetting to set it: if `SECRET_KEY` is
empty or still starts with `"django-insecure"` **and** `ENV_TYPE` is anything other than `dev`, the
module raises `ValueError("SECRET_KEY must be set to a secure value in non-dev environments")` at
import time, a hard startup failure rather than a silent insecure default. That guard only fires
when `ENV_TYPE` is explicitly non-`dev`, so set a real, unique `SECRET_KEY` regardless of whether you
ever set `ENV_TYPE=prod`.

## Token surfaces

Three different credential types exist, with different blast radii if one leaks:

- **Personal access tokens** (`bcrm_pat_...`, managed at `profile/tokens/`) are scoped to the one
  profile that created them. `resolve_valid_pat()` in `backend/common/pat_auth.py` checks
  `pat.profile.is_active` (and `pat.org.is_active`) on every use, so deactivating a profile cuts off
  its personal access tokens immediately. There's no separate token-revocation step required when
  offboarding someone.
- **Admin oversight of tokens** is separate from the self-service surface above: `org/tokens/`
  (`OrgAccessTokenListView`/`OrgAccessTokenDetailView`) is admin-only and org-wide. An admin can see
  and revoke any token in their org, including one belonging to a colleague who has since been
  deactivated. It's deliberately a distinct endpoint from `profile/tokens/` rather than a widened
  version of it, so the self-service guard never has to be loosened to support oversight.
- **The org API key** (`GET`/`POST /api/org/api-key/`, `OrgApiKeyView`, admin-only) is the bluntest
  of the three: `common/middleware/get_company.py`'s `_process_api_key_auth` resolves a request
  bearing this key to the org's first active `ADMIN` profile. One key per tenant, never expiring,
  so it cannot be revoked per integration. It is read-only and barred from the credential endpoints
  (`common/scopes.py`), which stops a leaked key deleting records, escalating a role, or minting a
  personal access token owned by the admin whose identity it borrowed. It still reads every record
  in the org. It's kept out of every nested API representation and served only by this endpoint.
  **Set `DJANGO_ORG_API_KEY_AUTH=false` once every integration uses a personal access token**, which
  refuses the key as an authentication method entirely. If it's ever exposed: committed to a repo,
  logged, shared over an insecure channel, rotate it with `POST /api/org/api-key/` from a signed-in
  session; the response is explicit that "the previous key is no longer valid" immediately on
  rotation.
- **No token can manage a credential.** `/api/profile/tokens/`, `/api/org/tokens/` and
  `/api/org/api-key/` are refused for any personal access token and for the org API key, whatever
  their scopes. Both chains would otherwise defeat revocation: a token that mints tokens leaves
  children behind when you revoke it, and a token that reads the org API key upgrades itself into a
  credential that outlives its own revocation. Credential management requires an interactive
  sign-in.

## Public-link tokens in logs

Four kinds of link carry their credential as a path segment. Whoever holds the URL can use it
without signing in, so a log line that records the path hands the credential to anyone who can
read that log, or to wherever the log is shipped.

| Link | API path | Web app path (the one emailed to customers) |
|---|---|---|
| Task calendar feed | `/api/public/calendar/<token>.ics` | none |
| Satisfaction survey | `/api/public/csat/<token>/` | `/csat/<token>` |
| Invoice | `/api/public/invoice/<token>/`, `.../pdf/` | `/portal/invoice/<token>` |
| Estimate | `/api/public/estimate/<token>/`, `.../pdf/`, `.../accept/`, `.../decline/` | `/portal/estimate/<token>` |

From django-crm 1.13.0 the application writes `[Filtered]` in place of the token everywhere it logs
a path: Django's request log (`Not Found: <path>`, `Too Many Requests: <path>`), Sentry events, and
the app server's access log. The last is a logging filter that `crm/settings.py` attaches to the
`uvicorn.access` and `gunicorn.access` loggers, so it applies to `uvicorn` (which logs every request
by default) and to `gunicorn --access-logfile` alike, with no flag to set. A line reads, for
example, `"GET /api/public/invoice/[Filtered]/pdf/ HTTP/1.1" 200`. The patterns live in
`backend/common/public_tokens.py`.

**Your reverse proxy is outside the process and needs its own redaction.** nginx's default
`combined` format records the full request line and the `Referer`, and the web app's portal pages
are same-origin, so their token shows up in the `Referer` of every asset they load. For nginx, put
this in the `http {}` context (on Debian and Ubuntu, a new file under `/etc/nginx/conf.d/`):

```nginx
# BottleCRM: public-link tokens are credentials; log them as [Filtered].
map $request_uri $bottlecrm_log_uri {
    "~^((?:/api/public/(?:calendar|csat|invoice|estimate)|/portal/(?:invoice|estimate)|/csat)/)[^/?]+(.*)$" "$1[Filtered]$2";
    default $request_uri;
}

map $http_referer $bottlecrm_log_referer {
    "~^((?:https?://[^/]+)?(?:/api/public/(?:calendar|csat|invoice|estimate)|/portal/(?:invoice|estimate)|/csat)/)[^/?]+(.*)$" "$1[Filtered]$2";
    default $http_referer;
}

# nginx's "combined" format, with the request line and Referer redacted.
log_format bottlecrm_redacted '$remote_addr - $remote_user [$time_local] '
                              '"$request_method $bottlecrm_log_uri $server_protocol" '
                              '$status $body_bytes_sent "$bottlecrm_log_referer" "$http_user_agent"';
```

Then name that format on every `access_log` line in the `server {}` blocks for the API and the web
app, keeping each block's existing path. A block with no `access_log` line inherits the global one,
so add one:

```nginx
access_log /var/log/nginx/access.log bottlecrm_redacted;
```

Check every `location {}` inside those blocks too: one with its own `access_log` does not inherit
the server's. Run `nginx -t` before reloading. With Caddy, Traefik or a cloud load balancer, apply the
same pattern with its own log-filter feature, or stop it logging those paths.

Two limits worth knowing. nginx's `error_log` has no format, so an upstream failure (a 502 or 504 on
one of these paths) still writes the raw request line there. And lines written before you made
these changes still hold working tokens: shorten their retention or remove them. A member can cut
off a leaked calendar feed by regenerating it, and a survey link expires, but an invoice's or
estimate's link cannot be rotated or switched off from the app or the API.

## What to monitor

`SecurityAuditLog` (`backend/common/audit_log.py`, table `security_audit_log`) is a table of
security-relevant events, with a matching `security.audit` Python logger that also writes to
`security_audit.log` (configured in `crm/settings.py`'s `LOGGING` dict). The table has no RLS policy
on purpose, since some rows have no org; an org's admins read their own rows through
[API: Security audit log](../api/audit-log.md), filtered on their org. It's worth watching, with one
caveat: of the event types the model defines, `LOGIN_SUCCESS`, `LOGIN_FAILURE`, `LOGOUT`,
`ORG_SWITCH`, `TOKEN_REFRESH`, `TOKEN_REVOKED`, `PERMISSION_DENIED`, `SAMPLE_DATA_CLEARED`,
`WEBHOOK_PAUSED`, `WEBHOOK_REENABLED`, `WEBHOOK_CHANGED` and `RECORD_MERGED` are written by the
application code as of 1.13.0. `CROSS_ORG_ATTEMPT`, `API_KEY_USED`, `API_KEY_INVALID`,
`MEMBERSHIP_REVOKED` and `SUSPICIOUS_ACTIVITY` are defined on the model and have methods on the
`AuditLogger` helper, but nothing calls them. Don't rely on this table to surface a bad API key
attempt.

Failed staff sign-ins are recorded from django-crm 1.13.0 as `LOGIN_FAILURE`, with the claimed email
and a reason code (see [API: Security audit log](../api/audit-log.md#failed-sign-ins)). They carry no
org, so no org's audit viewer shows them: watch the table or the log file for them. Each client IP
writes at most 20 of these rows an hour, so a brute-force run shows up as a burst that stops at 20,
not as one row per attempt.
