# NEXTTHOUSE CRM — D0 hardening baseline

Upstream pinned: `989dc0373444a152ddb951588406f4e93e38c6ee`

This local branch is an evaluation fork. It is not connected to the NEXTTHOUSE
Portal, Kommo, production data, providers or a deployment pipeline.

Validated source identity:

- branch: `codex/nextthouse-hardening`;
- upstream/base HEAD: `989dc0373444a152ddb951588406f4e93e38c6ee`;
- deterministic digest of the four D0 code/configuration files (excluding this
  self-referential evidence document):
  `80dba8d1511c98f60aca4f08cd1e0f745e88031f890da326ecf1e755e5abcdbc`;
- local, non-canonical Graphify snapshot: 22,470 nodes / 40,592 edges,
  `graph.json` SHA-256
  `de80fde91f38b56049c3f711f2120240f788235fca2354a7073f586699acd329`.

## Controls closed in D0

- `manage.py` honours an explicitly supplied `DJANGO_SETTINGS_MODULE`;
- the Docker image contains the curated API reference required by the endpoint
  drift test;
- a regression test exercises the settings boundary in a subprocess;
- backend and frontend images must build from the pinned tree;
- PostgreSQL RLS tests must run under `NOSUPERUSER NOBYPASSRLS`.

## Validation evidence

- hermetic backend suite: `4158 passed, 21 skipped, 7 deselected`;
- PostgreSQL suite: `4168 passed, 11 skipped, 7 deselected`;
- PostgreSQL RLS-only suite as `NOSUPERUSER NOBYPASSRLS`: `7 passed`;
- backend and frontend production image builds: successful;
- focused Ruff validation and `git diff --check`: required before handoff.

## OpenAPI debt — release blocker

The pinned upstream generates an OpenAPI document, but schema generation emits
errors for APIViews without serializers and warnings for unresolved fields,
enum naming and colliding operation IDs. D0 records this debt; it does not
declare the contract complete.

The D0 measurement found 231 paths and 615 emitted error occurrences across
130 unique messages. These numbers are a baseline, not an acceptance target.

Before Portal integration:

1. every public endpoint used by the Portal must have an explicit request and
   response schema;
2. operation IDs must be stable and unique without numeric collision repair;
3. schema generation must run in CI with warnings treated according to an
   explicit, shrinking allowlist;
4. generated clients and contract tests must use the same checked artifact;
5. internal/admin-only endpoints must not be accidentally published.

## Controls still blocked

- real credentials, PII or customer data;
- Kommo or agency access;
- Portal wiring;
- service accounts and production SSO;
- non-root runtime user and production filesystem ownership (the evaluated
  upstream image currently has no `USER` directive and therefore defaults to
  root);
- import/migration;
- RPO/RTO operational proof;
- merge, release image, deploy or external effects.
