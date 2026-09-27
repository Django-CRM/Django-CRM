# PostgreSQL and RLS

BottleCRM's multi-tenancy is not just an application-layer convention. Every org-scoped table is
protected by PostgreSQL Row-Level Security (RLS), configured centrally in
`backend/common/rls/__init__.py` and enabled by Django migrations. This page explains why that
matters, how to set up the database role RLS depends on, how policies get applied, and how to
verify isolation is actually working rather than assuming it is.

## Why RLS

Every RLS-protected table carries an `org_id` column, and PostgreSQL, not just the Django ORM,
refuses to return or accept rows whose `org_id` doesn't match the current session's org context.
That context is the PostgreSQL session variable `app.current_org`, set by application middleware
(see [Verifying isolation](#verifying-isolation) below) from the caller's authenticated org. The
policies in `get_enable_policy_sql()` apply this filter with `USING (org_id::text = (select
NULLIF(current_setting('app.current_org', true), '')))`, so it's enforced even for a raw SQL
query issued outside the ORM, a query in a Django view that forgot its `.filter(org=...)`, or a
bug in application code. This is defense in depth: `Model.objects.filter(org=request.profile.org)`
in application code is still the contract, RLS is the safety net underneath it.

**The single most important fact in this section: a PostgreSQL superuser role bypasses RLS
entirely, and it does so silently, no error, no log line, nothing.** `manage_rls.py`'s own
`--status` output says as much: `Database user "X" is a SUPERUSER - RLS will be bypassed!`
Every policy still exists and looks correctly configured; a superuser connection simply ignores
them. If your application's database role is a superuser, and the default in
`backend/crm/settings.py` (`DBUSER` defaulting to `postgres`) is exactly that unless you override
it. Every RLS policy in your schema is dead weight and tenant isolation depends entirely on
every application code path remembering its own org filter. There is nothing in this codebase
that detects or warns about this at request time; it is only visible if you deliberately run
`manage_rls --verify-user` or `--status`.

## Creating the application role

The application must connect as a **non-superuser** PostgreSQL role. Connected as the `postgres`
superuser (or an equivalent admin account):

```sql
CREATE DATABASE crm_db;
CREATE USER crm_user WITH PASSWORD 'crm_password';
GRANT ALL PRIVILEGES ON DATABASE crm_db TO crm_user;
\connect crm_db;
GRANT ALL ON SCHEMA public TO crm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO crm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO crm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO crm_user;
```

(This mirrors what `docker/postgres/init-rls-user.sql` runs automatically for the Docker Compose
setup, see [Docker](docker.md), except that script also runs unconditionally, wrapped in a `DO
$$ ... IF NOT EXISTS ...` block so re-running it on an existing database is safe.)

Granting schema and table privileges (`GRANT ALL ON SCHEMA`, `GRANT ALL ... ON TABLES`) does
**not** weaken RLS: those privileges govern DDL and raw read/write access; only the
`BYPASSRLS` role attribute or superuser status bypasses row-level policies. Do not grant
`BYPASSRLS` to the application role; nothing in this codebase needs it, and there is no
superadmin-style cross-org dashboard in this repository that would justify it.

Point Django at the new role, `DBNAME`, `DBUSER`, `DBPASSWORD`, `DBHOST`, `DBPORT` in your `.env`
(see [Manual setup](../getting-started/manual-setup.md)) or `.env.docker`/`.env.docker.local`
(see [Docker](docker.md)), then confirm it landed correctly:

```bash
cd backend
uv run python manage.py manage_rls --verify-user
```

This runs `SELECT usename, usesuper, usecreatedb FROM pg_user WHERE usename = current_user` and
prints `Is superuser: False` (and a success line) when configured correctly, or an explicit
`WARNING: Superusers bypass RLS!` with remediation SQL when it isn't.

## Enabling policies

RLS is enabled and disabled **by Django migrations, not by any management command**,
`manage_rls.py`'s own module docstring says so explicitly. Running migrations is what actually
applies policies:

```bash
cd backend
uv run python manage.py migrate
```

The set of tables that get policies is centralized in `ORG_SCOPED_TABLES` in
`backend/common/rls/__init__.py`. Read the count from that list rather than from here: it moves in
both directions (`security_audit_log` was removed from it when `common/0036` dropped its policies,
which is why an earlier draft of this page said 61). The list spans core
records (`lead`, `accounts`, `contacts`, `opportunity`, `case`, `task`, `invoice`, ...), supporting
data (`comment`, `attachments`, `document`, `activity`, `tags`, ...), Kanban boards and pipeline
stage tables, invoicing/estimate/recurring-invoice tables, and more. One deliberate exception:
`personal_access_token` is **not** in `ORG_SCOPED_TABLES`. It's looked up by token hash before any
tenant context exists (an "auth-bootstrap" table, the comment in that file calls it, mirroring the
`Org` table itself), and its isolation is enforced instead by explicit `org`/`profile` filters in
`backend/common/views/pat_views.py`.

For each table, `get_enable_policy_sql(table)` runs:

```sql
ALTER TABLE "<table>" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "<table>" FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS org_isolation ON "<table>";
DROP POLICY IF EXISTS org_insert_check ON "<table>";

CREATE POLICY org_isolation ON "<table>"
    FOR ALL
    USING (org_id::text = (select NULLIF(current_setting('app.current_org', true), '')));

CREATE POLICY org_insert_check ON "<table>"
    FOR INSERT
    WITH CHECK (org_id::text = (select NULLIF(current_setting('app.current_org', true), '')));
```

The two `DROP POLICY IF EXISTS` statements are why re-running `manage.py migrate` after an
interrupted run is safe rather than something that fails on a duplicate policy. See
[Troubleshooting](troubleshooting.md#migrations-fail) and
[Backups and upgrades](backups-and-upgrades.md#migrations).

`FORCE ROW LEVEL SECURITY` matters specifically because the table owner (typically the role that
ran the migrations) would otherwise be exempt from its own table's RLS policies by default,
forcing it closes that gap without requiring a separate, non-owning application role.

To add RLS to a new org-scoped model: inherit from `BaseOrgModel`, add the table name to
`ORG_SCOPED_TABLES`, and write a migration that calls `get_enable_policy_sql('your_table')` inside
a `RunPython` operation (guarded with `if schema_editor.connection.vendor != 'postgresql': return`,
since SQLite has no concept of row security).

## Verifying isolation

`manage_rls` (`backend/common/management/commands/manage_rls.py`) is a diagnostic command with
three flags, each proving something different, none of them modify the database:

```bash
cd backend
uv run python manage.py manage_rls --status
uv run python manage.py manage_rls --verify-user
uv run python manage.py manage_rls --test
```

- **`--status`** proves *which tables have policies applied, and whether the connected role is a
  superuser*. It queries `current_user`/`usesuper` from `pg_user`, then for every table in
  `ORG_SCOPED_TABLES` checks `pg_class.relrowsecurity`/`relforcerowsecurity` and prints
  `ENABLED (forced)`, `disabled`, or `TABLE NOT FOUND`, ending with an `Enabled: N, Disabled: N`
  tally.
- **`--verify-user`** proves *the connected database role cannot bypass RLS*. It's the
  `--status` command's superuser check in isolation, with explicit remediation SQL printed if the
  role turns out to be a superuser.
- **`--test`** proves *isolation actually holds for real data*, not just that policies exist. It
  picks up to two orgs that have `lead` rows (falling back to the first two orgs in the database
  if none has leads yet, and skipping entirely if fewer than two orgs exist at all), sets
  `app.current_org` to each in turn and counts visible leads, then sets the context to an **empty
  string** and counts again. Seeing non-zero counts for the two real orgs and zero for the empty
  context is the actual proof of isolation the other two flags can't give you.

**Why the empty-context count matters:** the session variable is `app.current_org`, and it's set
by `RequireOrgContext` (`backend/common/middleware/rls_context.py`, wired into `MIDDLEWARE` in
`backend/crm/settings.py`) from `request.org`, which is itself derived earlier in the same
middleware chain by `GetProfileAndOrg` (`backend/common/middleware/get_company.py`) from the
`org_id` claim inside the caller's signed JWT, validated against an active `Profile` for that
user in that org before it's trusted. (The file also defines a second, older middleware class,
`SetOrgContext`, referenced in that file's own module docstring, but `settings.py` wires
`RequireOrgContext` instead, which additionally rejects unauthenticated/context-less requests
with a 403 rather than silently proceeding without a context; `RequireOrgContext` is the class
actually enforced.) Every policy uses `NULLIF(current_setting('app.current_org', true), '')`,
when no context has been set, `current_setting` returns an empty string, `NULLIF` turns that into
SQL `NULL`, and `org_id::text = NULL` is never true. **No context means zero rows, by design**.
A fail-safe default, not an edge case to special-case around.

`RequireOrgContext` resets the variable to `''` after every request (`SELECT set_config
('app.current_org', '', false)`), specifically to stop it leaking between requests on pooled
connections.

**Celery workers never run this middleware.** Background tasks in `backend/common/tasks.py` that
touch org-scoped tables call `set_rls_context(org_id)`, also defined in `common/tasks.py`, as
their first line, before any ORM query:

```python
from common.tasks import set_rls_context

@shared_task
def my_task(data_id, org_id):
    set_rls_context(org_id)  # required before any org-scoped query
    ...
```

`set_rls_context` is a thin wrapper around the same `set_config('app.current_org', ...)` call the
middleware uses, with an early return on non-PostgreSQL connections (so it's a no-op under the
SQLite test settings). A task that skips this and queries an RLS-protected table runs with
whatever context the underlying connection happened to have, on a fresh worker connection, that's
empty, which (per the design above) returns zero rows rather than the wrong org's rows; but it is
still a bug worth catching, since "silently returns nothing" produces its own class of confusing
failures. Not every task in `common/tasks.py` needs this: `flush_expired_refresh_tokens` operates on
simplejwt's `OutstandingToken`, which has no `org` column and is not in `ORG_SCOPED_TABLES`, so no
context applies to it.

`purge_read_notifications` is the counter-example, and it is worth studying because it looks like the
same case and is not. Its docstring asserts that RLS needs no per-org context here, but `notification`
*is* in `ORG_SCOPED_TABLES`, so on the non-superuser role this page tells you to use, that task runs
with an empty context and its delete matches nothing. Read notifications are never purged. Take it as
the practical warning: a docstring claiming a task is exempt is not evidence, and the failure is
silent, the task reports success having done nothing at all.

## Common mistakes

- **Leaving the default database role as `postgres`.** Nothing enforces a non-superuser role for
  you; `backend/crm/settings.py`'s `DATABASES` setting defaults `DBUSER` to `postgres` if the
  environment variable is unset. That default is a superuser almost everywhere PostgreSQL is
  installed. Run `manage_rls --verify-user` after any environment change, not just once at setup.
- **Trusting a green local/dev run as proof RLS works.** If your development database user is a
  superuser (very common. It's the path of least resistance to "just get it running"), every
  endpoint will appear to isolate tenants correctly purely because application-level `.filter(org=
  ...)` calls happen to be correct, while RLS itself is doing nothing. Only `--verify-user` (or
  `--status`) surfaces this; functional testing cannot.
- **Forgetting `set_rls_context(org_id)` in a new Celery task** that queries an org-scoped model.
  See [Verifying isolation](#verifying-isolation) above.
- **Adding a new org-scoped model without registering it.** `BaseOrgModel` gives you the `org`
  foreign key, but RLS policies only get created for tables listed in `ORG_SCOPED_TABLES` with a
  migration that calls `get_enable_policy_sql`. A table can carry real `org_id` data and simply
  have no RLS protection if this step is skipped, `manage_rls --status` only reports on tables
  that are in that list in the first place, so an unregistered table won't even show up as
  "disabled."
- **Assuming the anonymous client-portal endpoints (invoice/estimate links, CSAT surveys) are
  either broken or unprotected. They're a deliberate, narrow exception, and testing them locally
  can be misleading either way.** `RequireOrgContext.EXEMPT_PATHS` in
  `backend/common/middleware/rls_context.py` exempts `/api/public/invoice/`,
  `/api/public/estimate/` and `/api/public/csat/` (among the other anonymous prefixes listed there)
  from requiring org context, because these are genuinely anonymous requests (a customer clicking
  an emailed link) authorized by a possession token rather than a JWT. The inbound email webhook
  (`/api/cases/inbound/<mailbox_id>/`) is exempt the same way, by URL name in
  `EXEMPT_VIEW_NAMES` rather than by prefix. See `backend/docs/PORTAL_RLS.md`. Exemption from *requiring* context
  is not the same as *setting* one: `_set_org_context` returns early when `request.org` is `None`,
  so these requests run with `app.current_org` empty, which, per the fail-safe design above,
  means the `invoice`/`estimate`/`csat_survey` rows the view is trying to read are invisible under
  RLS too, until the view itself resolves the org from the token and calls `set_rls_context`
  explicitly. This is exactly the kind of thing a superuser dev database masks: with `DBUSER=
  postgres`, RLS does nothing, so a locally-broken or locally-fine portal endpoint looks identical.
  Test portal endpoints against a real non-superuser role before trusting them.
