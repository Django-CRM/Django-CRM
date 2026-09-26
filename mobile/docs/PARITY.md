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
| B1 | CSV import for contacts, tickets and leads | Mobile | `import/preview/` and `import/commit/` exist | 2026-09-26 | Web mounted `ContactImportDrawer` and `TicketImportDrawer` on 2026-09-26, and `LeadImportDrawer` (`/api/leads/import/`) the same day. Tracked as G1 in `enterprise-crm/docs/gap-analysis/TRACKER.md`. |
| B3 | Parent and child tickets: parent banner, link parent, detach, tree | Web | `/api/cases/<id>/tree/`, `link/`, `close-with-children/` and `parent_summary` exist (authz fixed as D50 and D51) | 2026-09-26 | Mobile has all of it in `ticket_detail_screen.dart`. Web has `TicketTreePanel.svelte` and `LinkParentDialog.svelte` exported from the components index but imported nowhere; only the close-with-children cascade (`tickets/[id]/close.js`) is wired. |

## Web-only by design

These are not gaps. Do not build them on mobile without a decision.

| Surface | Why |
|---|---|
| Public customer portal (`routes/(no-layout)/portal/`) | Anonymous customers use it in a browser; it is not part of the staff app. |
| HTML and CSS editing in the invoice template editor | Editing markup on a phone is not a real use; mobile edits the template's other fields. |

## Closed

| ID | Gap | Closed | How |
|---|---|---|---|
| B2 | Editing a converted lead | 2026-09-26 | `_buildPayload` in `lead_form_screen.dart` now sends `status` on an edit only when it changed, as the web does, so editing any other field of a converted lead is no longer refused by `LeadCreateSerializer.validate_status`. The detail screen's sheets (assignees, tags, follow-up) never sent `status`. Tests: `test/screens/leads/lead_form_status_test.dart`. |
