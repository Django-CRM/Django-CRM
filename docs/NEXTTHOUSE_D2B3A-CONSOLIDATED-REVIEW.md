# NEXTTHOUSE CRM — D2B3A consolidated review

Date: 2026-08-19

Change control: `CHG-20260819162658-62D0A4`

Branch: `codex/nextthouse-hardening`

Base HEAD: `989dc0373444a152ddb951588406f4e93e38c6ee`

## Scope

This gate reviewed the accumulated local CRM change-set from acquisition and
attribution through LGPD intake/export/inventory, immutable conversion lineage,
PostgreSQL RLS and the read-only historical reconciliation report. It did not
commit, merge, deploy, connect the Portal, read real CRM records or execute a
privacy deletion.

## Consolidation changes

- Normalized generated-migration and contract-test formatting.
- Added `graphify-out/` to `.gitignore`. The directory describes a dirty local
  tree and must never be confused with a canonical graph promoted after merge.
- No product or persistence behavior was changed during the final review.

## Validation

- `ruff check`: pass.
- `ruff format --check`: pass.
- `makemigrations --check --dry-run`: no changes detected.
- Focused acquisition, attribution, LGPD, lineage and RLS matrix: 53 passed,
  4 SQLite-only skips. The subset's non-zero exit was only the repository-wide
  coverage threshold; the complete run below is authoritative.
- Complete SQLite matrix: 4,216 passed, 30 skipped, 93.73% coverage.
- `git diff --check`: pass.
- Local Docker build: pass.
- Image identity: application runs as `10001:10001`, workdir `/app`.
- Container smoke test: API JSON schema readable and WeasyPrint 69.0 imports.

The earlier PostgreSQL 16 gate remains the database-engine evidence for this
same feature family: functional and restricted-role/RLS matrices passed. This
review did not claim that a local image or synthetic database proves a served
deployment.

## Review outcome

No release-blocking defect remains in the reviewed local scope. The source is a
candidate for a separate commit/PR gate, subject to an explicit authorization.
The historical reconciler remains dry-run only, and no real CRM database was
found on the previously inspected server.

## Remaining stop-gates

- Commit and draft PR.
- Independent PR review and CI.
- Staging deployment with PostgreSQL, restore rehearsal and runtime evidence.
- Portal/CRM integration, migration from any incumbent CRM and real-data
  reconciliation.
- Production deployment, DNS/routing and publication are separate approvals.
