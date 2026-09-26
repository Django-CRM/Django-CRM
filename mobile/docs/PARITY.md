# Web and mobile parity

The tracker the `CLAUDE.md` files point at when a feature ships on one client and not the other. Add a Backlog row when that happens, and say so in the reply.

## History

An earlier copy of this file was never committed, so it lived only in one working tree and was lost. It had started from 78 web pages against 26 mobile screens and, by 2026-08-08, recorded every page as built on both clients (create and edit share one form screen on mobile, so page counts differ on purpose). This file restarts the tracker from that point. It holds only gaps opened since, plus the surfaces that are web-only by design.

On 2026-09-26 the counts were 90 `+page.svelte` files under `frontend/src/routes` and 72 `*_screen.dart` files under `mobile/lib`. Page counts are not a parity measure: read the Backlog, not the totals.

## How to use it

- One row per gap. Say which client lacks what, and whether the backend already supports it.
- A gap closes when both clients have it with tests. Move the row to **Closed** with the date instead of deleting it.
- Recheck a row against the code before quoting it. Rows go stale when a fix lands without an edit here.
- Parallel sessions edit this file. Re-read it right before editing and change exact rows, not whole sections.

## Backlog

| ID | Gap | Missing on | Backend | Opened | Notes |
|---|---|---|---|---|---|
| B3 | Parent and child tickets: parent banner, link parent, detach, tree | Web | `/api/cases/<id>/tree/`, `link/`, `close-with-children/` and `parent_summary` exist (authz fixed as D50 and D51) | 2026-09-26 | Mobile has all of it in `ticket_detail_screen.dart`. Web has `TicketTreePanel.svelte` and `LinkParentDialog.svelte` exported from the components index but imported nowhere; only the close-with-children cascade (`tickets/[id]/close.js`) is wired. |
| B4 | CSV import: errors download and valid-row sample | Mobile | Preview returns `errors[]` and `valid[]` | 2026-09-26 | Mobile's import sheet (`widgets/forms/csv_import_sheet.dart`) previews, commits and shows row errors inline, but cannot save the errors as a file or show the sample the web drawers show. |
| B5 | Invoice and estimate forms: document discount and tax | Mobile | Serializers accept both | 2026-09-26 | The web forms (`LineItemsEditor.svelte` plus the adjustments card) set them; `new_invoice_screen.dart` and `new_estimate_screen.dart` do not. Line discounts do reach both. |
| B6 | Invoice detail: subtotal, discount, tax and shipping rows | Mobile | Detail payload carries them | 2026-09-26 | Mobile shows Total, Paid and Due only. |

## Web-only by design

These are not gaps. Do not build them on mobile without a decision.

| Surface | Why |
|---|---|
| Public customer portal (`routes/(no-layout)/portal/`) | Anonymous customers use it in a browser; it is not part of the staff app. |
| Public help center pages (`routes/(no-layout)/help-center/`) | Anonymous, search-indexable pages for an org's customers, not app users. Both clients have the admin settings for it. |
| HTML and CSS editing in the invoice template editor | Editing markup on a phone is not a real use; mobile edits the template's other fields. |

## Closed

| ID | Gap | Closed | How |
|---|---|---|---|
| B1 | CSV import for contacts, tickets and leads | 2026-09-26 | One reusable sheet (`mobile/lib/widgets/forms/csv_import_sheet.dart`, provider `csv_import_provider.dart`) on the contacts, tickets and leads lists: pick, preview, commit, created count, template help, the web's 403 wording. The errors download stayed web-only (B4). Tracked as G1 in `enterprise-crm/docs/gap-analysis/TRACKER.md`. |
| B2 | Editing a converted lead | 2026-09-26 | `_buildPayload` in `lead_form_screen.dart` now sends `status` on an edit only when it changed, as the web does, so editing any other field of a converted lead is no longer refused by `LeadCreateSerializer.validate_status`. The detail screen's sheets (assignees, tags, follow-up) never sent `status`. Tests: `test/screens/leads/lead_form_status_test.dart`. |
