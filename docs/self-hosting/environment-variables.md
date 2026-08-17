# Environment variables

`backend/crm/settings.py` is read once, at process import time, by every Django entry point:
`manage.py runserver`, `manage.py migrate`, a Gunicorn/Uvicorn worker, a Celery worker, a management
command. This page explains where those values come from and which ones you actually have to set
yourself; the exhaustive per-variable table lives in the Reference section.

## How configuration is loaded

`backend/crm/settings.py` calls `load_dotenv()` (from `python-dotenv`) near the top of the module,
before any setting is read, so a `.env` file next to `manage.py`, created by copying
`backend/.env.example`, per [Manual setup](../getting-started/manual-setup.md), is loaded into the
process environment automatically. `docker-compose.yml` does the equivalent for containers by
listing `.env.docker` and then the optional, gitignored `.env.docker.local` under each service's
`env_file:`, in that order, so values in `.env.docker.local` win.

Almost every setting in `crm/settings.py` follows the same pattern: `os.environ.get("NAME",
<default>)`, so an unset variable falls back to a working (usually dev-oriented) default rather than
failing. `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DBNAME`/`DBUSER`/`DBPASSWORD`/`DBHOST`/`DBPORT`,
`EMAIL_BACKEND`, `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`,
`DOMAIN_NAME` and `FRONTEND_URL` are all read this way. A
smaller set of variables has no default at all: `backend/crm/server_settings.py` (imported only when
`ENV_TYPE=prod`, see [Production deployment](production-deploy.md)) reads `AWS_BUCKET_NAME`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SES_REGION_NAME`, `AWS_SES_REGION_ENDPOINT` and
`SENTRY_DSN` with plain `os.environ[...]`. A missing one is a `KeyError` at import time, which means
the process refuses to start at all rather than starting with something half-configured.

Because all of this runs at import time, not per-request, a changed environment variable only takes
effect the next time the process restarts: editing `.env` while `runserver` is already running does
nothing until you restart it (or, for the Docker Compose setup, until the container restarts, which
happens automatically on `docker compose up --build`).

## The minimum for a working install

The values in `.env.example` and `.env.docker` are enough to bring the stack up and use it locally;
you do not need to touch anything else to follow [Docker quick start](../getting-started/docker-quick-start.md)
or [Manual setup](../getting-started/manual-setup.md). Before anyone other than you can reach the
instance, the variables that actually need a real value are: `SECRET_KEY` (a unique secret, not the
shipped `django-insecure-...` placeholder), `DEBUG=False`, `ALLOWED_HOSTS` (your real hostname(s)),
`CSRF_TRUSTED_ORIGINS` (each entry needs a scheme, e.g. `https://crm.example.com`), `DBUSER` pointed
at a non-superuser PostgreSQL role, and `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` pointed at a
Redis instance you control. [Production deployment](production-deploy.md#required-settings) covers
the full list and exactly what each one guards against; [PostgreSQL and RLS](postgresql-and-rls.md)
covers why the database role matters as much as any of the Django settings.

## Database

`DATABASES["default"]` in `crm/settings.py` reads `DBNAME` (default `crm_db`), `DBUSER` (default
`postgres`), `DBPASSWORD` (default `postgres`), `DBHOST` (default `localhost`) and `DBPORT` (default
`5432`). The default `DBUSER` is the PostgreSQL superuser account on most installations, and a
superuser silently bypasses every Row-Level Security policy this project relies on for tenant
isolation. See [PostgreSQL and RLS](postgresql-and-rls.md) for how to create and verify a
non-superuser role before pointing these variables at anything but a local, throwaway database.

## Email

`EMAIL_BACKEND` defaults to `django.core.mail.backends.console.EmailBackend`, so outbound mail
(welcome emails, magic-link sign-in emails, comment-mention notifications) prints to stdout instead
of being sent anywhere. `.env.example` sets `EMAIL_BACKEND` to that same console backend explicitly;
`.env.docker` doesn't set `EMAIL_BACKEND` at all, only a comment noting the console backend is
what's active, so it's relying on the identical default rather than setting it. `DEFAULT_FROM_EMAIL`
and `ADMIN_EMAIL` default to `noreply@localhost`/`admin@localhost`. Switching to a real mail backend,
and the Amazon SES-specific variables that come with it, is covered in full in
[Email and Celery](email-and-celery.md).

## Integrations

`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` both default to empty strings in `crm/settings.py`,
and `.env.docker` and `.env.example` ship both blank. Google sign-in stays off until they are set, on
both the backend and the SvelteKit frontend. There is no `GOOGLE_REDIRECT_URI` setting: the redirect
URI used in the token exchange is the one the frontend sends in the request body on each call, so a
setting of that name was read into Django and never referenced again. See
[Google OAuth](google-oauth.md) for what each one does and where to get the values.
`SENTRY_DSN` is read (with no default) only inside `server_settings.py`, i.e. only when
`ENV_TYPE=prod`; it's not consulted at all otherwise.

`CACHE_URL` is empty by default, which makes Django's default cache a per-process `LocMemCache`.
That is fine for `runserver` and wrong for anything with more than one worker, because DRF stores
its throttle counters there: with N Gunicorn workers the effective rate limit becomes roughly N
times the configured one and resets on every restart. The public [web form](../integrations/web-forms.md)
submit endpoint is the one thing today whose spam controls depend on it, so on any real deployment
point `CACHE_URL` at the Redis you already run for Celery (a separate database, e.g.
`redis://localhost:6379/1`). `WEBFORM_THROTTLE_IP` and `WEBFORM_THROTTLE_GLOBAL` set those limits
and both have working defaults.

## Full reference

Every variable this project reads, its default, and the file that reads it is tabulated in full in
the Reference section.
