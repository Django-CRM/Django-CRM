# Production deployment

Neither `docker-compose.yml` nor the repo-root `Dockerfile` builds a production-hardened
deployment out of the box. See the warning in [Docker](docker.md). This page covers what
changes: the process that serves requests, how static files are handled, what sits in front of
Django, and which environment variables the codebase itself treats as security-relevant. It
assumes you already have a working install per [Manual setup](../getting-started/manual-setup.md)
or [Docker](docker.md) and a PostgreSQL role configured per
[PostgreSQL and RLS](postgresql-and-rls.md).

## Before you start

There is no bundled production `docker-compose` file, systemd unit, or process manager
configuration in this repository, the `Dockerfile` builds an image (`backend/Dockerfile` puts
`uv`-managed dependencies and the venv's `bin/` on `PATH`, so `gunicorn`, `celery`, and `python`
all resolve inside it), but what command that image *runs* is up to you: `docker-compose.yml`'s
`backend` service overrides it with `/entrypoint.sh`, which calls `manage.py runserver`, the
Django development server. For a production container or host process you supply your own start
command (see [Application server](#application-server) below) instead of that entrypoint script.

Whatever you run it as, apply migrations and collect static files before serving traffic:

```bash
cd backend
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput
```

## Application server

`backend/pyproject.toml` lists `gunicorn>=26.0.0` under its dependencies, commented "Production
WSGI server". Gunicorn is the WSGI server this project ships as a dependency for serving the
Django app itself. `backend/crm/wsgi.py` exposes the standard `application` callable, so from
`backend/`:

```bash
cd backend
uv run gunicorn crm.wsgi:application --bind 0.0.0.0:8000
```

**WSGI is the recommended path, and there is no longer a reason to prefer ASGI.** Earlier
releases required an ASGI server (`uvicorn crm.asgi:application`) because the in-app notifications
endpoint was a Server-Sent Events stream, and a long-lived async view holds a worker hostage under
WSGI. That stream was removed on 2026-08-03 in favour of polling, and it was the only async code
in the project, so nothing needs ASGI now. `backend/crm/asgi.py` still exists and still works if
you prefer it, but it buys you nothing.

Prefer WSGI for a concrete reason beyond simplicity: **it bounds your database connections by
construction.** Under ASGI, Django runs each request in its own thread-sensitive executor with its
own thread-local connection and no ceiling, so concurrent requests translate one-to-one into
PostgreSQL backends. That is what caused two connection-exhaustion incidents in this project's
history. Under Gunicorn, peak connections are `workers × threads`, a number you set on the command
line:

```bash
cd backend
uv run gunicorn crm.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 4
```

That example tops out at 12 database connections per host, plus whatever Celery uses. See
[Database connections](#database-connections) below for pooling, which you may still want if you
run many Gunicorn workers or a large thread count.

`uvicorn[standard]>=0.51.0` remains a dependency so the ASGI path keeps working for anyone already
deployed that way. If you serve under ASGI, read the next section before going live: bounding
connections there is not optional.

## Database connections

Under ASGI, Django runs each request in its own thread-sensitive executor, and each of those gets
its own thread-local database connection. Nothing caps that. A burst of concurrent requests
becomes a burst of PostgreSQL backends, and `max_connections` (default `100`) is reached long
before the application itself is under strain. This project has hit that in production.

Set `DB_POOL_ENABLED=true` to put a bounded `psycopg_pool` pool in front of the database.
`DB_POOL_MAX_SIZE` (default `10`) is the ceiling per process. Size it deliberately: the pool is
per process, so peak usage is roughly `DB_POOL_MAX_SIZE × (uvicorn workers + Celery prefork
children)`. See [Environment variables](../reference/environment-variables.md#connection-pooling)
for the arithmetic and a worked example.

Two things to know before enabling it:

- **The pool requires psycopg 3.** That is what this project depends on
  (`psycopg[binary,pool]`); Django raises `ImproperlyConfigured` if pool options are set under
  psycopg2. Nothing to do unless you have pinned the driver yourself.
- **Do not hand-roll the pool configuration.** Tenant isolation depends on `app.current_org`, a
  session-scoped PostgreSQL setting that survives the transaction and the request. `psycopg_pool`
  does not clear session state when a connection is returned, so a pool without the project's
  `reset` callback serves one tenant's data to the next borrower of that connection. Setting
  `DB_POOL_ENABLED=true` wires the callback in for you. Writing your own `OPTIONS["pool"]` block
  without it is a cross-tenant data leak.

An external pooler is a reasonable alternative, with one hard constraint: it must run in
**session** mode. Transaction-mode pooling (PgBouncer's default) does not keep session settings
across statements, which silently breaks the RLS context this application relies on for tenant
isolation.

## Static files

`whitenoise.middleware.WhiteNoiseMiddleware` is in `MIDDLEWARE` (immediately after
`SecurityMiddleware`), and `STATICFILES_STORAGE` is set to
`whitenoise.storage.CompressedManifestStaticFilesStorage`. This means Django itself serves static
assets, compressed and content-hashed, directly from the WSGI/ASGI process; you do not need a
separate static file server or a reverse-proxy `location /static/` block for correctness. Static
files are collected to `STATIC_ROOT` (`backend/staticfiles/`, i.e. `BASE_DIR/staticfiles`) under
`STATIC_URL = "/static/"` by `manage.py collectstatic`, which you must run yourself. Gunicorn
does not run it for you (the Docker Compose entrypoint does, but only because
`docker/backend/entrypoint.sh` calls it explicitly on every start).

Media (user-uploaded files: invoice attachments, case attachments, and similar) is separate from
static files and is not covered by WhiteNoise. With `ENV_TYPE=dev` (the default),
`MEDIA_ROOT`/`MEDIA_URL` are a local `media/` directory served through Django itself; with
`ENV_TYPE=prod`, `backend/crm/server_settings.py` is imported instead and switches
`DEFAULT_FILE_STORAGE` to `storages.backends.s3boto3.S3Boto3Storage`, reading `AWS_BUCKET_NAME`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SES_REGION_NAME`, `AWS_SES_REGION_ENDPOINT` and
`SENTRY_DSN` from the environment with `os.environ[...]`: a plain `KeyError` at import time, and
the process refusing to start, if any of them are unset. It also switches `EMAIL_BACKEND` to
`django_ses.SESBackend` and hardcodes `SESSION_COOKIE_DOMAIN = ".bottlecrm.io"`. That domain is
specific to the project's own SaaS hosting, not to your deployment. Setting `ENV_TYPE=prod`
without also being ready to supply AWS S3, SES and Sentry credentials, and without meaning to
scope session cookies to `bottlecrm.io`, will not do what you want. See
[Required settings](#required-settings) below for the practical consequence.

## Reverse proxy

Nothing in `backend/crm/settings.py` terminates TLS, and `SECURE_PROXY_SSL_HEADER` is set only
when you opt in with `TRUST_PROXY_SSL_HEADER=true` (see
[Environment variables](../reference/environment-variables.md)). Concretely: if you terminate TLS at a
reverse proxy (nginx, Caddy, or similar) and forward plain HTTP to Gunicorn/Uvicorn, Django's
`request.is_secure()` will report `False` for every request unless that variable is set and your
proxy overwrites `X-Forwarded-Proto`. `SECURE_HSTS_SECONDS = 31536000`
(one year), `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`, `SECURE_HSTS_PRELOAD = True` and
`SECURE_CONTENT_TYPE_NOSNIFF = True` are all set unconditionally in `backend/crm/settings.py`, but
Django's `SecurityMiddleware` only emits the `Strict-Transport-Security` header on requests it
considers secure, so without `SECURE_PROXY_SSL_HEADER` configured correctly for your proxy, that
HSTS configuration silently never takes effect.

Point your proxy at whichever port your application server binds (`8000` in the examples above),
and route `/` there. There's no repo-provided proxy config (nginx site file, Caddyfile, or
similar) to point to. The specifics are yours to write, with one requirement: the proxy's access
log records the calendar feed, invoice, estimate and survey links, whose path carries a working
credential. The application redacts its own logs but cannot reach the proxy's. See
[Public-link tokens in logs](security-hardening.md#public-link-tokens-in-logs) for an nginx
`log_format` that does it.

The URLs the API hands out for use somewhere else are built from `DOMAIN_NAME`, not from the request
that reached it: the web form embed snippets and the form's submit URL and, from django-crm 1.13.0,
the task calendar feed URL (`/api/public/calendar/<token>.ics`, see
[API: Tasks](../api/tasks.md#calendar-feed)). The feed URL is pasted into Google Calendar or
Outlook, which fetch it from the internet, and the snippet onto a customer's site, so
`DOMAIN_NAME` has to be the API's public origin, for example `https://api.example.com`. The web app
asks for these from its own server at `PUBLIC_DJANGO_API_URL`, which may be an internal address such
as the compose file's `http://backend:8000`; that no longer leaks into the URLs it shows. With
`ENV_TYPE` set to anything but `dev`, the backend refuses to start when `DOMAIN_NAME` is a loopback
or non-absolute URL.

## Required settings

`backend/crm/settings.py` reads these from the environment at import time (i.e. at process
startup, not per-request):

| Variable | Behavior |
|---|---|
| `SECRET_KEY` | Defaults to the literal string `"django-insecure-dev-key-please-change-in-production"` if unset. The module itself checks this: if `SECRET_KEY` is empty or starts with `"django-insecure"` **and** `ENV_TYPE != "dev"`, it raises `ValueError("SECRET_KEY must be set to a secure value in non-dev environments")` at import time, a hard startup failure, not a warning. This guard only fires when `ENV_TYPE` is set to something other than `dev`; given the `ENV_TYPE=prod` consequences described above (S3/SES/Sentry env vars becoming mandatory, the hardcoded `.bottlecrm.io` cookie domain), don't rely on that check alone. Set a real, unique `SECRET_KEY` regardless of `ENV_TYPE`. |
| `DEBUG` | Must be `False` in production. Read as `os.environ.get("DEBUG", "False").lower() == "true"`, defaults to `False` already, so the failure mode to avoid is *explicitly* setting `DEBUG=True` (as `.env.docker` does for local development) and carrying that into production. |
| `ALLOWED_HOSTS` | A comma-separated environment variable: `os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")`. Set it to the hostname(s) you'll actually be served on, e.g. `ALLOWED_HOSTS=crm.example.com`. |
| `CSRF_TRUSTED_ORIGINS` | Also comma-separated: `os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")`, empty by default. Django requires each entry to include a scheme (e.g. `https://crm.example.com`), not a bare hostname. |

A few more the same module reads, worth setting deliberately rather than leaving at their
defaults: `CORS_ALLOWED_ORIGINS` (comma-separated, defaults to the two local dev frontend
origins) and `CORS_ALLOW_ALL` (`CORS_ORIGIN_ALLOW_ALL`, `.env.docker` sets this `True` for local
development. Leave it `False` in production and rely on `CORS_ALLOWED_ORIGINS` instead), and
and `FRONTEND_URL`, which is interpolated into every emailed link (magic-link sign-in, the
customer's invoice and estimate portal, the CSAT survey, internal notifications) and so needs to
point at your real, public hostname. `FRONTEND_URL` is now checked at startup: with `ENV_TYPE`
set to anything but `dev`, a loopback or non-absolute value raises rather than mailing a dead
link to a customer. `DOMAIN_NAME`, the API's own public origin, is checked the same way (see
above).
