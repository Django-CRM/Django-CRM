# Demo data and packs

BottleCRM has two independent ways to populate an organization with non-empty data: the
`seed_data` management command, which generates a large, randomized dataset for an entire org, and
**vertical packs**, which apply a small, curated, industry-specific set of pipelines and sample
records to an org that already exists.

## Seeding a demo organization

```bash
uv run python manage.py seed_data --email you@example.com
```

Run from `backend/` (or `docker compose exec backend python manage.py seed_data --email
you@example.com` against the Docker stack). This is the command referenced in
[Docker quick start](docker-quick-start.md) and [First sign-in](first-sign-in.md).

The command (`backend/common/management/commands/seed_data.py`) creates an organization and fills
it with realistic-looking fictional data, contacts, accounts, leads, opportunities, cases, tasks,
products, invoices, estimates, recurring invoices, sales goals, teams and tags, using curated
name/company/address pools and reserved demo domains (`example.com`, `.example`) so nothing it
generates resembles a real person or business. The **first** organization it creates is always
named **`MicroPyramid`**, specifically so local-dev workflows have a stable, known org name to
sign in against with `devlogin --org MicroPyramid`. An **admin profile** is created for whichever
`--email` you pass. Re-running the command with the same email reuses the existing org and user
rather than creating duplicates. It's safe to run more than once.

Counts for every entity type (`--leads`, `--accounts`, `--opportunities`, `--invoices`, and so on)
and locale (`--currency`, `--country`) are configurable flags; see `--help` for the full list. A
`--clear` flag deletes existing CRM data for re-seeding (but never users, orgs or profiles): it's
a separate, broader operation from the sample-data clearing described below, and is not scoped to
records a pack created.

## Vertical packs

A vertical pack is a small, declarative JSON manifest, not a data generator, that shapes an org
toward one industry: a starter pipeline (with stages) for leads, cases and tasks, and deal pipelines
for a pack that needs them; a handful of
custom field definitions; tags; products; some terminology overrides (for example relabeling
"Lead" as "Enquiry"); and a short list of named sample records (accounts, contacts, deals,
tickets, leads, tasks) that reference each other by name within the manifest.

Four packs ship in `backend/common/packs/manifests/`, one manifest file per pack:

| Pack id | File | Description |
|---|---|---|
| `real-estate` | `real-estate.json` | Agencies and brokers selling and letting residential and commercial property. |
| `professional-services` | `professional-services.json` | Agencies, consultancies and accounting firms selling billable work. |
| `education-admissions` | `education-admissions.json` | Coaching centres, training institutes and colleges managing admissions. |
| `events-weddings` | `events-weddings.json` | Wedding planners, event agencies and venues taking enquiries through to a delivered event. |

Every manifest is validated strictly at load time (`backend/common/packs/schema.py`). An unknown
key, an invalid `stage_type`, a `sample_data` reference to an account or stage name that isn't
declared elsewhere in the same pack, and similar mistakes all fail validation rather than being
silently ignored or half-applied.

## Applying a pack

Each pack is installed as an [industry app](../features/industry-apps.md), which records the
version an org has, lets an admin update it when the pack offers something new, and remove it
again. Installing runs `backend/common/packs/applier.py`'s `apply_pack()`, reachable through:

- **The UI**: Settings, then Apps (for an existing org), and the organization creation page (for a
  brand-new org), in the web app and the mobile app. On creation the picker asks "What kind of
  business is this?" and defaults to "Skip for now". It is hidden when the pack list cannot be
  loaded. An app that fails to install never undoes the new org.
- **The API**: `POST /api/apps/<id>/install/`, restricted to an organization admin, as
  [Industry apps](../features/industry-apps.md#api) describes. Any authenticated member can list
  the packs via `GET /api/packs/` and the apps via `GET /api/apps/`. The older
  `POST /api/packs/<pack_id>/apply/` installs the app with sample records.

**Applying a pack is strictly additive.** The applier's own description of itself: it creates
what's missing and skips what already exists, and it "never calls `.update()`, never calls
`.save()` on a row it did not just construct, and never deletes." A pipeline whose name already
exists in the org is skipped entirely (along with its stages); a custom field, tag or product that
already exists by its matching key is skipped individually. This makes installing a second pack
on an org that already has one safe: it can only add what's still missing, never overwrite or
remove something you've since changed. The one exception is terminology and the org's `vertical`
label, which are merged in (only keys not already set are added) rather than duplicated. An app
update runs the same applier over only the parts the app has never offered the org.

Sample records created by a pack (accounts, contacts, deals/opportunities, tickets/cases, leads,
projects, tasks and time entries) are marked with an `is_sample` boolean field, set only by the
applier. It is
`read_only` on every serializer that exposes it, so it can never be set by a client request. Every
sample row is also tagged with a "Sample data" tag and assigned to whichever admin applied the
pack, purely for visibility in the UI; the tag carries no special authority and isn't what the
system uses to know a row is sample data.

Sample dates are written relative to one day, which the manifest records as
`sample_dates_written_on`. When the pack is applied, every sample date (task due dates, deal close
dates, and values of custom fields whose `field_type` is `date`) moves by the days between that day
and today in the org's timezone. A task written as due the next day is due the day after the pack
is applied, and a deal that closed three weeks earlier still closed three weeks earlier. A manifest
whose sample rows hold dates without `sample_dates_written_on` fails validation.

Sample rows are optional when installing an app, and are on by default only for an org with no
real leads, contacts or accounts. If an org already has any sample rows (from this pack or a
previous one), they are not offered again: a pack's sample data is only ever created once per org.

## Clearing sample data

**Only `clear_sample_data()` deletes sample records**, via `DELETE /api/packs/sample-data/`
(also admin-only, also reachable from Settings, then Apps). That function is the sole counterpart
to pack-applied sample data: nothing else in the system deletes rows just because `is_sample` is
set, and removing an app does not delete them either.

Clearing is scoped strictly to rows where `is_sample=True` for the calling org, deleted in an
order that removes child records (time entries, leads, tasks) before the parents they can reference
(projects, tickets, deals, contacts, accounts). It never removes anything else. A real record you
created yourself is untouched even if it happens to reference a sample record, and a sample row is
**retained rather than deleted** if a non-sample record still depends on it (for example, a real
invoice raised against a demo account). A sample project is the exception: deleting a project keeps
its time, tasks and invoices with the link cleared, so real ones on a sample project do not keep it. Retained rows are reported back, not silently skipped, so the caller
knows some sample data was intentionally kept rather than assuming the clear was incomplete. Sample
rows that belong to a module the org has switched off (see [Modules](modules.md)) are kept too, and
the response counts them under `switched_off_by_type`.
