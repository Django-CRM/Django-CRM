# Multi-tenancy and RLS

This page is for anyone about to add an org-scoped model, or trying to understand why one query
returned no rows when it should have returned some. It explains the tenancy model from the
application-code side; [PostgreSQL and RLS](../self-hosting/postgresql-and-rls.md) covers the
same mechanism from the operator's side: creating the database role, running `manage_rls`, and
proving isolation holds against real data. Read that page too before you deploy anything; this one
is about what to do when you're writing a new model.

## The model

Every tenant's data lives in the same database, the same tables, distinguished by an `org_id`
column. Isolation is enforced twice, independently:

1. **In application code**, every queryset that touches an org-scoped table filters explicitly,
   `Model.objects.filter(org=request.profile.org)`, and every `create()`/`serializer.save()`
   passes `org=request.profile.org` rather than trusting a client-supplied value.
2. **In PostgreSQL**, via Row-Level Security. Each org-scoped table carries a policy that compares
   its `org_id` column to the session variable `app.current_org`
   (`backend/common/rls/__init__.py`), which `RequireOrgContext` sets from the caller's validated
   JWT on every request: see [Overview](overview.md#request-lifecycle) for exactly when.

Neither layer is optional, and neither is a superset of the other in practice: see
[The two-layer contract](#the-two-layer-contract) below for why both have to hold.

## BaseOrgModel

`BaseOrgModel` (`backend/common/base.py`) is a `BaseModel` subclass that adds an `org` foreign key
to `common.Org`, an `OrgScopedManager` (giving you `Model.objects.for_org(org)` and
`Model.objects.for_request(request)` helpers), and a `["org", "-created_at"]` index. Its own
docstring states the intent plainly: "All tenant-owned models should inherit from this instead of
`BaseModel`."

That said, verify this against the actual model files before assuming every org-scoped model
follows it, because most don't. Searching the codebase for classes that actually inherit
`BaseOrgModel` turns up four: `Order` and `OrderLineItem` (`backend/orders/models.py`), and
`PersonalAccessToken` and `PackApplication` (`backend/common/models.py`). Every core CRM model,
`Lead`, `Account`, `Contact`, `Opportunity`, `Case`, `Task`, and most of the supporting tables
around them (`CaseWatcher`, `CsatSurvey`, `Macro`, `BusinessCalendar`, and others) instead inherit
plain `BaseModel` and declare their own `org = models.ForeignKey(Org, on_delete=models.CASCADE,
related_name="...")` field by hand, shaped identically to what `BaseOrgModel` would have given
them. This isn't drift or oversight: several of these models say so directly in their own
docstrings, citing a coordination decision to prefer a hand-declared FK over `BaseOrgModel`. For
example, `backend/cases/models.py` on `CaseWatcher`:

```python
class CaseWatcher(BaseModel):
    """A profile subscribed to updates on a case.

    Per `docs/cases/COORDINATION_DECISIONS.md` D2 we inherit BaseModel and
    declare our own org FK rather than using BaseOrgModel. RLS still
    applies via the migration that adds the `case_watcher` table.
    """
```

(The `docs/cases/COORDINATION_DECISIONS.md` file that comment and two others like it, in
`backend/business_hours/models.py` and `backend/macros/models.py`. Cite does not exist in this
checkout; treat the reasoning captured in the code comments themselves as the source of truth.)

The practical takeaway: inheriting `BaseOrgModel` is a correct and recommended way to add an `org`
field to a new model. Doing so also gets you the `OrgScopedManager` helpers
(`Model.objects.for_org(org)`, `Model.objects.for_request(request)`) for free, which is itself a
reason to prefer it, and you should default to it unless you have a specific reason to match this
codebase's more common hand-rolled-FK convention instead (for consistency with a sibling model in
the same app, for instance). But inheriting it is not what makes RLS protection work,
and it is not sufficient on its own; `PersonalAccessToken` inherits `BaseOrgModel` and is
*deliberately excluded* from RLS (see [Policies and ORG_SCOPED_TABLES](#policies-and-org_scoped_tables)
below). What actually determines whether a table is protected is the next section.

## Policies and ORG_SCOPED_TABLES

`ORG_SCOPED_TABLES` in `backend/common/rls/__init__.py` is the single list that determines which
tables get RLS policies. At the time of writing it has 61 entries. `get_enable_policy_sql(table)`
generates the SQL that protects each one:

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

`FORCE ROW LEVEL SECURITY` matters because without it the table owner, typically the role that ran
migrations, would be exempt from its own table's policies. `NULLIF` turns an unset (empty-string)
session variable into SQL `NULL`, and `org_id::text = NULL` is never true, so an empty context
yields zero rows rather than every org's rows: fail-safe by construction, not by convention.

Adding RLS to a new org-scoped model is exactly three steps, and skipping any one of them leaves
the table looking protected while it isn't:

1. **Give the model an `org` field.** Inherit `BaseOrgModel`, or: following this codebase's
   dominant pattern, see [BaseOrgModel](#baseorgmodel) above: declare `org =
   models.ForeignKey(Org, on_delete=models.CASCADE, related_name="...")` directly.
2. **Add the table's `db_table` name to `ORG_SCOPED_TABLES`** in `backend/common/rls/__init__.py`.
3. **Write a migration** that calls `get_enable_policy_sql('your_table')` inside a `RunPython`
   operation, guarded so it's a no-op on SQLite (the test database has no concept of row security):

Here is a real one, `backend/opportunity/migrations/0012_enable_rls_sales_goal.py`, in full:

```python
from django.db import connection, migrations

from common.rls import get_disable_policy_sql, get_enable_policy_sql

TABLE = "sales_goal"


def enable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(get_enable_policy_sql(TABLE))


def disable_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(get_disable_policy_sql(TABLE))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("opportunity", "0011_opportunity_kanban_order"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
```

`get_enable_policy_sql` opens with the two `ALTER TABLE` statements, then. Before either
`CREATE POLICY`: drops both policies with `DROP POLICY IF EXISTS`, so re-running this kind of
migration is idempotent. Useful if a table's policy needs to be recreated after a schema change.

Skip step 2 and the consequence is worse than "no protection": `manage_rls --status` only reports
on tables that are actually in `ORG_SCOPED_TABLES`, so an unregistered table doesn't show up as
"disabled". It doesn't show up at all, and the gap is invisible to the one tool that's supposed to
catch it. The one deliberate exception in this codebase is `personal_access_token`: it inherits
`BaseOrgModel` (so it has an `org` field) but is intentionally left out of `ORG_SCOPED_TABLES`,
because it's looked up by `token_hash` before any tenant context can exist, an "auth-bootstrap"
table, in the same category as `Org` itself. Its isolation is enforced instead by explicit
`org`/`profile` filters in `backend/common/views/pat_views.py`. That exception is the proof that
inheriting `BaseOrgModel` and being RLS-protected are two separate facts, not one.

## The two-layer contract

RLS is a safety net, not a replacement for the ORM filter. Every view and serializer still writes
`Model.objects.filter(org=request.profile.org)` and passes `org=request.profile.org` explicitly on
create. That ORM filter is the contract; RLS is what catches the case where a filter is missing,
forgotten, or wrong. The two layers fail differently, which is exactly why both are required:

- If the ORM filter is missing but RLS is correctly configured (a non-superuser database role, see
  [PostgreSQL and RLS](../self-hosting/postgresql-and-rls.md#why-rls)), the query still returns
  only the caller's org's rows, RLS silently saved you.
- If the database role is a superuser, which is the default (`DBUSER` unset falls back to
  `postgres`) unless you deliberately change it. RLS is bypassed entirely, silently, with no error
  and no log line. In that configuration, the ORM filter is the *only* thing standing between a
  request and another org's data. A development environment where the DB role happens to be a
  superuser will look identical whether the ORM filter is present or not; only
  `manage_rls --verify-user` against a real, correctly-configured role tells you which one you're
  actually running.

Treat every new queryset the same way regardless of which layer you're thinking about: write the
`org=` filter as if RLS didn't exist, because on a misconfigured deployment, it doesn't.

## Portal tokens

!!! note "Which portal"
    This section is about the **invoice, estimate and CSAT links**, where the URL is the whole
    credential. The [customer portal](../api/customer-portal.md), where a customer signs in to read
    their support cases, does not use this mechanism and does not need it: its token carries an
    `org_id` claim, so `GetProfileAndOrg` resolves the org before `RequireOrgContext` runs and the
    request gets a real RLS context like any other. The one exception is its two anonymous sign-in
    endpoints, which are prefix-exempt at `/api/portal/login/` and set the context themselves from
    the org id in the URL. Nothing there reads an org-scoped row before the context is set.

The client portal (invoice and estimate links emailed to customers, and CSAT survey links) is the
one place in this codebase that has to read org-scoped rows with no authenticated user and no JWT
at all. That's a genuine chicken-and-egg problem for RLS: you cannot set `app.current_org` until
you know the org, and with RLS enforcing isolation, you cannot read the row that would tell you the
org until the context is already set.

`common.models.PortalAccessToken` (`backend/common/models.py`, defined starting at line 910) breaks
the cycle. It's a small, deliberately **unscoped** table: absent from `ORG_SCOPED_TABLES`, no RLS
policy. That maps `sha256(url_token)` to an `org_id`, populated at the same moment each portal
token is minted (`common/portal_tokens.py:register_portal_token`). A public view hashes the token
it was handed in the URL, looks up the org in this unscoped table, and only then sets the RLS
context and queries the actual resource:

```python
# backend/invoices/public_views.py
def _resolve_org_context(token, resource_type):
    org_id = resolve_portal_org(token, resource_type)
    if org_id:
        set_rls_context(org_id)
    return org_id
```

An unknown or malformed token leaves the RLS context empty; the subsequent scoped query then
returns nothing under RLS's fail-safe default, and the caller gets the same 404 a disabled link
would produce. A stranger probing token values learns nothing about whether one is real. This is
the same "resolve org first via an unscoped, narrow lookup, then call `set_rls_context`" pattern
Celery tasks use (see [Background jobs](background-jobs.md)); it adds a dedicated, narrow surface
rather than weakening the isolation policy on `invoice`, `estimate`, or `csat_survey` themselves.
`backend/docs/PORTAL_RLS.md` documents the reasoning and the alternatives that were deliberately
rejected (notably, granting the anonymous path `BYPASSRLS`), but its "what is still open" section
describes the state *before* `PortalAccessToken` existed; the resolution above is what actually
runs today.
