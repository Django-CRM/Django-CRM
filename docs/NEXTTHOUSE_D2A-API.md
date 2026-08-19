# NEXTTHOUSE CRM — D2A authenticated acquisition API

D2A exposes the D1 campaign and attribution domain only inside the local
evaluation fork. It does not connect the Portal, Kommo, a provider or real
customer data, and it does not authorize a release.

Validated source identity:

- branch: `codex/nextthouse-hardening`;
- pinned upstream/base HEAD: `989dc0373444a152ddb951588406f4e93e38c6ee`;
- deterministic D2A source/contract digest:
  `a71e0aa22bcff3bfcc301f4c0100f3de03bddedff9ff4e26ee2c9219528c18bc`;
- local, non-canonical Graphify snapshot: 22,650 nodes / 40,926 edges;
- `graph.json` SHA-256:
  `355781a487d11e921a3ed9471999aac37a2088de84ee4f446c803567ec7dcc10`.

## Endpoints

- `GET|POST /api/leads/campaigns/`
- `GET|POST /api/leads/attribution/`

Both require authenticated, active organization context and organization-admin
role. A future Portal service may use an admin-owned PAT restricted to
`leads:read` and/or `leads:write`; D2A does not create that credential.

## Write contract

- Unknown and read-only fields fail closed.
- Lead and campaign references are resolved inside the authenticated org.
- Cross-org and missing references have the same response.
- Attribution requires a 32–200 character opaque `Idempotency-Key`.
- Only SHA-256 of that key is stored; replay with the same command returns the
  existing row, while reuse with different content returns `409`.
- Request content is canonicalized and stored only as a digest. Provider
  payloads, headers, cookies and raw URLs are rejected.
- Consent evidence remains an opaque reference. Read responses expose only a
  boolean indicating that evidence exists.

## Deduplication boundary

`find_duplicate_lead` is advisory and organization-scoped. Email is matched
case-insensitively. Phone is normalized and returned only when exactly one
candidate exists; shared numbers are explicitly ambiguous. The function never
merges, deletes or mutates a lead.

## Security controls

| Control | D2A status |
| --- | --- |
| Authentication | Existing JWT/PAT authentication required |
| Authorization | Active org context plus admin role |
| Tenant isolation | Explicit `org=` lookups plus PostgreSQL RLS |
| Idempotency | Hashed key and request digest, unique per org |
| Input validation | Strict fields, bounded allowlists and formats |
| Sensitive evidence | Opaque write-only reference; presence-only response |
| Collection bounds | Lists capped at 200 rows pending cursor pagination |
| Logging | No new request logging or payload logging |
| Uploads/parsers | Not applicable; no files accepted |
| External effects | Not applicable; no provider or network adapter |

## Still blocked

- Portal service identity, PAT issuance/rotation and end-to-end authorization;
- cursor pagination and incremental synchronization;
- retention, export, correction and deletion workflows under LGPD;
- merge UI and human review for duplicate candidates;
- lead ingestion API using the advisory deduplication result;
- analytics mart, scoring, SEO/social/ad-platform adapters;
- commit, merge, image promotion, deployment and production proof.

## Validation evidence

- focused API and deduplication suite: 15 passed;
- focused D1+D2 acquisition/security suite: 32 passed, 2 PostgreSQL-only
  skipped under SQLite;
- complete SQLite suite: 4,191 passed, 21 skipped, 9 deselected;
- complete PostgreSQL suite: 4,204 passed, 8 skipped, 9 deselected;
- PostgreSQL-only suite under `NOSUPERUSER NOBYPASSRLS`: 9 passed;
- OpenAPI contains four unique operations:
  `leads_campaigns_list`, `leads_campaigns_create`,
  `leads_attribution_list`, `leads_attribution_create`;
- migration drift, focused Ruff and `git diff --check`: required green before
  handoff.

An initial attempt to run the full PostgreSQL suite and PostgreSQL-only suite
concurrently collided on Django's shared `test_crm_db`. This was a test
orchestration collision, not an application failure; the PostgreSQL-only suite
was rerun sequentially and passed 9/9.
