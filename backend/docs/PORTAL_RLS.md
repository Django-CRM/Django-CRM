# The client portal and RLS

The public endpoints: the invoice and estimate links emailed to customers,
and the CSAT survey link, are the only anonymous, org-less requests that need
to read org-scoped rows. That combination puts them at odds with RLS, and this
note records where that stands.

## What was wrong, and what is now fixed

`RequireOrgContext.EXEMPT_PATHS` listed `/api/public/csat/` but not
`/api/public/invoice/` or `/api/public/estimate/`. `_is_exempt` is a
`startswith` check, so the CSAT entry did not cover its siblings, and the
middleware returned

```
403 {"detail": "Organization context is required. Please login again."}
```

to every anonymous caller. That is, to every customer who clicked a link. The
middleware runs before the view, so token validity never entered into it.

**Fixed**: both prefixes are now exempt. Regression tests live in
`invoices/tests/test_invoices_api.py::TestPublicPortalIsReachableAnonymously`
and go through the full Django test client. That matters: the pre-existing
view tests use `APIRequestFactory` and call the view directly, bypassing
MIDDLEWARE, which is why they passed throughout the outage.

## What is still open

Reachability is not readability. `_set_org_context` returns early when
`request.org is None`, so a public request runs with `app.current_org` empty,
and the isolation policy built by `common.rls.get_enable_policy_sql` is

```sql
USING (org_id::text = (select NULLIF(current_setting('app.current_org', true), '')))
```

Empty context yields `NULL`, which matches nothing. `invoice`, `estimate` and
`csat_survey` are all in `ORG_SCOPED_TABLES`.

Measured directly as `crm_user` (non-superuser, no `BYPASSRLS`) with empty
context:

| table         | rows visible |
| ------------- | ------------ |
| `invoice`     | 0            |
| `estimate`    | 0            |
| `csat_survey` | 0            |

So on a correctly configured production database, these endpoints now return
**404 instead of 403**. The customer still cannot see their invoice.

This is masked in development, where `DBUSER=postgres`. A superuser, which
bypasses RLS entirely, so every public endpoint appears to work. Do not take a
green dev run as evidence.

**This applies to CSAT too.** `cases/csat_views.py::_load_survey` does its
initial `CsatSurvey.objects.filter(token_hash=...)` with no context set, and
only calls `set_rls_context(survey.org_id)` afterwards. The lookup itself is
subject to the same policy. CSAT should be re-tested against `crm_user` rather
than assumed healthy.

## The shape of the remaining fix

The problem is a chicken-and-egg: you cannot set `app.current_org` until you
know the org, and you cannot read the row that tells you the org until the
context is set. Three ways out, in the order they should be considered:

1. **An unscoped token→org lookup.** A small table (or an unscoped column on
   an existing unscoped table) mapping `token_hash` → `org_id`, deliberately
   *not* in `ORG_SCOPED_TABLES`. The view resolves the org, calls
   `set_rls_context(org_id)`, then queries normally under full RLS. This adds
   a surface rather than weakening one, and it leaks only the existence of a
   token to somebody who already holds it. Requires a migration.

2. **A dedicated lookup role.** A Postgres role permitted to select
   `public_token`/`token_hash` and `org_id` and nothing else. Narrower than
   option 3, but it still means a second connection path and another role to
   keep correct.

3. **A policy exception** allowing SELECT by token with empty context. Cheapest
   to write and the easiest to get wrong: it widens the isolation policy on
   the invoice and estimate tables themselves, which are exactly the tables
   whose isolation matters. Not to be done without an explicit decision.

Do not reach for `BYPASSRLS` here. That role exists for the superadmin
dashboard and every addition to its surface is a step toward the escalation
path the threat model is built around.

## The inbound email webhook uses the same lookup

`POST /api/cases/inbound/<mailbox_id>/` had both defects at once until 1.13.0:
`RequireOrgContext` answered 403 to every SNS delivery, and past that, the view
read `inbound_mailbox` before it set any context and answered 404. The route is
now exempt by URL name (`RequireOrgContext.EXEMPT_VIEW_NAMES`, deliberately not
a prefix), and the view resolves the org from `portal_access_token` (resource
type `inbound_mailbox`, key `sha256` of the canonical mailbox id) before it sets
the context and reads the mailbox. Rows are registered on mailbox create and
removed on delete (`cases/signals.py`); `common/0049` backfilled existing
mailboxes org by org. Tests: `cases/tests/test_inbound_webhook_reachability.py`,
including `postgres_only` cases that assert the empty context hides the mailbox
before proving the delivery lands.

## What must not be done

- Do not make the app's DB user a superuser to "fix" the portal. That disables
  RLS for the entire application.
- Do not broaden the exemption to `/api/public/` or `/api/`. The two prefixes
  are specific so that anything mounted there later has to opt in.
- Do not test this with `APIRequestFactory`. The bug lived in middleware; a
  test that skips middleware cannot see it.
