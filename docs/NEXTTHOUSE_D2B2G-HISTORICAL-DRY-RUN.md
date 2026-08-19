# NEXTTHOUSE CRM — D2B2G historical reconciliation dry-run

Status: implemented and executed with synthetic fixtures only; not merged or
deployed.

## Contract

`reconcile_converted_leads` is deliberately read-only. It has no `--apply`
argument and no persistence function. It scans a bounded number of converted
leads and emits JSON containing HMAC-derived case references, reason codes,
relation cardinalities and aggregate counts.

## Classifications

- `proven`: an immutable `LeadConversion` exists and every linked entity belongs
  to the same organization;
- `ambiguous`: explicit contact/account/opportunity relations exist, but no
  conversion ledger proves that the historical conversion produced them;
- `no_evidence`: no persisted relational evidence exists;
- `cross_org_conflict`: a persisted explicit relation crosses organizations.

Names, e-mails, raw UUIDs, notes and provider payloads are never emitted.
Matching names, e-mails or timestamps are not queried and cannot promote a case.

## Safety controls

- external HMAC key of at least 256 bits;
- key file must be an owned regular non-symlink file with private permissions;
- hard cap of 10,000 rows per run with an explicit `truncated` indicator;
- output always carries `mode=dry_run` and `write_authorized=false`;
- test captures SQL and rejects INSERT, UPDATE or DELETE;
- no real database, data subject, provider or network access was used.

## Execution evidence

Synthetic fixtures covered one case in each classification. The management
command emitted a valid sanitized report and rejected a public key file and an
unknown apply option.

## Remaining gates

Running this command against a served read replica or sanitized production
snapshot, assigning human reviewers, evidence review UX, an approved migration
proposal, commit, merge and deployment all remain separate approvals.
