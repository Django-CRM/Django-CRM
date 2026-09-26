# Email and Celery

BottleCRM sends email (welcome messages, magic-link sign-in, comment mentions) and runs background
and scheduled work (recurring invoices, SLA scanning, token cleanup) through Django's email
framework and Celery, respectively. This page covers how to configure both and the one rule every
background task that touches tenant data has to follow.

## Email backend

`EMAIL_BACKEND` in `backend/crm/settings.py` defaults to
`django.core.mail.backends.console.EmailBackend`: outbound mail is printed to the process's stdout
rather than delivered anywhere. `.env.example` sets `EMAIL_BACKEND` to that console backend
explicitly; `.env.docker` doesn't set `EMAIL_BACKEND` at all (only a comment noting the console
backend is what's active for local development), so it relies on the same default rather than
setting it. To send real email, set `EMAIL_BACKEND` to a working Django email backend, most
commonly `django_ses.SESBackend` (see [Amazon SES](#amazon-ses) below); `django_ses` is always in
`INSTALLED_APPS`, so the package is available regardless of which backend you choose.

There is a second, coarser way `EMAIL_BACKEND` gets set to SES: if you set `ENV_TYPE=prod`,
`backend/crm/server_settings.py` is imported and hardcodes `EMAIL_BACKEND = "django_ses.SESBackend"`
unconditionally, overriding whatever `EMAIL_BACKEND` you set directly. `ENV_TYPE=prod` also requires
`AWS_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SES_REGION_NAME`,
`AWS_SES_REGION_ENDPOINT` and `SENTRY_DSN` to already be set (a plain `KeyError` at import time if
any is missing) and hardcodes `SESSION_COOKIE_DOMAIN = ".bottlecrm.io"`. See
[Production deployment](production-deploy.md#static-files) for the full consequences. If you only
want SES for email and don't want the S3/Sentry/cookie-domain side effects that come with
`ENV_TYPE=prod`, set `EMAIL_BACKEND=django_ses.SESBackend` directly instead and leave `ENV_TYPE=dev`.

`DEFAULT_FROM_EMAIL` (default `noreply@localhost`) is the sender address used on every outbound
message; `ADMIN_EMAIL` (default `admin@localhost`) is separate and is what
`manage.py create_default_admin` uses for the seeded admin account, not a mail setting.

## Amazon SES

Once `EMAIL_BACKEND` resolves to `django_ses.SESBackend`, whichever of the two paths above got you
there; `crm/settings.py` reads `AWS_SES_REGION_NAME` (default `ap-south-1`) and
`AWS_SES_REGION_ENDPOINT` (default `email.{AWS_SES_REGION_NAME}.amazonaws.com`, computed from
whatever you set for the region). AWS credentials are read implicitly by `django-ses`/`boto3` from
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` if you set them; if you don't, `boto3` falls back to
IAM role credentials, which is the intended path when running inside AWS (EC2/ECS instance role)
rather than with long-lived access keys in the environment.

The Celery tasks that actually send mail; `send_welcome_email` and `send_magic_link_email` in
`backend/common/tasks.py`: catch `botocore.exceptions.ClientError` around the `msg.send()` call and
log the failure rather than letting the task raise, specifically because SES can reject a send (an
unverified sender identity, a suppressed recipient address, being outside the SES sandbox) in ways
that are the mail provider's problem, not a bug in the task. Check the worker's logs, not the Celery
task result, if an expected email never arrives.

## Broker and result backend

`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` both default to `redis://localhost:6379/0` in
`crm/settings.py`, and both `.env.example` and `.env.docker` leave them at that default (Docker
Compose's value points at the `redis` service hostname instead of `localhost`). Both need a reachable
Redis instance: `backend/crm/celery.py` constructs `Celery("crm")` and calls
`app.config_from_object("django.conf:settings", namespace="CELERY")`, so these two Django settings
are what Celery actually connects with; there's no separate Celery-specific config file.

## Running a worker

```bash
cd backend
uv run celery -A crm worker --loglevel=INFO
```

This process executes tasks queued by the API: sending the emails above, mention notifications,
team-membership propagation, and more. A separate `celery beat` process is required for anything on
a schedule: `backend/crm/celery.py` registers `app.conf.beat_schedule` with twelve periodic entries,
including recurring-invoice generation and overdue/expired-estimate checks (daily), outbound
webhook retries (every minute), SLA breach scanning for cases (every 5 minutes), stale-timer cleanup
(every 30 minutes), and nightly cleanup of read notifications, expired refresh-token records and
webhook delivery logs:

```bash
cd backend
uv run celery -A crm beat --loglevel=INFO
```

Neither process is required for the API itself to answer requests, only for background and
scheduled work to actually run. `docker-compose.yml` runs both as their own services,
`celery-worker` and `celery-beat`, alongside `backend`.

## RLS in background tasks

Celery workers never go through Django's request middleware, so nothing sets the PostgreSQL session
variable `app.current_org` for them the way `RequireOrgContext` does for an HTTP request. See
[PostgreSQL and RLS](postgresql-and-rls.md#verifying-isolation) for the full mechanism. Every task in
`backend/common/tasks.py` that queries an org-scoped table calls `set_rls_context(org_id)`, also
defined in that module, as its first line:

```python
from common.tasks import set_rls_context

@shared_task
def my_task(data_id, org_id):
    set_rls_context(org_id)  # required before any org-scoped query
    ...
```

Skip this and the task runs with whatever RLS context the worker's underlying database connection
happens to have, on a fresh connection, that's empty, which (per the fail-safe design described in
[PostgreSQL and RLS](postgresql-and-rls.md#why-rls)) means the query returns zero rows rather than
another org's rows, but it is still a bug: a task that's supposed to act on real data silently does
nothing. `flush_expired_refresh_tokens` is a legitimate case where no context is needed at all: it
operates on simplejwt's `OutstandingToken` table, which isn't in `ORG_SCOPED_TABLES` and carries no
`org_id` column, so there's no per-org filtering to get right in the first place.

`purge_read_notifications` is not the same kind of case, despite its own docstring's claim that "RLS
does not need a per-org context here." `notification` **is** in `ORG_SCOPED_TABLES`
(`backend/common/rls/__init__.py`) and gets the same `FORCE ROW LEVEL SECURITY` policy as every other
org-scoped table, and this task never calls `set_rls_context`. On the non-superuser database role
this project requires, it therefore runs with an empty RLS context, and its `DELETE` matches zero
rows, so instead of purging old read notifications across every org, it silently purges nothing at
all. This is exactly the failure mode described above, not an exception to it: a live bug in this
codebase's own scheduled task, worth knowing if you're relying on it to keep the `notification` table
from growing unbounded.
