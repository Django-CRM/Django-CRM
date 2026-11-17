# Email and Celery

BottleCRM sends email (welcome messages, magic-link sign-in, comment mentions) and runs background
and scheduled work (recurring invoices, SLA scanning, token cleanup) through Django's email
framework and Celery, respectively. This page covers how to configure both and the one rule every
background task that touches tenant data has to follow.

## Email backend

`backend/crm/settings.py` builds `MAILERS["default"]` from `EMAIL_BACKEND`, which defaults to
`django.core.mail.backends.console.EmailBackend`: outbound mail is printed to the process's stdout
rather than delivered anywhere. `.env.example` sets `EMAIL_BACKEND` to that console backend
explicitly; `.env.docker` doesn't set `EMAIL_BACKEND` at all (only a comment noting the console
backend is what's active for local development), so it relies on the same default rather than
setting it. To send real email, set `EMAIL_BACKEND` to a working Django email backend:
Resend over HTTPS (recommended for Render; see [Resend](#resend-recommended-for-render)),
SMTP (see [SMTP and Mailtrap](#smtp-and-mailtrap) below), or
`django_ses.SESBackend` (see [Amazon SES](#amazon-ses)) for legacy outbound setups.

For `ENV_TYPE=prod`, `backend/crm/server_settings.py` selects Resend for outbound email and
Tigris-compatible S3 storage for uploaded files. It requires `AWS_BUCKET_NAME`,
`TIGRIS_ACCESS_KEY_ID`, `TIGRIS_SECRET_ACCESS_KEY`, `RESEND_API_KEY` and a verified
`DEFAULT_FROM_EMAIL`. `SENTRY_DSN` is optional. The S3 endpoint defaults to
`https://t3.storage.dev` and region `auto`; override `AWS_S3_ENDPOINT_URL` and
`AWS_S3_REGION_NAME` only when using another S3-compatible provider. Production
keeps file objects private and generates signed URLs. SES/SNS inbound email remains
available separately; its receipt-rule and SNS configuration is not removed.

`DEFAULT_FROM_EMAIL` (default `noreply@localhost`) is the sender address used on every outbound
message; `ADMIN_EMAIL` (default `admin@localhost`) is separate and is what
`manage.py create_default_admin` uses for the seeded admin account, not a mail setting.

## Resend (recommended for Render)

Godan includes a Resend HTTPS API backend at
`common.email_backends.resend.ResendEmailBackend`. It implements Django's
standard email backend interface, so the existing application email flows keep
using `EmailMessage` / `EmailMultiAlternatives` without provider-specific
changes in each app. It uses the existing `requests` dependency and does not
open an SMTP connection.

Create a sending-only API key in Resend and verify a domain you control before
sending to customers. Publish the exact DNS records Resend provides for domain
verification, DKIM and SPF; configure DMARC for the domain as well. Use a sender
address on the verified domain. Do not use the shared onboarding sender for
production traffic.

Configure the Render web service (and every Celery worker service) with:

```bash
EMAIL_BACKEND="common.email_backends.resend.ResendEmailBackend"
RESEND_API_KEY="re_..."
DEFAULT_FROM_EMAIL="Godan <notifications@mail.yourdomain.com>"
```

For Render production, set `ENV_TYPE=prod`; production settings select Resend
and Tigris automatically. Add the storage credentials and Resend variables to
Render before deploying. The application refuses to start with a missing bucket,
Tigris credential, Resend key or verified sender rather than falling back to
local disk or console email. Keep the replacement Tigris secret and Resend key
in Render's environment only. Do not map Tigris credentials to the global
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` variables: those remain reserved
for optional AWS SES inbound S3 retrieval and must be real AWS credentials.

The backend supports Django plain-text and HTML alternatives, CC, BCC,
Reply-To, custom headers and attachments. It raises provider/API failures when
`fail_silently=False`; application tasks that intentionally catch delivery
exceptions still log their failures. A successful API response means Resend
accepted the message, not that the recipient's mailbox accepted or displayed it.
Use Resend's email event logs and delivery webhooks for delivery, bounce and
complaint monitoring.

This integration covers **outbound** email. The existing inbound support-email
pipeline uses AWS SES receipt rules and SNS; selecting Resend for outbound
messages does not migrate that inbound pipeline. Keep its AWS configuration
only if you use inbound email, or plan a separate Resend inbound-webhook
migration before removing SES resources.

## SMTP and Mailtrap

Set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend` to send through any SMTP server.
`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`,
`EMAIL_USE_SSL` and `EMAIL_TIMEOUT` are read into `MAILERS["default"]["OPTIONS"]`; `EMAIL_PORT` and
`EMAIL_TIMEOUT` are left out of `OPTIONS` entirely when unset rather than sent as `0`, so Django
picks the port itself: 465 with `EMAIL_USE_SSL`, 587 with `EMAIL_USE_TLS`, otherwise 25. An unset
or empty `EMAIL_HOST` means `localhost`, for a local relay. Production (`ENV_TYPE=prod`)
intentionally selects Resend; SMTP settings are for development and staging.

Mailtrap is one SMTP provider among others; nothing in this codebase is Mailtrap-specific, this is
just a worked example with real host/port values.

### Mailtrap Email Sandbox (development and staging)

The Sandbox captures outbound mail in an inbox you view in the Mailtrap UI instead of delivering it
to the real recipient, which is useful for a shared dev or staging deployment where you don't want
test traffic reaching real customer addresses. From a Sandbox inbox's SMTP settings:

```bash
EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST="sandbox.smtp.mailtrap.io"
EMAIL_PORT=2525
EMAIL_HOST_USER="<inbox username>"
EMAIL_HOST_PASSWORD="<inbox password>"
EMAIL_USE_TLS=True
EMAIL_TIMEOUT=10
```

Once mail is landing in the inbox, its HTML Check and Spam Analysis tabs are worth running against
this project's email templates (welcome, magic-link, portal sign-in, case reply) since they render
across more email clients than a browser preview does. Use a separate Sandbox inbox per environment
(dev, staging, CI) rather than sharing one, or messages from one environment's run show up mixed
into another's.

### Mailtrap Email Sending (production)

Email Sending delivers to the real recipient and requires verifying the sending domain in the
Mailtrap dashboard first; set `DEFAULT_FROM_EMAIL` to an address on that verified domain, or the
provider will reject the send. Its SMTP settings:

```bash
EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST="live.smtp.mailtrap.io"
EMAIL_PORT=587
EMAIL_HOST_USER="api"
EMAIL_HOST_PASSWORD="<API token>"
EMAIL_USE_TLS=True
```

As with any SMTP provider, `send_welcome_email`, `send_magic_link_email` and `send_portal_login_email`
in `backend/common/tasks.py` log an SMTP failure rather than raising (see below), so check the
worker's logs rather than the API response if an expected email never arrives.

## Amazon SES

Once `MAILERS["default"]["BACKEND"]` resolves to `django_ses.SESBackend`, whichever of the two paths
above got you there; `crm/settings.py` reads `AWS_SES_REGION_NAME` (default `ap-south-1`) and
`AWS_SES_REGION_ENDPOINT` (default `email.{AWS_SES_REGION_NAME}.amazonaws.com`, computed from
whatever you set for the region). AWS credentials are read implicitly by `django-ses`/`boto3` from
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` if you set them; if you don't, `boto3` falls back to
IAM role credentials, which is the intended path when running inside AWS (EC2/ECS instance role)
rather than with long-lived access keys in the environment.

The Celery tasks that actually send mail; `send_welcome_email`, `send_magic_link_email` and
`send_portal_login_email` in `backend/common/tasks.py`: catch `botocore.exceptions.ClientError` and
`OSError` (which covers `smtplib.SMTPException` and connection failures from the SMTP backend above)
around the `msg.send()` call and log the failure rather than letting the task raise, specifically
because a mail provider can reject a send (an unverified sender identity, a suppressed recipient
address, being outside the SES sandbox, bad SMTP credentials) in ways that are the provider's
problem, not a bug in the task. Check the worker's logs, not the Celery task result, if an expected
email never arrives.

## Broker and result backend

`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` both default to `redis://localhost:6379/0` in
`crm/settings.py`, and both `.env.example` and `.env.docker` leave them at that default (Docker
Compose's value points at the `redis` service hostname instead of `localhost`). Both need a reachable
Redis instance: `backend/crm/celery.py` constructs `Celery("crm")` and calls
`app.config_from_object("django.conf:settings", namespace="CELERY")`, so these two Django settings
are what Celery actually connects with; there's no separate Celery-specific config file.

### Upstash Redis (managed broker)

For a hosted deployment, BottleCRM can use [Upstash Redis](https://upstash.com/) for both
the Celery broker (queued jobs) and result backend. Set **one** environment variable on
the Django web service and every Celery process:

```bash
UPSTASH_REDIS_URL="rediss://default:<TOKEN>@<HOST>:6379"
```

Copy the **TCP** connection string from Upstash Console → your database → **Connect → TCP**.
Do not use the REST URL or REST token. The application requires `rediss://`, a hostname,
port and password, and forces `ssl_cert_reqs=required` so TLS certificate verification
cannot be disabled accidentally. Never commit the real URL or paste it into issue comments
or chat.

When `UPSTASH_REDIS_URL` is set, it becomes the default for both
`CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`. Explicitly configured
`CELERY_BROKER_URL` or `CELERY_RESULT_BACKEND` values take precedence, so remove old
localhost values from the hosted service or set both explicitly to the Upstash TCP URL.
Keep the Upstash database dedicated to BottleCRM; do not run `FLUSHDB` or `FLUSHALL`.

The result backend expires task metadata after one day by default
(`CELERY_RESULT_EXPIRES=86400`). The broker visibility timeout defaults to one hour
(`CELERY_VISIBILITY_TIMEOUT=3600`). Increase the visibility timeout if you have tasks
that legitimately run longer, and ensure the task is safe to retry because a worker
crash can cause a message to be delivered again. The Redis blocking-pop interval defaults
to 10 seconds (`CELERY_REDIS_POLLING_INTERVAL=10`): a newly published task wakes the
worker immediately, while an idle queue causes fewer polling commands. This option is
supported by the project's Kombu 5.6+ dependency. Worker prefetch is limited to one task
at a time. The worker retries broker connection failures during startup. Monitor Upstash
command and storage usage: even a long-lived idle worker uses Redis commands, so a free-tier
command allowance can still be exhausted.

Upstash stores the queue; it does **not** execute tasks. At least one continuously running
Celery worker is still required. A separate Celery Beat process is required for the
scheduled jobs listed below. Do not run multiple Beat instances against the same schedule
unless you have deliberately coordinated them.

### Render worker service

If you deploy the worker on Render, create a separate **Background Worker** using the same
repository, branch, Dockerfile and backend environment variables as the web service. Use
this Docker command:

```bash
celery -A crm worker --loglevel=INFO --pool=solo --concurrency=1
```

Set `SKIP_DJANGO_STARTUP_TASKS=true` on that worker service. The shared Docker entrypoint
will still wait for PostgreSQL, but will not run migrations or collect static files from
the worker. Keep migrations on the Django web service only.

For a separate Beat service, use:

```bash
celery -A crm beat --loglevel=INFO --schedule=/tmp/celerybeat-schedule
```

Run exactly one Beat instance. Render does not offer a free Background Worker, so this
layout requires a paid worker or another always-on host. A free Render web service that
spins down is not a reliable place to run a Celery worker or Beat. If you do not have a
worker yet, the authentication email dispatcher can fall back to inline delivery, but
other queued jobs and scheduled tasks will not execute until a worker/Beat process is
running.

## Running a worker

```bash
cd backend
uv run celery -A crm worker --loglevel=INFO
```

This process executes tasks queued by the API: sending the emails above, mention notifications,
team-membership propagation, and more. A separate `celery beat` process is required for anything on
a schedule: `backend/crm/celery.py` registers `app.conf.beat_schedule` with fifteen periodic
entries, including recurring-invoice generation and overdue/expired-estimate checks (daily),
outbound webhook retries and the [waiting-lead reminder](../features/lead-alerts.md#the-waiting-reminder)
(every minute), SLA breach scanning for cases (every 5 minutes),
stale-timer cleanup (every 30 minutes), retainer invoices (hourly, at 20 past: each project
retainer's month is drafted within the hour after the org's own midnight), and nightly cleanup of
read notifications, expired refresh-token records, webhook delivery logs and automation runs:

```bash
cd backend
uv run celery -A crm beat --loglevel=INFO
```

Neither process is required for the API itself to answer requests, only for background and
scheduled work to actually run. A running `beat` keeps the schedule it started with, so restart it
after an upgrade that adds an entry, or the new job never fires. `docker-compose.yml` runs both as their own services,
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
