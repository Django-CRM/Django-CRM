# NEXTTHOUSE CRM — D2B2F PostgreSQL validation

Status: validated locally against a disposable PostgreSQL 16 container; not
merged or deployed.

## Test topology

- PostgreSQL 16 container bound only to local loopback on an isolated port.
- Synthetic database and credentials only.
- Functional suite executed as the PostgreSQL superuser because those fixtures
  intentionally do not all establish an RLS context.
- Every `postgres_only` test executed separately as a `NOSUPERUSER`,
  `NOBYPASSRLS`, `CREATEDB` role so RLS assertions were not vacuous.
- The disposable container was removed after validation.

## Results

- Functional PostgreSQL matrix: 4,225 passed, 6 skipped, 11 deselected.
- Policy-bound RLS matrix: 11 passed, 4,231 deselected.
- Focused conversion/RLS matrix: migration, transaction rollback, append-only
  trigger and concurrent conversion all passed.
- Two concurrent conversions of one lead produced exactly one lineage record
  and one opportunity; the second operation failed closed.

## Proven

- migrations create the conversion ledger and PostgreSQL policies;
- `lead_conversion` is registered, RLS-enabled and policy-bound;
- direct UPDATE is rejected by the database trigger;
- source-row locking and one-to-one provenance prevent duplicate conversion;
- a provenance-write failure rolls back all downstream conversion effects.

## Not proven / separate gates

- production data volume, latency, pool behavior and lock-wait thresholds;
- historical lineage reconstruction or any backfill;
- THE SERVER migration, backup/restore, Portal integration or operational UI;
- commit, merge, build and deployment.
