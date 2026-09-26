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
| B7 | Deal board drag gated on the card's `can_move` | Mobile | Yes: `/api/opportunities/kanban/` cards carry `can_move` (1.12.0) | 2026-09-26 | The web deal board reads `can_move`; mobile builds its board from the deals list and drags any card (`kanban_column.dart`). Harmless today because the deal read rule equals the move rule, so every card a member can see is one they may move. Close it by reading `can_move` if the two rules ever diverge. |

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
| B3 | Parent and child tickets on the web | 2026-09-26 | Web `/tickets/[id]` has a Linked tickets panel: parent row (a redacted parent is shown, never linked), detach with an in-page confirm, the tree with the current ticket in bold, and a parent picker, gated on `comment_permission`. The unused `TicketTreePanel.svelte` and `LinkParentDialog.svelte` were deleted. Mobile's link action is gated the same way. Tests: `tickets/[id]/tree.test.js`, `ticket_detail_redirect_and_gate_test.dart`. |
| B4 | CSV import errors file and valid-row sample on mobile | 2026-09-26 | `csv_import_sheet.dart` saves `<plural>-import-errors.csv` through `file_picker` (quoted and formula-guarded like the web's shared `lib/utils/csv.js`) and shows the first 20 valid rows. |
| B5 | Document discount and tax on mobile invoice and estimate forms | 2026-09-26 | `document_adjustments.dart`: invoices set discount, tax and shipping; estimates and recurring schedules set discount and tax, in the web payload shape, with the server's bounds and messages mirrored. |
| B6 | Mobile invoice detail breakdown | 2026-09-26 | Subtotal, Discount (when set), Tax and Shipping (when set) rows, as on the web. Neither client has an estimate detail screen. |
| B8 | Web ticket detail: request approval, watch, saved-reply insert, link and unlink articles | 2026-09-26 | Found in the 1.12.0 pass (never had a row). A ticket under a pre-close approval rule could not be closed from the web at all. `/tickets/[id]` now has an approval panel gated on `approval_rule`, `can_act`, `can_cancel` and `comment_permission`, a Watch button on `is_current_user_watching`, a saved-reply picker that fills the composer and sends nothing, and an Articles card. Mobile's watch state and article suggestions were fixed in the same pass (they read keys the API never sent). |
| B9 | Web Delete for leads, contacts, accounts, deals and solutions | 2026-09-26 | Found in the 1.12.0 pass (never had a row). Both clients now gate Delete on the server's `can_delete`; mobile's client-side admin-or-creator check (`core/permissions.dart`) is deleted. |
