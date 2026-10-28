# Background jobs

This page is for anyone writing a new Celery task. If you're operating a deployment rather than
writing code, see [Email and Celery](../self-hosting/email-and-celery.md#rls-in-background-tasks)
instead. It covers running the worker and beat processes and has the same rule below from the
operator's side. This page exists because the rule is easy to get right by copying an existing
task and easy to get wrong by writing one from scratch.

## Topology

`backend/crm/celery.py` defines the app: `Celery("crm")`, configured from Django settings under the
`CELERY_` namespace, so `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`
(`backend/crm/settings.py`, both defaulting to `redis://localhost:6379/0`) are what it actually
connects with. There's no separate Celery-only config file. `app.autodiscover_tasks()` finds
`tasks.py` in each app; a second call, `app.autodiscover_tasks(related_name="celery_tasks")`,
exists specifically because the `tasks` Django app's own task module is named `celery_tasks.py`
to avoid colliding with the app's own name.

Two processes run against this one app definition, and neither is required for the API itself to
answer requests:

- **`celery -A crm worker`** executes tasks queued by the API, welcome emails, magic-link
  delivery, assignment notifications, and more.
- **`celery -A crm beat`** fires the schedule in `app.conf.beat_schedule` (fifteen entries:
  recurring invoice generation, overdue-invoice checks, payment reminders, expired-estimate
  checks, stale-opportunity and goal-milestone scans, case SLA-breach scanning every five minutes,
  stale-timer cleanup, webhook delivery retries, the waiting-lead reminder every minute
  (`leads.tasks.remind_waiting_leads`), retainer invoices hourly
  (`projects.tasks.draft_retainer_invoices`), and nightly cleanup of read notifications, expired
  refresh-token records, webhook delivery logs and automation runs). Nothing runs any of these
  unless `beat` is also running, and a running `beat` keeps the schedule it started with, so
  restart it after an upgrade that adds an entry.

See [Email and Celery](../self-hosting/email-and-celery.md#running-a-worker) for the actual run
commands and what each scheduled entry does operationally.

## Jobs and switched-off modules

An org admin can switch parts of the CRM off (see [Modules](../api/modules.md#scheduled-jobs)).
A job that sweeps every org checks `common.modules.module_is_available(org, key)` for the module
it serves and skips the org when it is off; a job queued for one record checks before it sends.
A module that comes back on starts fresh: the date-driven sweeps ignore dates before
`module_on_since(org, key)`, and `change_modules` runs the two start-fresh hooks listed in
`START_FRESH` (`backend/common/module_settings.py`) when a module becomes available again. A new
job that acts on dates, or emails a customer, belongs in the same pattern, with a test that a
date from the off period does nothing.

## Writing a task

Celery workers never go through Django's HTTP middleware stack. `GetProfileAndOrg` and
`RequireOrgContext`: the two middleware classes that establish tenancy for a normal request, see
[Overview](overview.md#request-lifecycle). Simply never run for a task, so nothing sets the
PostgreSQL session variable `app.current_org` on the worker's connection for you. A fresh worker
connection has an empty context, and per RLS's fail-safe design (see
[Multi-tenancy and RLS](multi-tenancy-and-rls.md#policies-and-org_scoped_tables)) an empty context
makes every org-scoped query on a correctly-configured, non-superuser database role return zero
rows, not another org's rows, but not the rows you wanted either.

The fix is one line, and it has to be the first thing a task that touches an org-scoped table does.
`set_rls_context(org_id)`, defined in `backend/common/tasks.py`, sets the same session variable the
middleware would have:

```python
# backend/contacts/tasks.py
from celery import shared_task

from common.tasks import set_rls_context
from contacts.models import Contact


@shared_task
def send_email_to_assigned_user(recipients, contact_id, org_id):
    """Send Mail To Users When they are assigned to a contact"""
    set_rls_context(org_id)
    contact = Contact.objects.get(id=contact_id)
    ...
```

Any task queued from a view already has `org_id` available (`request.profile.org.id`) to pass along
as an argument. Pass it, and call `set_rls_context` with it before the first query, every time.

A task that has to sweep every org rather than act on one record calls `set_rls_context` once per
org, inside the loop, immediately before that org's queries, for example
`cases.tasks.scan_for_breached_cases`:

```python
for org in Org.objects.filter(id__in=list(org_ids)):
    set_rls_context(org.id)
    try:
        total += _scan_org(org)
    except Exception:
        logger.exception("Escalation scan failed for org=%s", org.id)
```

Two kinds of task legitimately skip this call. First, a task whose table isn't org-scoped at all:
`flush_expired_refresh_tokens` operates on simplejwt's `OutstandingToken` table, which carries no
`org_id` column and isn't in `ORG_SCOPED_TABLES`, so there's no per-org context to set in the first
place. Second, a task that already sets context per-org inside a loop, as above, doesn't need it
again outside the loop. What doesn't qualify as an exception is "the query looks like it filters by
something else". See below.

## The RLS rule

Stated plainly, because it's the one rule this page exists to make impossible to miss: **any Celery
task that reads or writes an org-scoped table must call `set_rls_context(org_id)` before its first
query against that table.** Nothing else establishes tenancy for a worker process.

This codebase has a live example of what happens when a task skips it.
`purge_read_notifications` (`backend/common/tasks.py:381-397`) deletes read notifications older
than a cutoff:

```python
@shared_task
def purge_read_notifications(days=NOTIFICATION_PURGE_DAYS):
    """... Runs once across all orgs. RLS does not need a per-org context
    here because the query targets `read_at`, which is intrinsic to the row,
    not org-scoped logic."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = Notification.objects.filter(
        read_at__isnull=False, read_at__lt=cutoff
    ).delete()
    ...
```

The docstring's reasoning doesn't hold: whether a *query condition* is org-specific has nothing to
do with whether the *table* is org-scoped, and `notification` is. It's in `ORG_SCOPED_TABLES`
(`backend/common/rls/__init__.py`) and carries the same `FORCE ROW LEVEL SECURITY` policy as every
other table on that list. This task never calls `set_rls_context`, so on the non-superuser database
role this project requires, it runs with an empty `app.current_org`, and its `DELETE` matches zero
rows across every org, silently, on every scheduled run. It doesn't fail loudly and it doesn't
purge the wrong org's data. It just never purges anything, which is its own quietly bad outcome:
the table grows unbounded while the schedule entry that's supposed to prevent that keeps reporting
success. Read the code a task actually executes, not the comment sitting above it, before trusting
either one.
