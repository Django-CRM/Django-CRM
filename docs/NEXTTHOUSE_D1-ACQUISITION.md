# NEXTTHOUSE CRM — D1 acquisition foundation

This gate remains local and synthetic. It does not expose a new endpoint,
connect the Portal, access Kommo, import customers or authorize deployment.

Validated source identity:

- branch: `codex/nextthouse-hardening`;
- pinned upstream/base HEAD: `989dc0373444a152ddb951588406f4e93e38c6ee`;
- deterministic D1 code/config/contract digest:
  `b83c741a9dba52a2cb2cf3b8c7d5552c17a4b4cdb7951f31713a342023e12de1`;
- local, non-canonical Graphify snapshot: 22,592 nodes / 40,780 edges;
- `graph.json` SHA-256:
  `178a8ce87805d86639bb39e0697564072f15a63db77ac4d29078f6c4f004598c`.

## Domain boundary

- `MarketingCampaign` is the organization-owned campaign identity.
- `LeadAttributionTouch` records bounded first/assist/last acquisition touches.
- The versioned JSON Schema in `docs/api/nextthouse-attribution-v1.schema.json`
  is the future Portal adapter boundary. It accepts opaque references and
  bounded tracking tokens, not raw URLs, headers, cookies or provider payloads.
- Kommo remains the agency-owned Cinema workflow. It is not a source of truth
  for the NEXTTHOUSE CRM and is not part of this contract.

## Security pre-project matrix

| Control | D1 status | Evidence or boundary |
| --- | --- | --- |
| Data classification | Applicable | Lead identity is PII; attribution is internal metadata; evidence refs are opaque |
| Roles and permissions | Pending integration | No new HTTP route exists in D1 |
| Tenant isolation | Applicable | Direct `org` ownership, validation and PostgreSQL RLS policies |
| Untrusted input | Applicable | Strict JSON Schema, bounded fields, unknown fields rejected |
| LGPD lawful basis | Applicable | Lawful basis and privacy notice version required; consent requires evidence ref |
| Logs and secrets | Applicable | No payload, cookie, token, credential or raw evidence field |
| Upload/parser budgets | Not applicable | D1 accepts no files or archives |
| Cache/queue limits | Not applicable | D1 adds no cache, queue or worker |
| External integrations | Not applicable | No provider or network adapter is wired |
| Deletion/export/retention | Pending | Must be designed before a write API or real data |

## D1 acceptance

1. Backend image runs as UID/GID `10001:10001` and can write only its declared
   application directories.
2. Migration creates campaign and attribution tables without destructive
   changes.
3. Both tables are registered and stamped with PostgreSQL RLS.
4. Cross-organization attribution, future touches, unsafe tracking values and
   missing consent evidence fail closed.
5. The strict contract validates synthetic fixtures and rejects extra/private
   payload fields.
6. SQLite, PostgreSQL and non-superuser RLS tests pass before handoff.

## Validation evidence

- backend image identity: `USER 10001:10001`;
- non-root `collectstatic` and migration drift check: passed;
- focused acquisition/contract tests: 19 passed, 1 PostgreSQL-only skipped;
- complete SQLite suite: 4,176 passed, 21 skipped, 8 deselected;
- complete PostgreSQL suite: 4,189 passed, 8 skipped, 8 deselected;
- PostgreSQL-only suite as `NOSUPERUSER NOBYPASSRLS`: 8 passed;
- additional cross-organization campaign RLS test: passed under that restricted
  role;
- OpenAPI document remains at 231 paths with the upstream baseline summary of
  71 warnings (61 unique) and 615 errors (130 unique). No D1 route was added;
  this debt remains a Portal-integration blocker.

## Still blocked

- write/read APIs and Portal UI;
- real SSO/RBAC and service identity;
- lead deduplication policy across channels;
- LGPD export/delete/retention automation;
- SEO, social and ad-platform adapters;
- data mart, scoring and dashboards;
- real provider credentials or data;
- commit, merge, image promotion, deployment and production proof.
