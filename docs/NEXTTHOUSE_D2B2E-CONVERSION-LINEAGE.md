# NEXTTHOUSE CRM — D2B2E conversion lineage

Status: validated locally; not merged or deployed.

## Outcome

`LeadConversion` is the immutable, organization-scoped provenance record for a
lead converted by `convert_lead_to_account`. It links exactly one lead to the
account, optional contact and optional opportunity produced or reused by that
operation, while recording whether each downstream record was newly created.

The conversion service now runs inside one database transaction and locks the
source lead before downstream writes. A failure to persist provenance rolls
back the account, contact, opportunity, moved generic relations and lead status.

## Privacy and export

The ledger contains relational IDs and creation flags only. It does not copy
names, e-mail addresses, notes, request bodies or provider payloads. Verified
encrypted exports can now include the proven account, contact and opportunity
through this ledger. Free-text account/opportunity descriptions remain excluded.

## Existing data

No backfill is performed. A historical lead whose status is already
`converted` but has no `LeadConversion` remains unresolved. Relationships must
not be inferred from e-mail, company name or timestamps. A future reconciliation
gate needs explicit evidence and a dry-run report before inserting historical
lineage.

## Security controls

- organization equality is checked before conversion and again by model validation;
- one-to-one lead linkage and an atomic transaction prevent duplicate provenance;
- application-level mutation/deletion is rejected;
- PostgreSQL RLS and an append-only UPDATE/DELETE trigger are added by migration;
- no endpoint exposes the ledger in this gate;
- retention approval and deletion remain fail-closed.

## Non-applicable controls

- no upload/parser, cache, external network, secret, device or multi-tenant API
  surface was introduced;
- authentication and route authorization are unchanged;
- no production database, real PII, provider, Portal or Kommo integration was used.

## Remaining gates

PostgreSQL migration/RLS validation, historical reconciliation design, Legal and
Privacy approval, API/reporting UX, commit, merge, build and deployment are all
separate approvals.
