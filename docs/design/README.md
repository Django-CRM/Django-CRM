# v2 design reference

Two self-contained HTML files. No build, no dependencies, no network calls,
open either one directly in a browser.

## clearing-reference.html, **the reference**

The "Clearing" direction developed across 16 screens. This is the agreed
reference for **layout, information architecture, component behaviour and
copy** while the v2 revamp is in progress.

Screens: Today · Pipeline (list) · Pipeline (board) · Deal record · Leads list
· Lead record + convert · Account workspace · Ticket queue · Ticket record ·
Invoice list · Invoice record · New deal · ⌘K search · Empty & error states ·
Design system · Mobile.

Controls in the left panel: screen picker, density, accent, light/dark, and a
**"Mark what this fixes"** overlay that pins each screen to the specific defect
it addresses from the 2026-07-27 audit.

> ### Read this before copying anything out of it
>
> **The colours in this file are superseded. Do not port them to v2.**
>
> These mocks predate the palette decision and still use an exploratory
> "bottle glass" scheme (green / cobalt / amber / oxide). The v2 app uses the
> agreed palette instead, Ember `#EA580C`, Ink `#1C1917`, Slate `#78716C`,
> Paper `#FAFAF9`, Clay `#B45309`, Rust `#9A3412`, Moss `#3F6212`, where
> **Ember marks only "something you can act on, or something that needs you"**:
> never a heading, never an icon tint, never active chrome.
>
> Take the structure from this file. Take the colour from
> `frontend/src/lib/v2/styles/v2.css`, which is the source of truth.

**Type and metrics do come from this file.** On 2026-07-28 v2 was realigned to
the reference's numbers: 14.4px base, 21.9px/640 titles, 11px sans sentence-case
table headers, 10px sans section labels, 44px rows, 22px page padding. That
supersedes the earlier "Page title 19px / Label 9.5px mono" note. Mono is now
reserved for numerals only. Colour was not touched.

### Coverage

All sixteen reference screens exist in v2 as of 2026-07-28, including the four
that were outstanding: New deal, ⌘K search, empty/error states, and mobile.

Two differences are deliberate:

- **Mobile** is real responsive chrome (a top bar, a four-item tab bar and a
  FAB below 768px), not the phone-frame mock the reference draws. See
  **Lists on a phone** below for what happens to the tables inside it.
- **Empty and error states** are wired into the live routes rather than only
  catalogued. `/v2/+error.svelte` catches every failed load; each list renders
  `EmptyState` when it has no rows. `/v2/design/states` is the catalogue.

v2 also has screens the reference does not. Beyond the sixteen: an Accounts
list, Contacts, Tasks and a Knowledge base (the reference's sidebar advertised
the last two without drawing them), and then a second batch covering the parts
of v1 the reference never reached,

- **Serve**: ticket approvals and service analytics, as tabs under Tickets.
- **Sell**: goals, with attainment read against elapsed period rather than on
  its own.
- **Bill**: estimates, recurring schedules and the product catalogue, as tabs
  under Invoices; timesheet.
- **Run**: team and access, a settings hub, API tokens, business hours, and
  your own profile.

A third batch finished the administrative surface,

- **Settings**: ticket routing, escalation, reopen policy, approval rules,
  inbound email, macros, tags, custom fields, and the organization profile.
- **Serve**: a kanban board under Tasks.
- **Bill**: invoice reports and invoice templates, as further tabs under
  Invoices.

A fourth batch added the two things left: the pages a **customer** sees, and
the two authoring forms.

- **Customer-facing** (`src/routes/v2/(public)/`): the invoice portal, the
  estimate portal, and the CSAT survey. These use `+layout@.svelte` to escape
  the app shell entirely, no sidebar, no nav counts, no command palette.
  Someone who receives an invoice is not a user of your CRM and must not be
  shown its furniture.
- **Authoring**: a new-invoice builder and a new-article form.

The v2 copies live under `/v2/` and are therefore behind the app's auth guard,
unlike the real portal. That is deliberate: `hooks.server.js` denies by
default, and a design preview is the weakest possible reason to punch a hole
in it. Do not add `/v2` to `PUBLIC_ROUTES`.

Second-level navigation is a tab strip (`SectionTabs`), not a nested sidebar.
The sidebar stays one level deep. Tab sets live in `src/lib/v2/tabs.js`.
**Settings deliberately has no tab set**: twelve pages are not peers you flip
between, so the hub at `/v2/settings` is the index and each page carries a
`SettingsCrumb` back to it.

v2 covers every one of v1's app routes except Salesforce, plus three public
ones, plus two screens v1 never had (Documents, Notifications). What v2 does
not carry, and why:

- **Salesforce** (`settings/salesforce`, `settings/salesforce/import`), now in
  neither v1 nor v2. The v1 pages were deleted along with the rest of v1, which
  left two nav entries pointing at a route that no longer resolves: the account
  dropdown in `AppSidebar.svelte` and a "Still in v1" card on the settings hub.
  Both are removed, so nothing in the UI reaches the feature today.

  **This entry used to say the API "was never written", and that was wrong.**
  It is true of the community backend and false of the product.
  `salesforce_imports` is an **enterprise** app, mounted at `api/salesforce/`
  in `crm_enterprise/urls.py`, and `simple-salesforce` is a dependency of
  `enterprise/pyproject.toml` with a real caller in `sf_client.py`. The old
  `salesforce` client in `lib/api.js` and the `routes/api/salesforce-import-poll`
  proxy had no caller once the v1 pages went, and were removed in 1.12.0; a v2
  port starts from the enterprise routes (`status/`, `connect/`, `disconnect/`,
  `import/`, `import/<id>/`, `import/<id>/cancel/`, `import/history/`,
  `credentials/`), not from them. It is real work against a real API, not a redesign of nothing.
- **`tasks/calendar`**: a redirect stub in v1, no v2 equivalent by design.
- **The auth and org-selection flow** (`login`, `login/verify`, `org`,
  `org/new`, `bounce`). Out of scope. It is a security surface, not a design
  one, and mocking a login screen is how you end up with a login screen that
  accepts mock credentials.

### Lists on a phone

The chrome was responsive before the content was. Measured at 390px, every list
table scrolled sideways: contacts was 1083px wide and hid 693px of itself behind
a swipe, tickets hid 622px, accounts 467px. Nothing overflowed the page, so it
read as working, the `.v2-table-wrap` scroller absorbed it, but the column you
scan for a name went off-screen along with everything else, and lists are the
way into every other screen.

Below 768px each row is now a card: the identifying cell on the first line, the
status pinned to the top right, the rest wrapping underneath as a meta line. The
rules are in `v2.css`, in the `max-width: 768px` block, and the defaults carry
every table without it being touched. Pages opt into refinements with `data-m`
on a cell (`hide`, `title`, `tag`, `lead`, `bar`), plus `data-l` for a label.

Three things are worth knowing before changing it:

- **Columns are dropped, not shrunk.** A phone is not a narrow desktop. Six of
  the lists were curated by hand, leads, pipeline, accounts, contacts, tickets,
  tasks: on the question of what you do with a phone in your hand: check
  today, look something up before a call, log a note, move a stage, answer a
  ticket, approve something. The rest of the list pages get the defaults and are
  better than they were, but were not curated.
- **`data-m="hide"` can go on any element, not just a cell.** Contacts needs
  this: the relationship word is noise on a phone, but the "Do not call" pill
  sitting beside it in the same cell is least skippable on the one device that
  can place the call. Hiding the column would have hidden the warning.
- **The layout is blocks and a gutter, not flexbox.** Flex was tried first and
  abandoned. It picks line breaks from each item's content width *before* it
  grows anything, so a long title either claimed a line of its own or. With a
  zero basis. Let the meta cells crowd onto line one and squeeze it into a
  column three words wide. Which one you got depended on the length of the
  data, which is the worst property a layout can have.

Two ordering traps are already paid for and commented in place: `hide` sits last
in the block because `.v2-table td:first-child` outranks a bare attribute
selector, and the `lead` cell has to reset the right padding the title rule
reserves for the tag, or it inherits 96px and shoves the title most of a screen
to the right.

Touch targets were raised in the same pass, buttons and view chips to a 40px
minimum, since icon-only buttons cannot get there on padding alone, and the
filter chip's remove button from 12×14px, which was smaller than the fingertip
aiming at it and sat inside a chip that does something else when missed.

### The two screens v1 never had

Both are models with a complete API and no interface anywhere, not in the
SvelteKit app, not in Flutter. Designing against them turned up four backend
defects, each measured against a running server rather than read off the code.
**All four are now fixed**, with tests; the pages are drawn against the
corrected rules.

- **Documents** (`/v2/documents`). `common.Document` has supported upload,
  status and sharing since the first migration, and because nothing ever
  rendered it, three defects survived its whole life: `GET /api/documents/`
  called `request.profile.documents()`, which does not exist on `Profile`, so
  **every non-admin got a 500**; the object checks compared `request.profile`
  to `created_by`, a **User** FK, so they were always False and **the uploader
  could not open or delete their own document**; and `Document.teams` was
  written by POST/PUT and read by no query, so **sharing with a team granted
  nothing**. Fixed in `common/views/document_views.py`, one `_visible_to()`
  predicate shared by the list query and the object checks, so they cannot
  drift apart again, and covered in both directions by
  `common/tests/test_documents_access.py`. Deletion stays narrower than
  reading: admin or creator only, because a share hands someone a copy to work
  with, not the right to remove it from everyone else.

- **Notifications** (`/v2/notifications`). v1 has a bell and a 360px dropdown,
  which is fine for two items and useless for twenty. The producer wrote
  `link=f"/cases/{case.id}"` and **no client has ever served `/cases`**.
  Tickets are at `/tickets/[id]`, so v1's panel, which assigns `n.link`
  straight to `window.location.href`, sent every notification to a 404. Fixed
  at the producer in `cases.notifications.case_link()`, shared with the merge
  redirect that had the same bug; `api.js` still rewrites the dead prefix
  because rows written before the fix are in the database. Unchanged and still
  worth knowing: **only two verbs are ever dispatched** (`case.mentioned`,
  `case.commented`) while v1's panel carries labels for five more that nothing
  produces, so the page renders an unknown verb as a readable sentence rather
  than a raw dotted identifier.

### What the edit forms are for

`leads/[id]/edit` and `pipeline/[id]/edit` complete v1 parity, but an edit form
is not a create form with values in it. Creating is about asking for as little
as possible; editing is about a change whose consequence is invisible. Each
form names the ones that are:

- **Lead status is not just a label.** Choosing "Converted" makes email
  required (`Lead.clean()` and `LeadCreateSerializer.__init__` both enforce it)
  and creates an Account, a Contact and an Opportunity with no endpoint that
  undoes it. `leads/workflow.py` declared that rule and **was imported by
  nothing**. Its sibling `cases/workflow.py` is used by two modules, the leads
  one by none, so the API accepted both a repeat conversion, which built a
  *second* Opportunity because `LeadDetailView.put` reran the service on every
  "converted" PUT, and a move back out of it, which orphaned everything
  conversion had made. Both are now refused by
  `LeadCreateSerializer.validate_status`, and the form shows the status as
  settled rather than offering a control the server will reject. "Closed" is
  deliberately still reversible: reopening a lead creates nothing.
- **A deal's amount is not always yours to set.** `recalculate_amount()` sets
  `amount = SUM(line_items.total)` and flips `amount_source` to CALCULATED
  whenever line items exist, so a number typed into v1's plain Amount input is
  discarded on the next save. Here the field is disabled with the reason and a
  link to the line items.
- **Changing a stage resets a clock someone else is watching.**
  `Opportunity.save()` sets `stage_changed_at = now()` on any stage change, and
  that is what the board's aging colour is computed from, so moving a stage
  turns a stalled deal green without anything improving. The form shows the
  aging pill that is about to be discarded.

### What the settings pages are for

Each one shows the current state and what it costs, not just the fields:

- **Ticket routing** marks rules that can never run. Rules are evaluated in
  order and stop on the first match, so anything below an unconditional
  stopping rule is dead, and "matched 0 times" on its own just looks like a
  quiet month.
- **Escalation** reports which policies resolve to no recipient. `reassign`
  with a null target, `notify` with neither a person nor a team, and
  `is_active: false` all look identical in a form and all do nothing.
- **Macros** marks placeholders the server will not substitute. Only seven
  tokens are supported; anything else reaches the customer literally.
- **Custom fields** shows how many records predate a required field, because
  `is_required` binds writes from the moment it is set and nothing backfills.
- **Inbound email** distinguishes "off" from "off and still receiving mail".
  An inactive mailbox does not bounce; the sender just gets silence.

Credentials appear on none of them. `Org.api_key`, `InboundMailbox.webhook_secret`
and raw token values are absent from the fixtures and stripped in `api.js`, so
no component can render one by accident.

### What the customer-facing pages are for

The same principle, pointed outward. Say what is true, and what it costs:

- **The invoice portal** answers one question (do I owe anything, how much, by
  when) and never shows `status`. `Sent` / `Viewed` / `Partially_Paid` are
  facts about our workflow, and one is actively misleading: `Viewed` is set by
  the customer's own page load. It also draws no "Pay now" button, because
  there is no payment processor in this product; `Payment` rows are entered
  by hand after money arrives.
- **The estimate portal** makes accept a two-step decision. It is the only
  irreversible thing a customer can do, there is no undo endpoint, and v1
  wires it to one button reachable from an email link.
- **The survey** is a confirmation, not a form. The rating was already clicked
  in the email; the comment box appears only after a rating exists.

Two backend gaps are named on the pages themselves rather than papered over:
the accept endpoint never reads `expiry_date`, and `Estimate` records
`accepted_at` with no record of *who* accepted.

## four-directions-explorer.html. Provenance

The four directions that were compared before Clearing was chosen: Clearing
(evolution), Workbench (dense operator tool), Dispatch (editorial), Room
(record as a live thread). Kept for the rationale, not as a spec. The
**"Compare all four at once"** toggle shows one screen in all four treatments.

## The live implementation

`frontend/src/routes/v2/` and `frontend/src/lib/v2/`, browsable at `/v2`.
Most modules still read fixture data through `frontend/src/lib/v2/api.js`.
**Leads, pipeline, accounts, contacts, tickets, the knowledge base, tasks,
invoices, team-and-access and the API-tokens settings page are wired to the
real API**. See below. `src/lib/v2/migrated.js` is the list the shell banner
reads, and it is the one place that says which is which.

## Wiring a module

Leads went first, the pipeline second, accounts third, contacts fourth, tickets
fifth, the knowledge base sixth, tasks seventh, invoices eighth, team-and-access
ninth, the API-tokens settings page tenth, the organization settings page
eleventh, the goals page twelfth, the documents page thirteenth, the products
catalogue (the first of the invoices sub-pages) fourteenth, the estimates
worklist fifteenth, the recurring-invoice schedules sixteenth, the invoice
reports seventeenth, the invoice templates eighteenth, the from-scratch invoice
builder nineteenth, which finished the invoices cluster: list, detail,
lifecycle, and every sub-page now read and write the real API. The service
analytics dashboard (`/v2/tickets/analytics`) went twentieth and the approvals
queue (`/v2/tickets/approvals`) twenty-first, the first two tickets sub-pages.
All twenty-one are worked examples; by the third it is clear which findings are a
pattern and which are per-module work. The rules, and the parts that were not
obvious:

**Wire the module the live pages already point at.** Pipeline shipped linking
every deal to `/v2/accounts/{uuid}` while accounts was still fixtures keyed by
slugs like `acc-northwind`, so every one of those links answered 404. A module
is not finished when its own pages work; it is finished when the pages it points
at do. Before declaring one done, follow its outbound links,
`grep -rn 'href="/v2/' src/routes/v2/<module>` and check what is on the other
end.

**Where the module it points at is not wired yet, do not link.** Accounts
shipped its tickets and invoices rails pointing at `/v2/tickets/{uuid}` and
`/v2/invoices/{uuid}`, both still fixtures, the same mistake pipeline made,
repeated one module later. Those rails are plain rows now and become links again
in the change that wires those modules. A link that 404s is worse than text: it
teaches people the record is missing rather than the page.

The other half of the same rule is the reason contacts went fourth. Three wired
pages showed people and none of them could link to one, so the account's people
rail and the deal's contact rail were written as dead text. Both are links now.
**A rail you had to leave dead is the module to wire next.** Tickets went fifth
for exactly that reason: the account and contact pages both listed a customer's
tickets as plain text. Invoices went eighth and last for the same reason. The
account page had rendered a real Invoices rail since accounts were wired, actual
rows deliberately left as plain `<div>`s with a "View all" pointing at the
fixture list, because there was no invoice page to open. Every customer rail on
the account page is a link now.

**When no rail is dead, pick on risk.** Team-and-access went ninth on different
grounds. Nothing dead-ends into it (it is a nav destination, not a rail), so
the usual "wire the page the others point at" heuristic said nothing. It was
chosen because it is the access-control surface: who is an admin, who was
deactivated, whose API tokens still authenticate. Wiring it meant driving
`/api/user/<id>/` for real, and that is where the highest-severity finding of
any module so far was hiding. A plain member could `PATCH` their own profile to
`role="ADMIN"` and become an org admin (see the authorization section below).
The lesson generalises: once the dead rails are gone, the module worth wiring
next is the one whose endpoints, if wrong, cost the most, not the one with the
most broken links.

**Wiring one module makes a rail for the next.** The API-tokens settings page
went tenth because team-and-access, wired ninth, links straight to it: the
team page counts each member's live tokens and points the number at
`/v2/settings/api-tokens`. So the tenth pick was a dead rail after all, one the
ninth module created. It is the same access-control family (a token
authenticates as its owner and inherits their whole role and org), so it was the
right next step on risk as well as on links. Wiring it needed a backend that did
not exist: the only token endpoint, `/api/profile/tokens/`, is deliberately
self-scoped ("a user manages ONLY their own"), and this page is org-wide
oversight. The answer was a **separate, admin-only** surface;
`GET /api/org/tokens/` and `DELETE /api/org/tokens/<id>/`, added *beside* the
self-scoped one rather than by widening it, so the existing guard was never
relaxed. `personal_access_token` carries no RLS (it is looked up by hash before
any tenant context exists), so on those new endpoints the explicit
`org=request.profile.org` filter is the only tenant barrier, load-bearing, and
tested both ways. Create was left on the self-scoped endpoint: a token can only
ever be minted for yourself, so there is no "create on behalf of" for an admin
to misuse.

That new list route also walked straight into a URL-ordering trap the codebase
had already solved once: `path("org/<str:pk>/", …)` sits above it and its
`<str:pk>` swallows the literal `tokens`, so `GET /api/org/tokens/` resolved to
`OrgUpdateView` (a 403) until the literal route was moved above the catch-all,
exactly the fix the `org/api-key/` line already carries a comment about. A
literal path added below a `<str:pk>` route is dead on arrival; the tests caught
it because the list 403'd while the two-segment revoke route (which the pk rule
cannot match) passed.

**A page can show a column the model has and no endpoint writes.** The
organization settings page went eleventh, on risk again. It is the settings
flagship, admin-only, and sits next to the org API key, with no dead rail
pointing at it. Wiring it exposed a gap that reads like the
silent-drop family but is quieter than any of them: `csat_enabled` and
`auto_close_children_on_parent_close` are real `Org` columns the page has always
displayed, yet **no serializer made them writable**. Turning off org-wide CSAT
was simply not expressible through the API. Adding them to `OrgSettingsSerializer`
(an allow-list that still excludes `api_key` and `is_active`) is the whole
backend change; there was no auth hole, only an edit path that did not exist.
Worse than the missing fields is a *second* org-update endpoint that does exist:
`OrgUpdateView` at `PUT/PATCH /api/org/<id>/` writes **only `name`** and silently
drops every other field in the body, so a client that "updates the organization"
there loses the company profile it thought it saved. The wiring deliberately uses
`/api/org/settings/` (serializer-based, org taken from `request.profile`, so no
id in the URL to get an IDOR wrong) and leaves the older view flagged, not
widened. The rule: when a settings page renders a field, confirm a write path
*reaches the column*, a value that round-trips through the read serializer is
not proof anything can change it.

**A writable relation is a cross-tenant hole until its queryset is org-scoped.**
The goals page went twelfth, on risk, a sidebar dead rail whose "New goal"
button wrote nowhere, and `SalesGoal` is the one place a quota points at a
*person*. `SalesGoalCreateSerializer` exposed `assigned_to` (a `Profile`) and
`team` with DRF's default queryset, every row in the table, and validated only
the dates and the target. `common_profile` carries no RLS (it is looked up before
any tenant context exists), so nothing downstream caught it: an admin could
`POST`/`PUT` a goal assigned to a Profile in another org, and that person's name
and email then rendered in this org's goal detail and leaderboard. The fix scopes
both fields to `request.profile.org` in the serializer, the trust boundary, and
the frontend's pickers only ever offer in-org targets, but that is UX, not the
guard. The rule: a `PrimaryKeyRelatedField` the client can set is an IDOR unless
its queryset is filtered to the caller's org; DRF's default is every row, and RLS
only saves you on the tables that have it.

**RLS in the database is not RLS in source.** Wiring goals also surfaced that
`sales_goal` was never in `ORG_SCOPED_TABLES` and no *current* migration stamps
its policy. The live DB carries `org_isolation`/`org_insert_check` on the table,
but only because a migration (`0012_rls_opportunity_config`) that has since been
deleted from source once applied them; the ledger still records it, the file is
gone. A fresh `migrate` from this tree ships the table with **no** RLS, tenant
isolation resting entirely on the ORM `.filter(org=...)`. The fix registers the
table and adds an idempotent source migration (`get_enable_policy_sql`, which
drops-then-creates, so it is safe on the databases that already have the policy).
Flagged, not fixed: `stage_aging_config` and `opportunity_line_item` sit in the
same deleted-migration gap. The rule: `pg_policies` proving a table is protected
today says nothing about a rebuild. Check that an org-scoped model is in
`ORG_SCOPED_TABLES` *and* has a policy migration on disk.

**Write must be at least as narrow as delete.** The documents page went
thirteenth, on risk; `Document` is a first-class model with a full API that no
client (v1, Flutter, or v2) had ever rendered, so its access rules had only ever
been exercised by tests. `DocumentDetailView` already gated `delete` narrowly,
with a written rationale: a share "hands someone a copy to work with, not the
right to remove it from everyone else," so delete is the creator or an admin
only. But `put`/`patch` authorised on `_may_read`: creator, anyone in
`shared_to`, any member of a shared team, or an admin. So anyone a document was
merely *shared with* could overwrite its file, rename it, flip its status to
`inactive` (hiding it from the org), and, through `PUT`, which clears the M2M
before re-adding, wipe the entire share list and lock everyone else out.
Overwriting the bytes behind a title is at least as destructive as deleting the
row. The fix adds `_may_write = _may_delete` (creator or admin) and uses it on
both verbs; reading stays broad on purpose. The rule: whenever a "read" grant is
wider than the "delete" grant, the write path has to match delete, not read,
being shown a record and being allowed to rewrite it for everyone are different
privileges.

**Do not enforce in the UI a rule the server does not have.** The first cut of
the documents frontend gated *uploading* on admin, copying the goals pattern. But
`DocumentListView.post` checks only `(IsAuthenticated, HasOrgContext)`. Any org
member may add a document, and the mock's own notes never restricted create. An
"admins only" upload screen would have been the [[v2-team-wired]] mistake in
reverse: a UI inventing a rule the backend does not enforce, so a rep who curled
the API would succeed where the page told them they could not, and a normal
action (attaching a discovery note) would be blocked for no reason the server
agrees with. Uploading was relaxed to match the backend; the narrow writes
(edit, delete) stayed gated because those the server *does* enforce. Wiring the
list also exposed a plain bug the endpoint had carried unhit: the inactive
pagination envelope computed its offset from `results_documents_active[-1]`, a
copy-paste from the active block, so an org with archived documents but no
active ones (archive the only document and reload) hit `[][-1]` → `IndexError` →
500. No client had ever rendered the list, so the crash sat there for the life of
the model. The rule: a screen may hide an action, but the server decides who may
take it, build the UI to the backend's real rules, not to a tidier story you
wish it told.

**Shared config reads wide and writes narrow, and that gate has to be added,
not assumed.** The products catalogue went fourteenth, the first of the invoices
sub-pages, and its `Product` model had a full CRUD API that no client had drawn,
so every write test had used an admin. But the view gated `POST`/`PUT`/`DELETE`
on `(IsAuthenticated, HasOrgContext)` only: any member, `USER` role included,
could rewrite a list price, retire a product, or hard-delete one for the whole
org. The catalogue is org-wide config: its prices flow onto every rep's invoices
and estimates, so it is the same shape as Organization and Team settings, which
are admin-gated. The fix gates all three writes to an admin and leaves *reading*
open to every member, because a rep needs the catalogue to build line items.
This is the mirror of the documents lesson above: there, a write path had
silently widened to match read and had to be *narrowed*; here, no write gate
existed at all and one had to be *added*. Both come from the same question,
"who may change this, versus who may see it?". Asked of a surface that had only
ever been tested by an admin. Note the reasonable-looking wrong turn avoided: it
is tempting to also gate *reading* on admin ("it's a settings page"), which would
break the line-item picker for every rep. Read wide, write narrow.

**The load and the page share a data-shape contract, and renaming a field breaks
it silently.** The fixture loader returned `{ products, totals }`; the real server
load returned the data layer's `{ results, totals }` verbatim, and the page reads
`data.products`. So `data.products` was `undefined`, `{#each}` iterated nothing,
actually threw during SSR, and the page 500'd before a single row rendered, even
though the API behind it answered `200` with twenty products. The backend was
never the problem. The rule: when a `+page.js` becomes a `+page.server.js`, the
new load must return the exact shape the `.svelte` already reads. The field
names are an interface, and the compiler cannot see across it. Diagnose a
migrated-page 500 by checking that contract before suspecting the API.

**A worklist can only sort by facts the list row actually carries.** The
estimates page went fifteenth, and its whole reason to exist is to separate
"accepted but not yet billed" (money agreed, nobody invoiced it) from "already
billed". The decision is `status == 'Accepted' and not converted_to_invoice`.
But `EstimateListSerializer` returned neither `converted_to_invoice` nor
`opportunity`; the fixtures had faked both. Read against the real API, every
accepted estimate would have looked unbilled, so the page would have offered to
raise a *second* invoice for one that already had one, a duplicate, produced by
the one button the page is built around. This is not an authorization hole (the
estimate endpoints were already the best-authorized of the invoices cluster.
Object-level `get_estimate_or_error` plus non-admin list scoping); it is a
correctness gap the wiring exposed. The fix put both fields on the list row,
trimmed to id-plus-one-label each, and a test asserts the row flips from
"unbilled" to carrying its invoice reference the moment it is converted. The
general form: before trusting a list to drive an action, check that every fact
the action's guard reads is present in the list payload, not just in the detail.

**When a page's create surface is a builder, defer it rather than half-build
it.** The estimates page has a "New estimate" button, but creating an estimate
means a header, three foreign-key pickers and a line-item table, the same class
of surface as the invoice builder that is still a fixture. Wiring the list and
the one-click `convert` is the sub-page's real work; a full create form is its
own task. So "New estimate" points at the pipeline, which is where an estimate is
raised from a deal, exactly what the page's empty state has always said, rather
than at a form this change would only get halfway through. A dead-ended button
repointed at the honest origin beats a live button wired to a half-built form.

**When an owned record has no object-level auth, match its siblings. Don't
invent a model.** Recurring invoices went sixteenth, and every one of their
endpoints filtered on `org` only: no object check on detail/update/delete/toggle,
and no non-admin list scoping, so any member could read, edit, delete or
pause/resume every schedule in the org. The tempting fix is the products one
(admin-only writes, "it's config"), but a recurring invoice is not org-wide
config like the catalogue; it is an *owned billing record*, `AssignableMixin` +
`created_by`, the exact shape of the Invoice and Estimate sitting beside it. So
the fix is theirs, not the catalogue's: non-admins are scoped to what they
created or are assigned to, admins see all, and the same `get_recurring_or_error`
helper the estimates use now guards the detail and toggle. The deciding question
is what *kind* of thing the resource is. Shared configuration everyone uses, or
an owned record with a creator and assignees, and the answer is already encoded
in its base classes. Reuse the sibling's guard rather than writing a third one;
the centralised `invoices/permissions.py` is where that reuse lives.

**Some surfaces are admin-only by their nature, not by a missing check:
recognise which.** The invoice reports went seventeenth: an org-wide financial
dashboard: total invoiced and collected, revenue by month, AR aging, who owes
what across every account. The three endpoints carried only
`(IsAuthenticated, HasOrgContext)`, so any member saw the whole org's money. This
is not the owned-record case (recurring): there is no per-user version of "the
org's revenue", and scoping the aggregates to a rep's own invoices would be a
different, lesser feature, not a fix. The right answer is that a whole-org
financial roll-up is a *management* surface, admin-only, the same bar as org and
team settings, and especially so once the invoice and estimate lists scope reps
to their own records, which makes an unscoped report the exact asymmetry those
scopes exist to close. So the fix gated all three on admin and the page shows a
plain "admins only" state (a `can_view` flag from the data layer, an empty but
valid shape so the deriveds do not trip) rather than a broken dashboard. The
judgment: before scoping a surface per-user, ask whether the surface even *has* a
per-user meaning; when it is inherently org-level, the answer is a role gate, not
a filter. Wiring it also surfaced the UI-with-no-backend pattern in
miniature, the page drew three things no endpoint returned (average time to pay,
invoiced-by-month beside paid, overdue grouped by account), and, as with every
module before it, those were *added to the real endpoints*, not faked in the data
layer: an aggregate the UI needs is a query to write, not a number to invent.

**A stored field that a server later renders is an injection sink, and the
render is the trust boundary, not the page that never shows it.** The invoice
templates went eighteenth, the highest-risk sub-page, and carried two findings at
once. The first was the by-now-familiar shape: templates are org-wide shared
config (how every invoice looks to a customer), and both endpoints were
`(IsAuthenticated, HasOrgContext)` only. Any member could create, edit, delete,
or re-point the org default. Fixed the shared-config way: admin-gate the writes,
leave the read open. But the second finding lived nowhere the page looked. A
template carries `template_html`/`template_css`, org-authored HTML/CSS that
WeasyPrint renders into a PDF with `base_url=BASE_DIR` and no URL-fetcher
restriction, reachable *unauthenticated* through the public invoice-PDF link. So
an `<img src="file:///…/crm/settings.py">` or `url(http://169.254.169.254/…)` in
that markup is a local-file read of the app's secrets and an SSRF to cloud
metadata, generated on the server, handed back as a PDF. The page is a red
herring here: it is scrupulous about never rendering the markup (no `{@html}`,
ever), which protects the *browser*, but the danger was never the browser, it
was the *server-side* render the page doesn't touch. The fix is a deny-by-default
`url_fetcher` that permits only what a real invoice loads: `data:` URIs, a file
strictly inside `MEDIA_ROOT` (the logo), HTTPS to the one configured S3 host,
and blocks every other scheme, host, and path (`invoices/pdf.py`). Two lessons
compounded: (1) when a field is *stored now, rendered later*, secure the
renderer, and note that "the UI never displays it" is not a defense when a
different, unauthenticated path renders it; (2) the same discipline that keeps
markup out of the DOM should keep it off the wire, the list serializer now
strips `template_html`/`template_css` entirely (a `has_custom_html` flag and a
byte count stand in), so the raw markup is write-only across the whole API and
the frontend's old boundary-strip became belt on top of braces.

**A create form that wrote nowhere hid what the create serializer trusted.** The
from-scratch invoice builder went nineteenth and finished the cluster. Because it
had never posted, nobody had looked hard at `InvoiceCreateSerializer`, and it
trusted three things it should not have: `status` was a plain writable field
(POST `status="Paid"` and you mint an invoice that reports as paid with no
Payment behind it: `amount_paid` stays 0, `amount_due` the full total, and AR,
aging and revenue all lie), and both `template_id` and each line item's `product`
were unscoped FKs (attach another org's template or product, a cross-tenant
IDOR), while account/contact/opportunity beside them were already org-validated.
All three sit in one serializer and were closed together: `status` made read-only
(a new invoice is always Draft; it advances only through the send/mark-paid/cancel
endpoints, which is where the state machine belongs), plus a `validate_template_id`
and a line-item product-org check mirroring the existing `validate_account_id`.
The lesson: "it doesn't write yet" is exactly why the write path rots, the moment
you wire the form, prove every FK is org-scoped and every status/flag the client
can set is one it is allowed to set.

**Driving the real form finds the mismatch a fixture can hide.** The builder's own
UI labelled Title *optional*: true for a page that never called the API, and
false the instant it did: `Invoice.invoice_title` is not blank, so the create
400s without it. A fixture can claim any contract; only the real POST tells you
which one the server keeps. The fix ran the honest direction. The backend is
authoritative, so the UI conformed to it (Title made required, `ready` gates
submit on it), not the reverse. And the builder had a second gap the serializer
exposed: it collected no contact at all, yet `contact_id` is required and
cross-validated against the account, so wiring it meant *adding* a contact
picker, scoped to the chosen account, before the form could ever succeed.

**A fixture can model a different product than the backend built, and then
"wiring" is a fork.** The service analytics dashboard went twentieth, the first
tickets sub-page, and the only module so far where the fixture and the API did
not describe the same thing. The page is a service-desk view: opened/closed
volume per day, first-response met/missed *per priority* (it refuses on purpose
to average four different promises into one number), a case-type mix, and a
per-agent "open now / closed this week" table. The backend `cases/analytics`
module computes a different model, FRT/MTTR/SLA-breach with drilldown and CSV
export, and several of the page's cards had no source at all in it. That is not
a wiring job with a defect to fix; it is a choice, and the choice changes both
what you build and how much new attack surface you add, so it belonged to the
user, not to a silent default. They chose to extend the backend to the page: one
new admin-only `analytics/service/` endpoint assembling the whole shape from the
org-scoped queryset (per-day volume, per-priority attainment against the
`workflow.py` targets, case-type counts, an agent table with its own time bases;
`open` point-in-time, `closed_this_week` a trailing week, FRT over the window),
plus the business-hours calendar name pulled from `business_hours`. The general
rule this draws out: when the mock over-specifies past what the server computes,
name the fork out loud, reshape the page to the backend, or extend the backend
to the page, rather than quietly picking one and calling it wiring.

**An org aggregate is admin-only, and the read gate is a policy call, not a
missing check.** The seven per-metric analytics endpoints already existed and
were clean: org-scoped, and deliberately narrowing a *non-admin* to their own
cases so a rep can see their own FRT. But the dashboard reads as a
whole-organisation management surface ("who is carrying it"), and showing a rep
that page scoped to their own slice under org-wide headings would misrepresent
it. So the new endpoint 403s a non-admin outright, the same shape as the
invoices reports page, and the same reasoning: a figure that only means anything
across the whole org has no honest per-user rendering. Adding that gate tightens
access; it weakens nothing. The classification is the tool. Shared config reads
wide and writes narrow, an owned record scopes per user, an org aggregate is
admin-only, and analytics, like reports, is the third kind.

**Wiring the button is how you find the missing check behind it.** The approvals
queue went twenty-first, and it carried the sharpest security finding of the set:
`ApprovalApproveView` checked that you were in the rule's approver pool and
nothing else, so an admin, who defaults into every rule's pool, could approve a
close request they had filed themselves. The old page even had a banner admitting
it (`is_own_request` was shown, but the Approve button stayed live, because
disabling it client-side changes nothing, the endpoint accepted the POST
regardless). Separation of duties belongs in the view, not the page: the fix is a
Profile-to-Profile guard (`approval.requested_by_id == request.profile.id → 403`)
in both approve and reject, with no admin exception. The whole point is that the
requester, admin or not, cannot be the decider. Both fields are `Profile` FKs, so
the comparison is `Profile.id` to `Profile.id`; comparing against
`request.profile.user` would be the always-False `created_by` type-mismatch this
codebase has been bitten by before, and here it would have silently let every
self-approval through. Two existing tests had quietly encoded the bug as the happy
path (an admin approving their own request); they now use a distinct requester,
and a dedicated test asserts the 403.

**A button guard is a claim about the server; make the server the one source.**
The queue shows Approve/Reject only where the viewer may actually act, and the
page refuses to recompute that. Pool membership and identity live server-side, so
guessing yes for someone the API says no to means a button that 403s. That guard
is a serializer field, `can_act`, and the moment the self-approval rule landed it
had to fold the same rule in: `can_act = pending AND in-pool AND not-the-requester`,
computed from `request` in serializer context. Now the button the page offers and
the decision the endpoint makes come from one function, so they cannot drift. The
own-request row loses its Approve/Reject and keeps only Withdraw, which is the
one thing a requester *may* do to their own request, and the banner explains why
rather than tempting a click that would fail.

**The rail you did not have to leave dead is the one that catches you out.**
Tickets shipped a "Linked articles" rail built from real `Case.solutions` rows,
an `<a href="/v2/solutions/<uuid>">` pointing at a page whose fixtures were
keyed `SOL-01`. It was written as a link because the data was real, and every
one of those links was a 404 waiting for the first org to file an article
against a ticket. **The crawl did not catch it**: no seeded case has a linked
solution and no seeded org has an article at all, so the link never rendered.
That is the whole failure mode, a crawler only sees the links the seed data
happens to produce, and the knowledge base had never been seeded. When a rail
renders conditionally, check the empty case by *creating* the data, not by
loading the page. The knowledge base went sixth to close it.

**A rail that renders real rows and links to nothing is the same failure,
quieter.** The contact page has listed a person's tasks from real rows since
contacts were wired, as plain text, because there was no task page to point at.
Nothing was broken and no crawler could have complained; the rows were simply a
dead end for three sessions. Tasks went seventh to close it. The general form:
after wiring a module, list not only the links that 404 but the rails that
*should* be links and are not: `grep -n 'v2-label' src/routes/v2/*/\[id\]/+page.svelte`
and ask, for each heading, whether its rows go anywhere.

**The fixture search is a rail too, and it was missed for four modules.** The
command palette searched `mock/*.js` and linked to fixture ids, so the moment a
module went live every result it offered became a 404: `acc-bluepeak`, `421`.
That went unnoticed through leads, pipeline, accounts and contacts because
nothing points *at* the palette to check. Its blocks for the five wired modules
are gone; delete a module's block in the same change that adds its prefix, until
there is a real search endpoint. **Grep for the fixture arrays, not just the
routes**, `grep -n '\.\.\.<module>' src/lib/v2/api.js`.

**A prefix can over-claim.** `/v2/tickets` is the first migrated prefix with
sub-pages that did not come with it: `analytics` and `approvals` sit under it on
different endpoints. A prefix match alone hangs "Live data" over both.
`migrated.js` grew a `NOT_MIGRATED_ROUTES` list of exact paths for that, checked
first, exact, so a real `/v2/tickets/<uuid>` cannot be caught by an entry meant
for a sibling. Both tickets sub-pages (`analytics`, `approvals`) are now live and
off that list; only `tasks/board` remains on it. Before adding a prefix, list
what is under it; when a sub-page goes live, delete its exact-path entry in the
same change.

**Data functions move to `src/lib/server/v2/<module>.js`.** Not `$lib/v2/api.js`
with a `cookies` argument bolted on. The access token is an httpOnly cookie, and
`$lib/v2/api.js` is importable from a `+page.js` and therefore from the browser.
SvelteKit refuses at build time to bundle anything under `src/lib/server` into
client code, and that refusal is the guarantee worth having. Loaders become
`+page.server.js`.

**Delete the mock rather than leave it unused.** A mock that still resolves is
one something can quietly fall back to.

**Add the route prefix to `src/lib/v2/migrated.js`.** The shell banner reads it.
One list, because two drift, and the way this one would drift is a banner
telling somebody a page is a sandbox while they edit a real customer.

**A module is done when reads *and* writes are wired.** A page listing real rows
behind a form that still pretends to save is not migrated.

**Add a line to `LIVE_COUNTS` in `routes/v2/+layout.server.js`** in the same
change, *if the module has a badge at all*. Otherwise the sidebar badge keeps
its fixture and contradicts the page it links to. The counts run concurrently;
each falls back to its fixture rather than failing the shell.

Accounts deliberately has no entry. These badges count work. Leads to call,
deals to move, tickets to answer, and "Accounts 10" counts inventory: it never
goes down and nothing about it is ever waiting on you. A badge you learn to
ignore teaches you to ignore the ones beside it. The count was added, seen to
render nothing, and removed rather than given a nav slot it had not earned.

**A cleared checkbox submits nothing, which is what "leave this alone" looks
like.** The whole PATCH scheme rests on absent-means-unchanged, and that makes an
unticked box indistinguishable from a field the form does not own, so "do not
call" could be switched on and never off. Each flag on the contacts form posts a
hidden `<field>_present` partner that is always sent, and the action reads the
flag only when its partner arrived. Any boolean on a PATCH form needs this; test
it by turning the thing off, not on.

**A multi-select needs the same marker for the opposite reason.** Deselecting
everything in a `<select multiple>` submits *nothing*: identical, over the
wire, to a field the form does not own. The ticket form's "people affected"
posts a hidden `contacts_present` so "remove the last person" is expressible at
all. Same shape as the checkbox rule, same test: try to empty it, not fill it.

**A single select over a many-to-many needs an original to compare against.**
`assigned_to` is M2M on Lead, Opportunity, Account, Case, Task and Contact, and
every edit form offers one owner. Sending it on every save rewrites the whole
list from one value.

A deal with two people on it quietly lost one whenever anybody edited the
description. All three forms now post a hidden `assigned_to_original` and the
action only forwards the field when it actually changed. This was found by
saving a real two-assignee record and counting afterwards, not by reading the
code, and it is the check to repeat on every module: save something with more
than one of everything, then count the relations.

**Check what a verb *other* than the one you are wiring does.** In four of the
five modules the object-level check was wrong in `get`/`put`/`patch`/`post` and
right in `delete` and in the list filter. Where verbs disagree, the majority is
not automatically the policy. Read both and work out which one somebody meant.

The knowledge base is the sixth and it had nothing to disagree with:
`permission_classes = [IsAuthenticated]` on all four views and no object-level
check anywhere. **Absence is easy to read past**, five modules of practice at
spotting a *wrong* check gives no help at all against a missing one, and the
file looks tidy. Ask the question the other way round: for each verb, name the
person who may not do this. On `Solution` there was no such person. A plain
`USER` rewrote an admin's article, published it and hard-deleted it, at 200,
200 and 204.

Tasks is the seventh, and it is the `created_by` type mismatch in its purest
form: the *list* spelled the creator clause correctly,
`Q(created_by=profile.user)`, and every other verb compared `request.profile`
to it, which is never equal. So a member could create a task, watch it appear in
their own list, and then get a 500 opening it and a 403 on every write. **The
one place a check is written correctly is the best evidence the others are
wrong**, and it is also the fastest way to find them: grep for the field, not
for the check.

**A dead permission clause and a writable field that feeds it are one bug, not
two.** `created_by` was writable on the task serializer and accepted any `User`
id in the database, including another org's. That was a data-integrity bug only
while nothing read the field. The moment the creator clause starts returning
`True`, an assignee can PATCH themselves into being the creator and gain the
right to delete. Fixing the comparison without fixing the field would have
converted a dormant bug into an escalation. When you repair a check, look at
what feeds it in the same change.

Cases is the fifth and it disagreed differently, which is worth knowing before
assuming the pattern: its checks compared the right *types*, and the divergence
was about **who**. The list granted access to watchers with a citation to a spec;
the detail view dropped that clause; delete dropped the assignee as well. So a
watcher's queue listed exactly one ticket and opening it answered 403.

**When you consolidate divergent checks, do not consolidate them into the widest
one.** The obvious fix, one `assert_case_access` matching the list, would have
handed every watcher the right to edit and reply, which nothing ever claimed.
Watching is subscribing, not being handed the keys. `cases/access.py` is three
rules on three lines instead: read is admin/creator/assignee/watcher, write drops
the watcher, delete drops the assignee too. Widening read access was the fix;
widening write access would have been a privilege escalation shipped as a
cleanup.

`cases/kb_access.py` is the same shape for a different reason. Writing an
article and *approving* one are not degrees of the same permission. The whole
point of a review workflow is that somebody other than the author says the
answer is right, so `write` is author-or-admin and `release` (approve,
publish, unpublish) is admin, and `release` takes no article argument at all,
because being its author is the one fact that must not grant it. Where two
verbs legitimately differ, keep two functions whose names say so.

**Gate the field as well as the endpoint.** `POST /publish/` existed and looked
like the control; `PATCH {"is_published": true}` was the same act through the
door beside it, and `PATCH {"status": "approved"}` was self-approval with no
endpoint involved. The view checks whether a request *moves* either field
before demanding an admin: a transition, not a value, so an author fixing a
typo on their own already-approved article is not stopped by a gate meant for
the person who approved it.

Contacts is the clearest case of the four, because the two defects compound: the
`created_by` comparison refused the creator, and the `users_mention` line under
it raised `AttributeError` for everyone else. A role="USER" profile could open
**no contact at all**: 403 on the ones they entered, 500 on the ones assigned to
them, while deleting the first kind worked. Four tests in `test_contacts_api.py`
pinned that behaviour rather than reporting it, with names like
`test_add_comment_with_serializer_fields_hits_save_bug` and docstrings saying
"exercises the branch and verifies the bug". **A test written to reach a line is
not a test of anything.** They assert the fixed behaviour now.

Invoices is the eighth, and the invoice itself was already right, every verb
routed through `invoices/permissions.py::get_invoice_or_error`, which compares
`created_by` against `request.user` correctly and which a note in the file says
the other apps should copy. The bug was that the *estimate* endpoints beside it
never did. **A centralised, correct check is only worth as much as the callers
that use it**. The audit's job was to find the sibling entity that reached
around it. Estimates had two shapes of the same failure at once. The detail,
convert and send views carried **no object check at all**, only an org filter,
so any member could read, edit, delete, convert or send any estimate in the org,
the `Solution` shape from the knowledge base, a missing check rather than a
wrong one. And the list carried the `created_by` Profile-vs-User mismatch in its
loudest form: `Q(created_by=request.profile)` does not silently return `False`
the way an `==` does, it reaches the query layer as `Q(created_by=<Profile>)`
and **raises `ValueError`**, so every non-admin's estimate list was a 500, not a
short list. Same root cause as the dead `==`, opposite symptom. One hides, the
other shouts. The fix generalised the invoice helper to a shared
`has_object_access` and routed the estimate views through
`get_estimate_or_error`, and the estimate PDF's creator-lockout (the familiar
`request.profile == created_by`) went with it.

**Flagged, not fixed, because it needs a decision:** any org member can `PUT` an
`InvoiceTemplate` whose `template_html`/`template_css` WeasyPrint renders with
`base_url=BASE_DIR`: arbitrary HTML to a local-file/SSRF sink, no role gate.
Restricting template writes to admins narrows it but does not close it (an admin
can still inject); sanitising the template is the real fix and a larger change.
It stays open.

Team-and-access is the ninth, and it carried the most severe finding of the
series. An org-admin escalation, live. `UserDetailView` lets a member edit
their **own** profile (the guard passes `self.request.profile.id == profile.id`
through), and the update runs through `CreateProfileSerializer`, which exposed
`role` and `is_organization_admin` as writable. So a plain `USER` could
`PATCH /api/user/<their-own-id>/` with `{"role": "ADMIN"}` and become an org
admin. Proven end to end: after the PATCH, the same token opened the admin-only
roster it had been 403'd from a moment earlier. This is **mass assignment**, not
the `created_by` type mismatch, a different class with the same shape as every
"server-derived field is client-writable" bug: the field that decides
permissions was in the serializer's `fields` and the self-edit path had no
reason to withhold it. The mock had it right and the backend did not, the
page's own header comment claimed "the server refuses to let anyone change their
own role," a rule that did not exist. The fix makes the privileged fields
(`role`, `is_organization_admin`, `has_sales_access`, `has_marketing_access`)
read-only unless the actor is an admin acting on **someone other than
themselves**, one predicate that closes both the member's self-promotion and an
admin's self-demotion (which would be the other way to strand the org without an
admin). A last-active-admin guard on the deactivate endpoint closes the only
remaining lockout path. **When you repair who may edit a record, look at what
the serializer lets them write in the same change**, the same lesson tasks
taught with `created_by`, one severity higher.

The API-tokens page is the tenth, and its finding runs the other way: **the mock
cried wolf about a hole the backend had already closed.** The page's headline,
and the matching alert on the team page, was "a token whose owner was
deactivated keeps working with their old role." It does not. `resolve_valid_pat`
rejects any token whose `profile.is_active` is false, and deactivating an account
sets exactly that flag; `test_pat_auth.py::test_inactive_profile_raises` has
proven it all along. So an "owner deactivated" token is **dormant**, refused at
login today, revived only if the account is reactivated, not a live credential.
Wiring the page truthfully meant *downgrading* a security alarm, which is only
safe once you have proven the backend is actually safe: the reframe ships with a
new end-to-end test (`test_pat_org_api.py::test_deactivated_owner_token_is_
rejected_at_the_api`) alongside the existing unit one, and the false copy was
corrected on both this page and the team page (rust → clay: a loose end to clear
on offboarding, not a breach). **A fixture that asserts a protection is a claim
to verify against the backend, in either direction. It can be more careful than
the server (the team escalation) or less (this one).**

**Flagged, not fixed, because it changes an auth chokepoint:** `resolve_valid_pat`
checks `profile.is_active` and `org.is_active` but **not** `profile.user.is_active`.
Deactivation in this app is profile-level, so the common offboarding path is
covered, but if a `User` account is globally disabled (e.g. by a superadmin)
while a profile stays active, that user's PATs keep authenticating. It is
defense-in-depth, not a live exploit in the normal flow, and adding
`or not pat.profile.user.is_active` is a one-line change, but it alters the auth
path every PAT request runs through, so it is left for an explicit decision
rather than slipped into a page migration.

### What the mock was hiding

Fixtures are written to make a design look right, so wiring one up is also the
first honest audit of it. Leads lost three panels:

- **The qualification checklist**: "decision maker identified", "budget
  confirmed", four ticks gating a Convert button. No model records any of it.
  It rendered identically on every lead, so the count was decoration and the
  gate gated nothing.
- **The always-on duplicate warning**: "Another lead shares this website", true
  of every lead the mock ever drew. It is a real query now and renders only when
  there is an answer. A warning that is always on is one people learn to skip.
- **`last_activity_at`**: a field Lead does not have. The mock file said so in
  its own header: aging (`StageAgingConfig`, `get_aging_status()`) is
  Opportunity-only. The column reads `last_contacted` now and says "Not
  contacted" where it is null, rather than substituting `updated_at`. An edit
  is not a conversation.

Real data also broke two things fixtures never could: the rail's
`grid-template-columns: 100px 1fr` scrolled sideways on a long email (a grid
track's implicit minimum is its content's min-width: it wants
`minmax(0, 1fr)`), and 11 of 20 seeded leads carry phone numbers with `x123`
extensions that the model's own `flexible_phone_validator` rejects.

Accounts lost two more, and the second was the most confident invention yet:

- **`renewal_note`** ("Renews every August", "Prospect) no contract yet",
  filled in for all fifteen fixture accounts and shown in a table column. There
  is no contract, subscription or renewal model anywhere in the backend, and
  nothing to derive one from. It read as a fact somebody would plan a quarter
  around.
- **`customer_since`**: a stored date on a model that has no such field. It
  comes back as `first_won_on`: the close date of the first deal won against the
  company, which the CRM does know. The page still says "customer since"; the
  field name says where the date came from. When an invented field has an honest
  derivation, derive it and name it after the derivation, not after the claim.

The fixture also got one thing exactly right, and it is worth copying. Its
header refused to store `lifetime_value` / `open_pipeline` / `overdue_amount`,
derived them from the records instead, and left an instruction that the real API
had to aggregate them in SQL over the org-scoped queryset. `annotate_rollups`
does. **A mock that explains what the real thing must do is worth more than one
that looks finished.**

The phone problem is wider than leads: **7 of 9 seeded accounts, 7 of 15
contacts and 11 of 20 leads** carry phones the validator rejects. On accounts it
is now visible rather than mysterious, the edit form mirrors the exact regex,
so the offending field is marked instead of the whole save failing with no field
named. The underlying mismatch still needs a decision: widen the regex, or fix
the seeder.

The pipeline lost three of its own:

- **The "next action" card**: "Call Kavi about the legal review". `Opportunity`
  has no such field and nothing derives one. A suggestion the system invented is
  worse than no suggestion, because people act on it.
- **`last_activity_at`** again, in the rail. The row reads "Stage since" now,
  from `stage_changed_at`: a narrower claim, and the one the board is actually
  coloured by.
- **A contact's `relationship`**: "Champion", "Blocker". Contact has `title` and
  `department`. The **Attached** rail of related invoices and tickets went too:
  both are real models, but neither is on the opportunity response, so filling
  that panel costs two cross-module requests per page load.

The board also stopped claiming "Drag a card to move a stage". There is a real
move endpoint, but nothing is draggable. Naming a gesture that does not exist
is the habit this redesign is meant to break.

Contacts lost the same two fields a fourth and a third time, and gained a
distinction nobody had noticed:

- **`relationship`** ("Champion", "Blocker") for the fourth time. On this page
  it was load-bearing: a table column, a rail row, the subtitle under every
  colleague, and half the headline sentence. Contact has `title` and
  `department`.
- **`last_activity_at`** again, and here it took the page's whole argument with
  it. The mock sorted the list by it, coloured a column by it, and built the
  banner on "nobody has spoken to X in 47 days". Nothing in the CRM records when
  anyone last spoke to a contact. Lead has `last_contacted`, Contact has no
  equivalent, and there is no activity table to derive one from. The column reads
  `updated_at` under the label **Updated**, which is a fact about the record
  rather than about the relationship, and the banner now only fires on things the
  data can carry: they have left, nobody can reach them, or money is riding on a
  record with no owner.
- **One account, where there are two.** The fixture gave each contact an
  `account_id`. A Contact is joined to an Account twice over: the `account` FK
  the model documents as "primary", and membership of `Account.contacts`, which
  is many-to-many, and the two are independent. In the seeded org **not one of
  the fifteen contacts has the FK set**, while twelve are in the M2M; the account
  page builds its people list from the M2M, so the account field on a contact
  form wrote to a column no page read. Setting the primary account now records
  the membership too (`link_primary_account`), the API publishes both links, and
  the pages pick the primary where there is one. Clearing the FK deliberately
  leaves the membership: it can be granted from the account side, and losing
  "primary" is not a statement that somebody left the company.

`organization` survives that but is demoted. It is free text, and across the
seeded org it routinely names a *different* company from the account the person
is attached to, so it appears only where there is no account link to show, and
is labelled "Company typed in" rather than presented as the account.

Tickets lost the thing the whole page was arranged around:

- **`#421`**: a ticket number. `Case` has a UUID and a subject and nothing else
  to call itself by. The "#" column, the crumb and every row in the "also open
  here" rail were printing a field that does not exist. The subject is the
  identifier now.
- **`first_response_target_minutes`**, and the fixture's own note about it had
  the schema backwards. It said the field "models the escalation policy"; but
  `EscalationPolicy.first_response_target` is a **Profile to notify** on a
  breach, not a duration. The target is `Case.sla_first_response_hours`, and the
  deadline derived from it walks the org's business calendar and adds any time
  the ticket spent Pending. The page takes that deadline from the server rather
  than doing wall-clock arithmetic beside it. **Check a fixture's comments
  against the model, not just its fields**. This one was cited as evidence.
- **`next_action`**: invented for the fifth time. The page states what is true
  (unanswered, waiting on the customer, nobody assigned) and gives no advice.
- **"Suggested articles"**: the rail is the articles *linked* to the ticket.
  Suggestions are a different endpoint doing keyword matching, and labelling a
  filed link as a guess misreads what the rail is for.

The sharper find was on the other side. Two fields the design leaned on are
real columns that **nothing in the codebase ever wrote**: `first_response_at`
had no writer at all, and `resolved_at` had exactly one, in
`close_with_children`. Four things read the first: the breach property, the
queue column, the escalation scan, the FRT dashboard, so every ticket in every
org was permanently first-response breached and the escalation task re-fired on
tickets answered hours ago. Both are stamped in `cases/signals.py` now. **A
field existing is not evidence anybody fills it in**; grep for an assignment
before building a column on one.

The knowledge base lost one panel and one sentence:

- **"Related articles"**: three other articles under a heading that never said
  what the relation was. There are no tags, no embeddings and no "customers
  also read"; nothing in the schema computes relatedness and nothing could.
  What the database does hold is the M2M to `Case`, so the rail is now the
  **tickets this article is filed against**: the same link the ticket page
  already draws, pointing back. An invented relation replaced by a real one is
  a better trade than an invented relation deleted.
- **"That is allowed, and sometimes right."** The new-article form offered
  `status` and `is_published` as free switches and told the writer a published
  draft was a deliberate choice. It is not a choice: `Solution.publish()` has
  always refused it. The mock could say that because it never submitted
  anything, which is the general hazard. **a form that does not post can
  contradict the model indefinitely and read as considered while it does.**

`use_count` survived under a new name (`case_count`, and it is real), and
`author` had to be added to the API: `created_by` serialized as a bare `User`
UUID, so the mock's Author column was showing a name no client could have
obtained.

Tasks lost a relation and a stat card:

- **A task attached to an invoice.** The fixture had `related: { kind:
  'invoice', … }` linking to `/v2/invoices/INV-2025-0142`, and "Chase the
  Bluepeak invoice" is exactly the task a real rep writes. `Task` has four
  optional parents (account, opportunity, case, lead), and no invoice FK.
  Nothing would have populated it. **A relation that reads as obviously
  necessary is not evidence of a column**; check the model before designing the
  rail on it.
- **"Done this week."** `Task` has no `completed_at`, only `BoardTask`, the
  other table, does, so nothing anywhere records *when* a task was finished.
  The number could only have been invented. It is **"no due date"** now, which
  is real and is the better card: a task with no due date is never overdue and
  never due this week, so it appears in no count and in front of nobody, which
  is the one thing about it worth being told.

The fixture's sort (priority, then due date, nulls last) did not survive
either, and that is fine: it was a comparator expressing "no date means
whenever, not now", and the same observation is a stat card now, where it can
be acted on rather than quietly determining scroll position.

Invoices lost a reminder log and a way of computing a summary:

- **A per-reminder history with read receipts.** The invoice detail's rail
  listed "First reminder · opened · 8d ago", "Second reminder · opened · 3d
  ago". The model records no such log and tracks no opens: it has a
  `reminder_count`, a `last_reminder_sent` and the on/off settings, and nothing
  about the client opening anything. The "they opened it" line was pure
  invention. The rail shows the real counters now. The same gap runs through
  the actions: the button says "Send a reminder" but the only endpoint is
  `POST /send/`, which re-mails the invoice and stamps `sent_at`. It does not
  touch `reminder_count`, because reminders are the automated task's job. The
  button re-sends; the counters describe the schedule; the page says both
  plainly rather than pretending the click bumped the count.
- **A summary that re-derived tax as `total − Σ(qty×rate)`.** The mock detail
  recomputed the subtotal in the browser and called whatever was left over
  "tax", which silently folds any discount into the tax line. Real invoices
  compute `subtotal`, `discount_amount` and `tax_amount` server-side, so the
  page shows those figures and adds a discount row when there is one. **A
  number the client can recompute is a number the client can get wrong**; when
  the server already has it, render it.

Team-and-access is the opposite of the usual case: the mock was more careful
than the backend, and the wiring exposed fields the API does not return rather
than fields it does.

- **Two rules the fixture and the page both claimed the server enforced.** The
  mock's own comment said "the endpoint refuses to let anyone change their own
  role or remove the last admin, and the UI mirrors those two rules as hints."
  Neither rule existed server-side: the first was the escalation above, the
  second let an admin deactivate the last admin and strand the org. **When a
  fixture asserts a protection as fact, that is a claim to verify, not a spec to
  trust**. Here the claim was aspirational and the backend had to be made to
  match it.
- **Names, teams and token counts the serializer does not carry.** The fixture
  gave every person a `first_name`/`last_name`, a `teams` array and an
  `active_token_count`. `ProfileSerializer` returns a single `name`, no per-
  person teams and no token count. Names fall back to the single field (and to
  the email when someone was invited and has not set one); teams are derived
  from `/api/teams/`, whose serializer nests each team's members, so there is
  one source of truth rather than a second field to drift; and the token count.
  A live signal on this page, since a not-yet-revoked token on a deactivated
  account is a dormant liability (stopped at login today, revived if the account
  is reactivated; see the tokens entry below), was added to the users list as a
  real per-profile count, computed once in the view rather than on the shared
  serializer every endpoint pays for. **Nothing was invented to fill a column**;
  the ones the API could not back were derived, sourced, or added, not faked.
- **The invite is not pending.** The mock's success copy said the invited person
  "will appear here once they sign in"; the endpoint creates their Profile
  immediately and active, so they show up at once as a member who has "never"
  signed in. The copy says that instead. Team CRUD (creating a team, editing
  membership) stayed a fixture, a separate write surface with its own picker,
  left as estimates were left beside invoices, so the Teams list renders
  read-only and says so.

The API-tokens page is the sharpest version of "verify the claim, don't trust
it": here the fixture was *less* careful than the backend, and the honest wiring
made the page say something reassuring where the mock had raised an alarm.

- **"Owner deactivated → still authenticating" was false.** The mock's rust
  "Needs you" banner said a deactivated owner's tokens "keep working with their
  old role." The backend rejects them (`resolve_valid_pat` gates on
  `profile.is_active`). The banner is now a clay "Loose end": the tokens are
  dormant, not live, but still worth revoking on offboarding because reactivating
  the account would revive them. Because downgrading a security warning is only
  safe if the backend really is safe, this reframe ships with a test that proves
  it end to end.
- **The owner block is real, and the risk flags are the API's job.** The fixture
  carried a `profile` sub-object per token; `PersonalAccessTokenListSerializer`
  does not. The new admin endpoint attaches an `owner` (name, role,
  `is_active`) and derives `orphaned`/`unused_90d`/`live` totals in the view, so
  every number and every "deactivated" tag is backed, not drawn.
- **The one-time reveal is now real.** The mock never created a token, so its
  "value shown once" rule was only a caption. Create posts to the self-scoped
  `/api/profile/tokens/`, the raw value comes back in that one response and is
  shown once behind a Copy button; the list has only the 13-char prefix
  thereafter, and the server keeps a hash. **The page is admin-only**. It lists
  every owner's tokens, so a non-admin gets the same clean "Admins only" state
  team uses, and no token metadata leaks to a member at all.

### PATCH, not PUT

**All five** of `LeadDetailView.put`, `OpportunityDetailView.put`,
`AccountDetailView.put`, `ContactDetailView.put` and `CaseDetailView.put` call
`.clear()` on their M2M fields unconditionally: `assigned_to`, `tags`,
`contacts`, `teams`, and re-add only what the body carried. A form that owns the
scalar fields would therefore strip every tag each time somebody corrected a
phone number; on an account it would strip every contact, off the record whose
whole purpose is the people. `patch` guards each with `if "<field>" in params`.
Pinned by `TestPutClearsRelations` in
`backend/leads/tests/test_lead_access_and_totals.py`, and verified on the
pipeline, accounts, contacts and tickets by saving a record with several of
everything and counting afterwards. **Stop assuming and start checking: five for
five.**

Six for six, and the sixth has a different reason worth knowing, because
`Solution` has no M2M a form touches. There the danger is the **permission
gate**: a PUT sends every field on every save, so it always looks like a
request to set `status`, and an author correcting a typo on their own approved
article would be told they may not approve it. Where a gate reads transitions,
sending unchanged fields manufactures transitions. The edit form posts a hidden
`status_original` and forwards `status` only when it actually moved, the same
marker pattern as `assigned_to_original`, guarding an authorization check
rather than a relation.

Seven for seven, and the seventh needs it for **both** reasons at once.
`TaskDetailView.put` clears `contacts`, `teams`, `assigned_to` and `tags`, so a
form that edits a title would unassign everyone; and a PUT also sends all four
parent columns, which the "one parent entity" rule reads as an attempt to add a
second one. The task edit form posts `parent_kind_original` and
`parent_id_original` and only sends the parents when they actually moved.

That last one has a wrinkle the other markers do not: when the parent *does*
change kind, both columns have to travel in the **same request**. The old one
set to `null` alongside the new one, because sent one at a time they are, quite
correctly, two parents. The rule refusing that is the rule working; the form has
to speak in whole states rather than in steps.

Absent also has to stay absent all the way through. On a converted lead the
status select is disabled and submits nothing; sending its current value anyway
trips `validate_status`, which reads `value == current` on an irreversible
status as a repeat conversion and rejects the whole save, including the job
title you were actually trying to fix.

### Totals are the API's job

`totals` is computed in `get_totals` on both list views, over the whole filtered
queryset and after the org scope and the non-admin narrowing. Never reduce over
`results` on the client: `results` is one page, and a page total labelled as a
list total is the specific bug this redesign exists to fix.

The pipeline adds `amount_sum`, `weighted_sum` (SUM of `amount * probability /
100`) and `stalled_count`. `stalled_count` shares one `stalled_filter()` helper
with the `?rotten=true` query, so the number in the header and the red pills on
the rows underneath it cannot mean two different things,
`test_agrees_with_the_pill_on_the_row` asserts exactly that.

The same view gained `?open=true`. The existing `stage` filter is a `contains`
match and cannot express "not closed", so the chip reading "Stage is not Closed"
was previously satisfied by fetching everything and dropping the closed ones
client-side, correct only until the first page boundary.

Accounts needed a different shape: not one total per list, but seven figures per
*row*: `won_amount`, `won_count`, `open_pipeline`, `open_deal_count`,
`overdue_amount`, `open_tickets`, `first_won_on`. `annotate_rollups` in
`accounts/views.py` attaches them to both the list and the detail queryset, so a
number cannot change meaning depending on which page you read it from, and the
serializer emits them under `rollups`: `null`, not a row of zeroes, anywhere
that did not annotate.

Two things about that annotation are easy to get wrong:

- **One subquery per figure, never `.annotate(Sum(...), Count(...))`.** Two
  aggregates over two different relations join both tables at once, so every
  deal is counted once per invoice and every total comes back multiplied.
  `test_deals_and_invoices_do_not_multiply_each_other` puts two deals and three
  invoices on one account and fails loudly if anyone collapses them.
- **An empty correlated subquery is NULL, not zero.** `.values("account")
  .annotate(...)` yields *no row* for an account with no matching records, so
  the `Coalesce` has to be on the outside. Without it every figure on a brand
  new account renders blank rather than zero.

Where a figure exists elsewhere, it is defined once and imported: open stages
come from `opportunity.workflow.CLOSED_STAGES`, open tickets from
`cases.workflow.TERMINAL_STATUSES`, unpaid invoices from a new
`invoices.models.UNPAID_STATUSES` that the AR aging report now shares. Overdue
money is keyed off `due_date`, not off `status == "Overdue"`. That status is
set by a nightly Celery task, so anything reading it alone reports nothing on a
day the task has not run. On the seeded database that is worth $47,384.40 on one
account.

Tasks needed seven figures over one queryset, which is the case where the
obvious spelling costs the most: `count`, `open`, `overdue`, `due_today`,
`due_this_week`, `no_due_date` and `unassigned`, written as seven `.count()`
calls, were **eight COUNT(\*) round trips** on every page load, all scanning the
same rows. One `aggregate()` of `Count("id", distinct=True, filter=Q(...))`
answers all seven in one query and took the list from 18 queries to 12.
`distinct=True` is not optional there: a non-admin's queryset joins the assignee
M2M, so without it a task with two assignees counts twice.

The list also gained the form catalogues the other modules already publish:
`status`, `priority`, `users`, `accounts_list`, `contacts_list`, which is what
the third of the three red tests in `tasks/` had been asking for since it was
written. They are **119 KB of the 128 KB response** on the seeded org, so
`?slim=true` omits them; the list pages send it and only the forms do not.

### Lanes come from the kanban endpoint

The board does **not** group the paginated list. Lanes built from one page are
wrong in the way that looks right: every column renders, each is just short.
`/opportunities/kanban/` returns each column with its true `item_count` and the
same RBAC scoping, capped at 100 cards per column. The lane says so when it is
truncated rather than stopping silently.

### Still missing

**Leads**, no create, import, kanban, bulk actions, delete, or **Convert** (the
button states the consequence and is disabled).

**Pipeline**, has create. Missing: delete, bulk actions, line-item editing,
drag-to-move, and comments (the timeline reads them, nothing posts one).

**Accounts**, has create and edit. Missing: delete, bulk actions, attaching
contacts/teams/tags (the form states that it does not touch them), comments, and
the activity timeline. The list has no filter controls: the loader forwards
`search`, `name`, `city`, `industry`, `assigned_to` and `tags` if a URL carries
them, but nothing on the page sets one, a filter chip that changes the URL and
nothing else is worse than no chip.

**Contacts**, has create and edit, including the active/inactive split, which
is a real API filter now (`?is_active=`) rather than rows discarded after they
arrive. Missing: delete, bulk actions, import (the endpoints exist:
`/contacts/import/preview/` and `/import/commit/`), attaching teams/tags,
removing somebody from an account, and posting a comment. Comments *are* now
recordable (the endpoint could never save one before) and the detail page
reads them, but nothing writes one yet. The account picker holds 200 accounts
and says so when there are more; past that it wants a search rather than a
select. The list forwards `search`, `name`, `city`, `email`, `phone`,
`assigned_to` and `tags` but, as with accounts, nothing on the page sets them.

**Tickets**, has create, edit, replying (public or internal note) and status
changes including close and reopen. Missing: delete, bulk actions, import,
attaching files, merging, watch/unwatch, linking a knowledge-base article,
parent/child, and time entries. `/v2/tickets/analytics` and
`/v2/tickets/approvals` are still fixtures and `NOT_MIGRATED_ROUTES` keeps the
banner honest about them. The queue forwards `search`, `priority`, `case_type`,
`account` and `assigned_to` and sets `status` itself; as with the others,
nothing on the page offers those controls yet.

**Knowledge base**, has create, edit, the review workflow (send for review,
approve) and publish/unpublish. Missing: delete (the API has it, gated to
author-or-admin; no page offers it), linking an article to a ticket from either
page, and the customer-facing KB site the `kb_views` docstring records as a
deliberate cut. The list forwards `search`, `status` and a `visibility` chip
that maps to `?is_published=`, and, as with every other module, nothing on
the page sets them yet.

Worth knowing before building on `is_published`: with the customer-facing site
cut, publishing today gates exactly one thing, the agent suggester behind the
reply composer (`/cases/<id>/solution-suggestions/`). The flag is not currently
a public-exposure switch. It is still worth gating as one, because that is what
it will be the day the site ships and because the review workflow is an
integrity control in its own right, but the severity of the hole it left is
"anybody could approve their own answer", not "anybody could publish to the
internet".

**Tasks**, has create, edit, delete (admin or creator), the per-row tick,
reassigning, and comments including posting one. Missing: bulk actions,
attaching files, tags and teams (the API takes them; no form offers them),
editing or deleting a comment, and any filter control, the loader forwards
`search` and `priority` and nothing on the page sets either.

`/v2/tasks/board` stays on fixtures and is listed in `NOT_MIGRATED_ROUTES` for a
sharper reason than "not done yet": a card is a `tasks.BoardTask`, a **different
table** from the `tasks.Task` the list writes. They share a word and nothing
else, a card never appears in the task list and a task never appears on a
board. Wiring one did not wire the other, and a prefix match would have claimed
it did. Whether the product wants two task systems is a question for somebody
else; until it is answered, the banner should not imply they are one.

The parent picker on the task forms needs JavaScript: "which record" only
renders once a kind is chosen. Every other v2 form degrades without it. Worth
fixing if no-JS is a goal, and worth knowing either way.

`/leads`, `/opportunities`, `/accounts`, `/contacts`, `/cases`, `/solutions` and
`/tasks` all still serve v1 for those reasons. No switch is safe while it would
remove something people use.

### Two things about tickets somebody has to decide

**Ticket subjects must be unique per org, case-insensitively.**
`CaseCreateSerializer.validate_name` enforces it. For a helpdesk that is a
strange rule, "Cannot log in" is a subject three customers will send this week,
and email-to-ticket does not go through this serializer, so the constraint
binds the UI and not the inbox. Left as found; the create form warns about it up
front so the refusal is at least legible.

**The two seeded Closed tickets still have `resolved_at = NULL`.** They were
closed before anything wrote that field. New closes are stamped; old rows are
not backfilled, so they read as "resolution breached" forever. A backfill from
`closed_on` is defensible but it rewrites existing records, which is not a
drive-by.

### Two things about the knowledge base somebody has to decide

**Existing published drafts.** Any org that used these endpoints before this
change can hold articles that are `is_published` and not `approved`, the
create path allowed it. The serializer now enforces the invariant only on
requests that *move* one of the two fields, deliberately: judging the stored
combination on every request would make those rows uneditable, refusing a
`PATCH {"description": ...}` with an error about `is_published`, when editing
is exactly how somebody would fix one. So they stay as they are until touched,
and the first edit that moves either field repairs them. A data migration that
unpublishes them outright is the other answer and it is a decision, not a
cleanup: it removes articles from the suggester without telling anybody.

**Nothing seeds the knowledge base.** `seed_data` creates leads, accounts,
contacts, deals, tickets and invoices, and zero articles, which is why the
module sat unaudited and why the ticket rail pointing at it was never seen to
break. A few seeded articles, one of them filed against a seeded ticket, would
have caught this a session earlier and would make the crawl meaningful for
whatever links here next.

### One thing about tasks somebody has to decide

**`Task.save()` calls `full_clean()`, and no other model here does.** It is why
the "one parent entity" rule reached the database at all, and it is also why
breaking that rule used to be a 500: Django's `ValidationError` is not DRF's, so
nothing translated it. The view catches it now and answers 400 with the model's
own message, which is the right handling, but the underlying arrangement means
**every future constraint on `Task` will arrive as an exception from `save()`
rather than from a serializer**, on every write path including Celery tasks and
the admin. Either the other models should do the same and the API should
translate centrally, or `Task` should stop and the rules should live in the
serializer with the rest. Two models validating in two different places is the
state that produced this bug.

### Three things about invoices somebody has to decide

**The invoice template is an HTML-injection sink and nobody guards it.** Any org
member can `PUT /api/invoices/templates/<id>/` with arbitrary `template_html`
and `template_css`, and WeasyPrint renders them with `base_url=BASE_DIR` when a
PDF is generated. A local-file read and SSRF primitive, with no role check. The
same is true of `Product` and `RecurringInvoice` writes: org-shared billing
config a plain member can rewrite. Gating those writes to admins is a strict
improvement and removes the non-admin path to the sink, but it does not close
the sink itself (an admin can still inject), which needs the template rendered
in a sandbox or run through a sanitiser. This session flagged it and left it;
it is a real decision, not an oversight.

**`/v2/invoices/new` is still a preview, on purpose.** The list, the detail and
the lifecycle writes (send, mark-paid, cancel, duplicate) are live, and
Duplicate is itself a real create path that lands on a fresh draft. But the
from-scratch builder, with its line-item editor and product/account pickers, is
a larger surface than the read-plus-lifecycle scope this change covers, so it
stays on fixtures in `NOT_MIGRATED_ROUTES`. Whoever picks it up wires
`createInvoice` and the line-item payload; nothing else about the module is
waiting on it.

**"Send a reminder" is `POST /send/`, which is not quite a reminder.** There is
no manual-reminder endpoint that bumps `reminder_count`. That counter belongs
to the automated `send_invoice_reminders` task. The button re-mails the invoice
and updates `sent_at`, which is the closest real action, and the page is honest
about the split (the rail's counters describe the schedule; the button says
"Sent to the client"). If a manual reminder that increments the counter is
wanted, it is a new endpoint, not a frontend change.

### Two things about API tokens somebody has to decide

**A globally-disabled `User` keeps their PATs.** `resolve_valid_pat` checks
`profile.is_active` and `org.is_active` but not `profile.user.is_active`. The
app's normal offboarding path (the team page's Deactivate) is profile-level, so
it is covered. A deactivated member's tokens are rejected. But if a superadmin
disables the underlying `User` account while a profile stays active, that user's
tokens keep authenticating. Adding `or not pat.profile.user.is_active` to the
check is one line, but it sits on the auth path every PAT request runs through,
so it wants an explicit decision rather than being folded into a page migration.
Flagged, not fixed.

**Self-service token management is not on this page.** `/v2/settings/api-tokens`
is the admin *oversight* view: every token in the org, read and revoke, gated
to admins. A regular member has no v2 surface to create or see their own tokens;
the self-scoped `/api/profile/tokens/` endpoint exists and is wired for create,
but there is no member-facing page around it yet. That is deliberate scope, the
same way team CRUD was left beside team, a "your tokens" page for the profile
area is the follow-on, not a gap in this one.

### Two things about organization settings somebody has to decide

**There are two org-update endpoints, and the obvious-looking one is the wrong
one.** `OrgUpdateView` (`PUT/PATCH /api/org/<id>/`) writes only `name`; every
other field in the request body is silently dropped. `OrgSettingsView`
(`PATCH /api/org/settings/`) is the serializer-based one the settings page uses,
and it takes the org from `request.profile` rather than a URL id. The v1 org
settings page already sends `domain` and `description` to `/api/org/settings/`,
neither of which is a field on the serializer, so those are dropped too. The two
should converge on one endpoint (the settings one), and the half-built
`OrgUpdateView` should either learn the full field set or be retired, so no
future caller "saves the org" through the path that keeps only its name. Flagged,
not fixed. Retiring an endpoint is a decision, not a page migration.

**Reading org settings is open to every member; only editing is admin-gated.**
`GET /api/org/settings/` has no role check: any member sees the company profile,
tax ID, contact details and locale. That is intentional (a member legitimately
sees their own org's address and currency, and the same values are printed on
invoices they already handle), and the v2 page renders read-only for a non-admin
with no edit affordance. If the tax ID or billing email is ever considered
admin-only, the gate goes on the GET, not on the page. The page is not the
boundary. Worth a deliberate call rather than an assumption either way.

## The kanban board: /v2/tasks/board (twenty-second module, last sub-page)

This finishes the sub-pages: every page under a migrated prefix now reads and
writes the real org, and `NOT_MIGRATED_ROUTES` is empty. A board card is a
`tasks.BoardTask`: a different table from the `tasks.Task` the list page writes,
so a prefix match never covered it, which is exactly why it was the one route
still opted out.

**Wiring the drag is what found the missing check behind it.** The page had no
interactivity at all: `svelte-dnd-action` was a dependency with zero uses, and
the "New card" / board-picker buttons had no handlers. The board reads two calls
now; `GET /boards/?archived=false` for the picker and the active board, then
`GET /boards/<id>/columns/`, which nests each column's cards: flattened into the
`{ board, boards, columns, tasks }` shape the lanes join by `column_id`. The one
write is the move: on drop, the destination lane POSTs `PUT /boards/tasks/<id>/`
with the new `column` and `order`. Lanes are optimistic; a rejection reloads and
the card snaps back, so the board never shows a move the backend didn't make.

**The backend said it could move a card and it couldn't.** `BoardTaskDetailView.put`
is documented "including moving to a different column," but `column` was
`read_only` on `BoardTaskSerializer`, so DRF stripped it from `validated_data` and
every cross-column PUT returned 200 with the card still in its old lane, a
silent no-op, and the kanban's whole point. Fixed in the view, not by making
`column` freely writable: the target is resolved and validated to a **sibling
column of the same board** (`BoardColumn.objects.filter(pk=..., board=board)`),
so a card can't be relocated to another board's, or another org's, column by
mass assignment. A bad or foreign column id is a 400, not a no-op. The move and
any reorder then resequence the touched lane(s) to a contiguous `0..n-1` so the
drop lands exactly where it was released and reloads are deterministic (the
`order` field has no uniqueness constraint, so "save the dropped index" alone
leaves ties). One PUT does the whole move; there is no batch-reorder endpoint and
none was added.

**A serializer with `fields = "__all__"` is a write surface until you name it.**
The same edit narrowed the rest of `BoardTaskSerializer` to exactly a card's own
content: title, description, order, priority, due_date, and assignees via the
write-only `assigned_to_ids`. `created_by`/`updated_by` are now read-only (they
FK `common.User`, which RLS does **not** org-scope, so they were the one live
cross-org mass-assignment here. A client could stamp any user's id onto a card);
the CRM-link FKs `account`/`contact`/`opportunity` are render-only (RLS covers
cross-org in prod, but the querysets were unfiltered, the same hole
`TaskCreateSerializer` hardens). `account` renders as `{id, name}` for the card
chip. Seven tests, both ways: the move persists and lands at its index; a
cross-board and a nonexistent column both 400 and leave the card put; a reorder
resequences with no duplicate `order`; the source lane recompacts; `created_by`
can't be overwritten.

**Drag was the deliberate call, against the pipeline precedent.** The pipeline
board, the same lanes-of-cards shape, was built *without* drag on purpose ("an
interface that names an action it can't do is worse than one that doesn't"), and
changes a deal's stage on its detail view. A board card has no detail page: the
card *is* the interaction, and the fixture framed the drag as its core. Asked and
confirmed drag rather than assume it; the backend fix is identical either way, so
only the page's mechanism differed. `svelte-dnd-action` brings keyboard support
(tab to a card, space to lift), which a bespoke drag would not have. The board
picker is a native GET form (no `goto`), and a non-member sees "No boards yet";
`GET /boards/` scopes to owner-or-member, so the empty-state branch is a real
authorization outcome, not just a fallback.

## Your account: /v2/profile (twenty-third module, first standalone)

The first module that is not under a migrated prefix: the sub-pages plan is
finished, so this begins the standalone routes (profile, notifications, the
dashboard home, the settings cluster, the public portal). It reads three calls
into one `$lib/server/v2/profile.js`: `GET /profile/` (the current profile, own
only, `request.profile`, extended with the caller's team names),
`GET /org/` (every org the caller is a member of, scoped to
`Profile.objects.filter(user=request.user)`: their memberships, not a directory
of orgs), and `GET /profile/tokens/` (counted here into the "active" number).
Two page fields the fixture invented, job title and time zone, have no column
on the Profile model, so they were dropped rather than faked into two new
columns; teams, which the fixture also showed, *is* real (`Profile.user_teams`)
and was surfaced instead.

**The self-edit door had to be checked for the escalation the /api/user/ door
already closed.** `/v2/profile` posts to `PATCH /profile/` (`ProfileView`), a
different endpoint from the `/api/user/<id>/` one where a USER could once PATCH
their own `role` to ADMIN. This view was already safe by construction. It
hand-read only `phone` and `name` from the body, but "safe because the code
happens not to read the field" is one refactor away from a hole. It now validates
through `ProfileSelfUpdateSerializer`, which *names* only `name` and `phone`:
role, org, and the access flags are not fields there, so no request body can
escalate a profile through this door regardless of how the view is later
rewritten. Tested both ways: a USER PATCHing `{role: ADMIN, is_organization_admin:
true, phone: ...}` gets 200 with the phone changed and every privileged field
unmoved.

**The same serializer turned a raw write into a validated one.** The old view
wrote `request.data["phone"]` straight onto the column, so any string landed;
`phone` now runs through `flexible_phone_validator` and a bad value is a clean
400, not an unvalidated string in the DB (blank is still allowed and clears it).
The view also 400s without org context instead of serializing `None`.

**Switching org is a real action, so it is wired, as an action, not a field.**
The page shows a per-org Switch button (against the pipeline "don't name an action
you can't do" rule, this one *can* be done). Its `switchOrg` action POSTs to
`/auth/switch-org/`, which issues a token only for an org where you have an
active profile, else 403, and swaps the httpOnly `jwt_access`/`jwt_refresh`/`org`
cookies the shell reads, mirroring the v1 `/org` picker. Verified end to end: an
admin who belongs to two orgs switched between them and the current-org flag
followed; the httpOnly cookies the switch set could not be overwritten from
`document.cookie` afterward, which is the point.

**Editing surfaced the seed-phone trap and closed it the leads way.** Some seeded
numbers carry an extension (`...x655`) the validator rejects. The edit form offers
name and phone together, so a plain name change would have re-sent an untouched
bad phone and been blocked by it, so the form only sends a field the person
actually changed (the PATCH leaves an absent field alone), the same rule the
leads owner-select uses. A USER with an invalid seeded phone can rename themselves
without first fixing a number they never touched.

## Notifications: /v2/notifications (twenty-fourth module)

The backend here was already right. Every endpoint (`GET /notifications/`,
`POST /<id>/read/`, `POST /read-all/`, `DELETE /<id>/`) scopes to
`recipient=request.profile`, and the both-ways tests already exist
(`test_cannot_mark_read_other_users_notification`, `..._does_not_touch_other_users`,
`test_list_returns_only_my_notifications`). So this module changed no backend code;
the findings were both on the page.

**The buttons were fictions.** The fixture page's `markRead`/`markAllRead` only
mutated local state, "nothing is persisted yet" said the comment, so the
Mark-read and Mark-all-read controls looked like they worked and never told the
server. They now POST to `?/read` / `?/readAll` actions (which call the
recipient-scoped endpoints) while keeping the optimistic local update for instant
feedback, and revert if the write is refused. The read fetch is `keepalive` so
clicking a notification, which both opens the ticket and marks it read, does not
lose the mark to the navigation it triggers. Verified end to end: mark-one and
mark-all both survived a reload (the count came back from the API, not the page).

**The link was rewritten wrong, and it is a field the page navigates to.**
`Notification.link` is a stored *client* path, and a reader's browser goes there.
The fixture's rewrite was `link.replace(/^\/cases\//, '/v2/tickets/')`, which
handled the old broken prefix but left a correctly-produced `/tickets/<id>` link
pointing at the *v1* route (out of the v2 app), and passed any other stored value
straight through as a live `href`. `resolvedLink` now rebuilds a fresh
`/v2/tickets/<id>` from *either* recognised prefix and returns '' for anything
else, so every notification stays inside v2 and a stored value can never become an
arbitrary link or an open redirect. Confirmed with a seeded `/cases/` row and a
`/tickets/` row both resolving to `/v2/tickets/<id>`, and the "written before the
fix" footnote counting the one old row.

**The nav badge stopped lying too.** `+layout.server.js` overwrites migrated
modules' fixture counts with real ones; notifications was still on
`notificationTotals.unread` (a fixture 3). It now calls `countUnread` (a cheap
`limit=1` call returning the API's `unread_count`), so the sidebar badge matches
the page, and drops as you read, since unread is work, not inventory.

## Today: /v2 (twenty-fifth module, the home screen)

The `/v2` home ("Today") was the hardest kind of wiring to classify: the page is
a single prioritised, cross-model **action queue**: quiet deals, tickets past a
first response, overdue invoices, tasks due, while the only backend at
`/api/dashboard/` (`ApiHomeView`) is a v1 **KPI dashboard**: counts, entity
lists, pipeline-by-stage, revenue metrics. Two different products. There was no
Today-queue endpoint, and the dashboard computes nothing for the fixture's
overdue-invoice, ticket-SLA, deal-aging or "cleared yesterday" items, the
[[fixture-backend-product-mismatch]] fork, brought to the user rather than
defaulted. The user chose to **build the queue endpoint** (as with analytics),
preserving the flagship UX.

**The new endpoint: `GET /api/dashboard/today/` (`ApiTodayView`).** Org-scoped
on every query (org from the JWT via middleware, never the client) and, for a
member, restricted to rows assigned to or created by them. The same visibility
`ApiHomeView` applies; admins see the whole org. It is guarded by
`(IsAuthenticated, HasOrgContext)`, which also closes a None-guard gap the older
`ApiHomeView` still has (that view is `IsAuthenticated`-only and would
`AttributeError` on `request.profile.org` for a profileless token). Four sources
fold into one ranked list: unanswered cases (SLA-breached first), overdue
invoices, quiet deals, and overdue/due tasks. Deal aging is translated to
`stage_changed_at` **date cutoffs** so "quiet" is a DB filter, not a per-row
Python scan. `summary` carries the true urgent `count` (uncapped), `quiet_deals`
/ `quiet_value`, and a `cleared_yesterday` morale line; `later` is the next
seven days. 15 tests, both ways: each source lands in the right bucket
(in-queue vs in-`later` vs absent), a member sees only their own rows while an
admin sees the same colleague-owned row, cross-tenant isolation, and unauth →
403.

**The page needed no reshape, only honest empty states.** The fixture was
already built to the `{ queue, summary, later }` contract, so `getToday` in
`$lib/server/v2/today.js` just reads the endpoint. But fed real (often sparse)
data, the hard-coded header copy read wrong: the sub-line now adapts ("Nothing
needs you right now" at zero; the "gone quiet. Those are first" clause only when
there are quiet deals), an "Inbox zero for today" card replaces the empty queue,
and the "Later this week" / "cleared yesterday" blocks hide when empty.

**One migrated-list subtlety.** `/v2` is the dashboard *root*, so adding it to
`MIGRATED_ROUTES` (which matches by prefix) would hang "Live data" over every
still-fixture page under it: settings/*, timesheet, the portal. It is registered
in a new `MIGRATED_EXACT` list instead, matched as an exact path and never as a
stem. This module is read-only (the home links out to each item's own module for
writes), so there was no DB to restore. Browser-verified admin (39 urgent, 7
quiet worth $5.29M) and member (a scoped 13 / 2, no admin rows leaked) plus
mobile 400px.

## Macros: /v2/settings/macros (twenty-sixth module, first settings sub-page)

The lightest of the settings cluster and a pure read: the page lists canned
replies (org-shared and personal), marks which placeholders are typoed, and
shows the seven tokens the server actually expands. The only mutating control is
a "New macro" builder, which is not wired (deferred, like the other settings
create forms). `getMacros` in `$lib/server/v2/macros.js` reads `GET /macros/`;
the one reshape is `owner`. The API returns a Profile id plus a separate
`owner_name` (the owner's email; `User` has no display name), folded into the
`{ id, name }` the card prints.

**Two things the list endpoint didn't return yet.** The stat cards need
per-scope **`totals`** (`count`/`org`/`personal`/`inactive`/
`with_unknown_placeholders`) and the reference card needs the **`placeholders`**
set. Both were added to the list response. `totals` is computed over the
requester's *visible* set (org rows + their own personal) so it never counts a
colleague's private macro, and independently of the `active`/`search` filters so
"Turned off" and "Broken placeholders" stay meaningful when the list is filtered;
`with_unknown_placeholders` folds in Python because it depends on
`find_unknown_placeholders`, the same server check the per-row field uses.
`placeholders` is served straight from `macros/render.py`, which is now the
single source of the supported set, `SUPPORTED_TOKENS` (used by the renderer and
the typo detector) is *derived from* an ordered `SUPPORTED_PLACEHOLDERS` tuple
that pairs each token with its description, so the page's reference card cannot
drift from the tokens the server expands.

**The finding the wiring surfaced: two authorization gaps.** (1) All three
macro views were `(IsAuthenticated,)` only, missing `HasOrgContext`, the same
profileless-token `AttributeError` risk `ApiHomeView` still carries; brought in
line with the documented standard. (2) A **personal-macro existence leak**:
`GET` of a personal macro you don't own returns 404 (invisible), but the write
verbs (`PUT`/`PATCH`/`DELETE`) returned **403** for the same row, so the id
space leaked which rows were somebody else's private macros. Fixed to 404 on the
write path too, matching the GET; an *org* macro edited by a non-admin stays 403
because it *is* visible (authorization refusal, not a hidden object). 8 new
tests: totals over the visible set / not counting another user's personal /
cross-org isolation / the server-owned placeholder set, and every verb on
another user's personal macro → 404 while the org-macro case stays 403.

Read-only, so the fixture and `listMacros` facade stay in `api.js`/`vocabulary.js`
purely to feed the not-yet-wired settings **index hub** its count. The same way
the already-live api-tokens and organization fixtures still do. Browser-verified
admin and member (personal isolation holds both directions; the typoed macro
flags "Broken placeholder" with the wavy underline and "sent 12 times" warning)
plus mobile 400px. Seeded macros were deleted afterward; the page writes nothing.

## Tags: /v2/settings/tags (twenty-seventh module, second settings sub-page)

Another read-only settings page. It lists every label the org shares (across
accounts, leads, deals, tickets), each with per-model usage counts, flags the
active ones applied to nothing, and pairs up near-duplicate names. `getTags` in
`$lib/server/v2/tags.js` reads `GET /tags/?include_archived=true`. Archived is
requested so the page can show the "Off" ones an admin has retired. Writes ("New
tag", the duplicate "Merge") are deferred builders.

**What the list endpoint was missing. The whole analytics layer.** `GET /tags/`
returned bare tag rows: no usage, no totals. Added both. Per-tag `usage`
`{ accounts, leads, opportunities, cases }` is computed as **one correlated
subquery per relation** (`_usage_subquery`), not four `Count`s on a single query,
joining all four M2M through-tables at once multiplies rows (the subquery-
rollup rule). Each subquery is **explicitly filtered by org**, not left to RLS:
usage is a per-org fact, and RLS is inert for the app's DB role in dev/test, so
the ORM filter is the actual guard, a test attaches the same tag row to a
foreign-org account and asserts the count stays 1, not 2. `totals`
`{ count, active, unused }` is computed over the *whole* org tag set,
independent of the list's active/name filters, so the stat cards stay correct
when the list is filtered; `unused` = active tags whose four counts are all zero.

**The authz finding** was the same class as macros: all three tag views
(`TagsListView`, `TagsDetailView`, `TagsRestoreView`) were `(IsAuthenticated,)`
only. Added `HasOrgContext`. Reads stay open to any member (tags are shared
config every record editor needs for pickers); writes were already admin-gated.
6 new tests (usage per model, unused-is-zero, totals count/active/unused,
cross-org usage isolation, cross-org totals isolation) plus a corrected
pre-existing `test_unauthenticated` that asserted `pytest.raises` where DRF now
returns a 401/403 Response. Full tags suite 33 pass.

Read-only, so `listTags` + the `tags`/`tagTotals` fixtures stay for the index
hub (#63), same as macros. Browser-verified admin **and** member (reads open, so
both see the same org-shared tags, no per-user scoping) against real seed data:
5 seeded tags with genuine usage, plus a seeded near-dup / unused / archived to
light up the duplicate banner, the "Unused" pill and the "Turned off" count;
mobile 400px reflows the table to cards. Seeded rows deleted afterward.

## Custom fields: /v2/settings/custom-fields (twenty-eighth module, third settings sub-page)

Read-only. Lists the schema an org added to its records, grouped by the record
type each field extends, and surfaces the number that matters: `records_missing_value`.
`getCustomFields` in `$lib/server/v2/custom-fields.js` reads `GET /custom-fields/`
(no filter, the page dims the turned-off fields, so it needs them). "New field"
is a deferred builder.

**Unlike macros and tags, the authz was already right**: the three
custom-field views already carry `(IsAuthenticated, HasOrgContext)` and gate
writes on admin, with reads open to members (the record editor needs the schema).
So this module's work was purely the analytics, and the finding is a computed
one, not an auth hole.

**`records_missing_value`: counting the records that predate a rule.** Custom
values live in each entity's `custom_fields` JSONField, and the value pipeline
never stores an empty one, so a record "has a value" for a field iff its JSON
carries the key: `missing = <org rows of the target_model> − <rows with the key>`
(`custom_fields__has_key`, which works on both Postgres and the SQLite test DB).
`_TARGET_MODELS` maps each of the nine `SUPPORTED_TARGETS` to its model; every
count is **explicitly `org=`-scoped**. RLS is inert for the app's dev/test DB
role, so without the filter the number would fold in (and leak the existence of)
another org's records. A test seeds three foreign-org cases and asserts the count
stays 1. `totals` `{ count, active, models_extended, required_with_gaps }` is
computed over the org's whole definition set, independent of the list filters;
`required_with_gaps` matches the page's gap banner exactly (a required field with
any rows missing it). 5 new tests (missing-value math, totals, cross-org
isolation, non-admin still sees analytics); 34 custom-fields tests pass.

Read-only, so `listCustomFields` + the fixtures stay for the index hub (#63).
Browser-verified admin and member (reads open) against seeded definitions on Case
and Lead (a required dropdown with a partial gap (one case filled, four not) a
required Lead field missing on all 20, an optional textarea, and a turned-off
checkbox): the gap banner reads "Severity tier is missing on 4 tickets; Budget
band is missing on 20 leads", the stat cards and Required/Off pills and the
filter icon all render, and mobile 400px collapses the two-column grid to one.
Seeded definitions deleted and the one modified case restored afterward.

## Reopen policy: /v2/settings/reopen (twenty-ninth module, fourth settings sub-page)

Read-only. A four-field admin singleton (reopen-on-reply, window, comes-back-as,
notify) plus three 30-day metrics. `getReopenPolicy` reads
`GET /cases/reopen-policy/`; the whole flat response is the page's `policy`.
Authz was already right (`ReopenPolicyView` GET+PUT both `HasOrgContext` +
admin-only), so, like custom-fields, the work was the analytics, and "Edit
policy" is a deferred builder.

**This was the first page where the fixture's clean metrics didn't map onto a
clean backend source**, a [[fixture-backend-product-mismatch]], surfaced as an
AskUserQuestion. The user chose **full honest compute**. The three metrics have
three different provenances in the reopen audit trail (`cases/signals.py`):
- `reopened_last_30d`: count of `REOPENED` Activities (one per reopen).
- `replies_after_window_30d`: the signal flags the `COMMENT` Activity
  `out_of_reopen_window=True` when a reply lands too late; counting that flag is
  the authoritative "reopened nothing" number, judged against the window in force
  at the time (a recompute here couldn't reproduce a since-changed window).
- `median_days_to_reply`: assembled from two stores because a reopen sets
  `closed_on = None`: reopened replies keep their delta only in the `REOPENED`
  metadata's `days_since_close`, while replies that didn't reopen leave their case
  Closed, so the delta is `commented_on − closed_on` (a hand-rolled join from the
  generic-relation `Comment` to `Case`). The median unions both; no double count,
  since a reopened case is no longer Closed and drops out of the comment side.

Every query is explicitly `org=`-scoped (RLS inert in dev/test). 5 new tests
(`TestReopenAnalytics`) drive the real signal path: a within-window reply
reopens, an out-of-window reply flags, the median unions a delta-2 reopen with a
delta-10 stale reply to 6, plus org isolation and an all-zero baseline; 19 reopen
tests pass. Browser-verified against a throwaway scenario (one reopened + one
out-of-window case) showing 1 / 1 / 6d and the "1 replies arrived too late"
banner; mobile 400px reflows. The throwaway cases, comments and activities were
deleted afterward (activities reference cases by `entity_id`, not FK, so they
don't cascade, deleted by hand).

## Escalation: /v2/settings/escalation (thirtieth module, fifth settings sub-page)

Read-only. One admin policy per priority; each card shows the two halves (missed
first response / missed resolution), what each does, and. The reason the page
exists, how many breaches went to a policy that does nothing. `getEscalationPolicies`
reads `GET /cases/escalation-policies/`. Authz was already right (`HasOrgContext`
on both views; POST/PUT/DELETE gate on `_is_admin`), no finding, like the three
sub-pages before it. Read-wide/write-narrow shared config: a member GETs 200,
POSTs 403.

The only computed add is per-policy `breaches_last_30d {first_response,
resolution}`, and it was the cluster's second fixture-backend fork. The clean
event source, `Activity(action="ESCALATED")` written by
`scan_for_breached_cases`, is the wrong source here: the scanner records an
activity *only when a policy acts* (active, with a target set), so a dead policy
(off, or reassign-to-nobody) leaves no event and would read as zero breaches.
That is exactly the population the page is built to expose ("breaches told
nobody"). So `_breach_counts_last_30d(org)` counts the breach **condition** on
the case instead: among cases opened in the last 30 days (a bounded, indexed
cohort), a first-response breach = the business-hours deadline passed and the
first response either never came or came after it; resolution likewise against
`resolved_at`. That definition also catches breached-then-resolved-late cases,
which the live `is_sla_*_breached` properties drop the moment a case is answered.
The org's business calendar is resolved once (not per case) and the small
`Case._sla_deadline` pause arithmetic is mirrored to avoid an N+1.

The SvelteKit server layer flattens the backend's nested `first_response_target`
/ `resolution_target` (full profile objects, name under `user_details.name`) and
`notify_team` (full team object) to the `{ id, name }` the card reads. Eight new
tests (`TestEscalationBreachCounts`) pin the behaviour: open-breached counts both
halves, resolved-late still counts (with a sanity assert that the live properties
call it *not* breached), on-time doesn't count, only-first-response, the 30-day
window excludes older cases, **a dead policy still reports its breaches** (and
fires zero ESCALATED activities), priorities don't bleed across policies, and
cross-org isolation; 27 escalation tests pass. Browser-verified against a
throwaway scenario (Urgent live 2/2, High with a dead resolution half 3/3, Low
policy off 1/1) → "5 breaches told nobody, 1 of 3 policies do nothing"; mobile
400px stacks the two halves. Seed policies, cases, activities and team deleted
afterward.

## Ticket routing: /v2/settings/routing (thirty-first module, sixth settings sub-page)

Read-only. Rules in evaluation order (the order IS the behaviour. The engine
takes the first match), each read as the sentence it performs. `getRoutingRules`
reads `GET /cases/routing-rules/`. Authz already right (`HasOrgContext` on all
views; POST/PUT/DELETE gate `_is_admin`), no finding. Member GETs 200, POSTs 403.

This was the fork test #58 taught us to run, and it came out the other way. The
page wants per-rule `matched_last_30d` and org `unrouted_last_30d`, and there IS
a clean event source: `cases.routing._apply` writes an `Activity(action="ROUTED")`
for every rule that fires on a case, *including* a rule that matched but had an
empty pool. So unlike escalation's ESCALATED log (which skips the dead policies
the page is about), the ROUTED log records the whole population, no fork.
`_routing_analytics(org)` reads that log once: `matched_last_30d` is a count per
`rule_id`; `unrouted_last_30d` is the window's cases (opened in 30d, `is_active`)
whose id appears in *no* ROUTED row. Literally "no rule matched", which is what
"nobody was assigned" means on the card. A rule that matched but could not assign
is deliberately NOT unrouted. The view also attaches each round-robin rule's
`RoutingRuleState.last_assigned_index` as `state`, so the page can name who is
next, and totals `count`/`active`.

The SvelteKit server layer flattens `target_assignees` (full profiles) to
`{ id, name, is_active }`: the page flags any assignee whose `is_active` is
false as "deactivated and still in the rotation", and `target_team` to
`{ id, name }`. Eight new tests (`TestRoutingAnalytics`) drive the real
create-signal path: a firing rule counts matched, an unmatched case is unrouted,
**a matched-but-empty-pool case is matched not unrouted** (the key distinction),
round-robin state surfaces (and direct rules have none), totals count/active, an
older-than-30d case drops out of unrouted, and cross-org isolation; 34 routing
tests pass. Browser-verified against a throwaway scenario (Urgent→lead 2 matched,
Incident→round-robin 2 matched with a deactivated agent flagged, 2 cases matching
nothing → Unrouted 2); mobile 400px turns the match count into a footnote. Seed
rules, cases, activities and throwaway profiles deleted afterward.

## Inbound email: /v2/settings/inbound-email (thirty-second module, seventh settings sub-page)

Read-only. The addresses that turn email into tickets, each with its defaults
and two metrics: `cases_last_30d` (tickets opened from the address in 30 days)
and `last_received_at`. Authz already right (`HasOrgContext`; POST/PUT/DELETE
gate `_is_admin`). Member GETs 200, POSTs 403.

This was the third fork, and unlike routing it did NOT resolve cleanly: nothing
durably recorded which mailbox a case or email arrived through. `EmailMessage`
had `org`/`case`/headers but no mailbox link; `Case` got only a transient
`_routing_mailbox_id`. The only stored signal was the To header (fuzzy: BCC /
alias / forwarding miss it). Surfaced as an AskUserQuestion (proxy vs exact); the
user chose **exact**. So a nullable `mailbox` FK was added to `EmailMessage`, set
at ingest in `_record_email_message` (the mailbox is already in scope there),
with a migration that backfills historical rows via the same To-header match,
so history is best-effort but all new mail is attributed exactly. `SET_NULL`
keeps the audit row if a mailbox is later deleted.

`_mailbox_analytics(org)` reads that FK: `cases_last_30d` per mailbox = distinct
cases *created* in the window (anchored on `case.created_at`, so a reply to an
old ticket is not a new one) with an inbound message through the mailbox;
`last_received_at` = newest inbound `received_at` (any message, dropped included,
the address still received mail); org `cases_last_30d` = distinct such cases
across mailboxes (not a sum, so no double count).

NO SECRETS reach the page. The backend still returns `webhook_secret` to admins
(the #52 fix strips it only for non-admins), but the SvelteKit server layer
rebuilds each row from a fixed allowlist that omits `webhook_secret` and the
`imap_*` columns, so the credential never lands in the browser payload for anyone:
verified by fetching the SSR HTML and asserting the seeded secret value, the
`webhook_secret` key, and `imap_password` are all absent while the mailbox data
is present. `default_assignee` (a full profile) is flattened to `{ id, name }`.

Six new tests (`TestMailboxAnalytics`) drive the real pipeline so the FK is set
the way production sets it: cases + last-received attributed per mailbox, counts
isolated between mailboxes, a threaded reply to an out-of-window case is not a new
ticket (but still updates last-received), a dropped auto-reply sets last-received
with no ticket, totals count/active, cross-org isolation; 45 inbound tests pass
(plus a pre-existing unused-variable lint fixed in passing). Browser-verified
against a throwaway scenario (support 2 / alerts 1 / a deactivated old address 1,
the last firing the "accepts mail and creates nothing" banner); mobile 400px
stacks. Seed mailboxes, cases, emails, activities and contacts deleted afterward.

## Approval rules: /v2/settings/ticket-approvals (thirty-third module, eighth settings sub-page)

Read-only. The rules that gate a ticket close and who can clear them. The queue
at /v2/tickets/approvals answers "what is waiting on me"; this answers "what will
be gated next, and by whom". `getApprovalRules` reads `GET /cases/approval-rules/`.
Authz already right (`HasOrgContext`; POST/PUT/DELETE gate `_is_admin`; the #53
cross-tenant PUT hole is closed). Member GETs 200, POSTs 403.

The backend metric was a clean compute; `pending_count` per rule = `Approval`
rows in state `pending` bound to it (the model indexes `(org, state)`), and
totals carry `active` + the org's total `pending`. The serializer returns
`approvers` as `[{id, email}]`; the page renders them as a sentence, so the
server layer flattens to the email strings. `match_team` already arrives as
`{ id, name }`.

**The finding was on the page, not the backend: a stale security warning.** The
page carried a prominent banner; "an approval can be granted by the person who
asked for it", asserting that `ApprovalApproveView` never compares requester to
approver, on every ADMIN-role rule with no named approvers. That gap was real
when the page was written but was **closed in #50**: the view now returns 403
when `approval.requested_by_id == request.profile.id`, unconditionally (no admin
exception), and the inbox already excludes self-requested rows. Leaving the
warning would tell admins a separation-of-duties hole exists when the API
enforces exactly that. So the `selfClearable` derivation, its per-rule flag, and
the bottom banner were removed, and the file's doc-comment rewritten to record
that the view closed it. The genuinely-still-true trap, approver_role MANAGER
with no named approvers, which matches nobody because `Profile.role` has no
MANAGER, is kept ("Nobody can clear").

Four new tests (`TestApprovalRuleAnalytics`) cover pending_count per rule, only
`pending` state counting (approved/rejected/cancelled excluded), totals
count/active, and cross-org isolation; 31 approval tests pass. Browser-verified
against a throwaway scenario (named-approver 1 waiting / MANAGER trap flagged
"Nobody can clear" / any-admin 2 waiting), and confirmed the removed
self-approval banner does not render for the any-admin no-approvers rule that
would previously have triggered it. Mobile 400px reflows. Seed rules, approvals,
cases and activities deleted afterward.

## Business hours: /v2/settings/business-hours (thirty-fourth module, ninth settings sub-page)

Read-only. The calendar every SLA target is measured against. The reason
"answered in 4h" means anything. `getBusinessHours` reads
`GET /business-hours/calendar/` (the org's default, created on first read).

No analytics: this was a **reshape**. The backend stores the week as fourteen
flat TimeFields (`monday_open`/`monday_close` … `sunday_open`/`sunday_close`, a
null pair = "closed") serialized as "HH:MM:SS"; the page reads an ordered
`days: [{ day, open, close }]` Monday-first with "HH:MM" times, so the server
layer folds the flat fields into that array and trims the seconds. Holidays
already arrive as `{ id, date, name }`. "Edit hours" / "Add holiday" are deferred
builders (the backend has admin PUT/POST/DELETE, but no form is wired), so this
is reads-only like the rest of the cluster.

The finding was the cluster's recurring one: all three `business_hours` views ran
`permission_classes = (IsAuthenticated,)`: **missing `HasOrgContext`** (like
macros/tags). Org-scoping was present everywhere (`filter(org=request.profile.org)`),
so no cross-tenant leak, but an authenticated-but-org-less token would reach
`request.profile.org` on a null profile and 500 instead of getting a clean 403,
and it deviated from the documented standard. Added `HasOrgContext` to all three.

One new test (`TestOrgContextRequired`) proves the fix behaviorally: a
force-authenticated request with no `request.profile` (the direct-call path skips
the middleware) now returns 403, not 500. Verified it fails (500) with the guard
removed and passes (403) with it, so it is not a tautology. The existing 17 tests
(authed GET/PUT/holiday CRUD, cross-org 404, validation) all still pass; 18 total.
Browser-verified against a throwaway America/New_York calendar (Mon-Thu 09:00-17:30,
Fri 09:00-17:00, weekend Closed → 42 hours a week, Thursday marked "today", two
holidays with relative dates); mobile 400px stacks the split. Seed calendar +
holidays deleted afterward (org had none, restoring the pristine state).

## Settings hub: /v2/settings (thirty-fifth module, closes the settings cluster)

The settings landing: it summarises every destination with the current value
beside it and a warning where something needs attention. The last page of the
cluster, and the one that lets the other nine drop their fixtures.

**Fan-out, not a summary endpoint.** The hub needs a number from each
destination. `$lib/server/v2/settings.js::getSettingsHub` calls the same server
layers each sub-page uses, concurrently, and assembles their totals, rather
than a bespoke `/settings/summary/` that would have to reproduce, in Python
across eight apps, the per-destination rollups the pages derive. Two of those
rollups the endpoints don't return and are computed here from the arrays they
do: escalation's `breaches_unhandled_30d` (counted exactly as the escalation
page's `breachesGoingNowhere`, so the hub's warning matches the page it links
to) and inbound's `silently_dropping`. Added `/v2/settings` to `MIGRATED_EXACT`,
never as a prefix. The sub-pages carry their own prefixes, and a prefix here
would re-cover them.

**Graceful degrade for members.** Three destinations are admin-only: people
(`/users/`), API tokens (`/org/tokens/`) and the reopen policy
(`/cases/reopen-policy/`) all 403 a member. `listTeam`/`listOrgTokens` fold that
into a `forbidden` sentinel; the reopen fetch throws, so it is caught to null
(the first cut 500'd the member hub. The reopen GET being admin-only, unlike
the other read-wide settings, was the missed case). Each of the three rows then
renders for a member with the destination but no value it is not allowed to
compute. The same choice the shell makes omitting the team badge a member
cannot count. The other nine destinations are member-readable. Browser-verified
both roles: admin sees "4 people · 1 admins", "1 live" (⚠), "Within 7 days";
member sees those three rows valueless and no warning count, every other row
populated, no 500. Desktop + mobile 400px.

**Closing the cluster.** Each sub-page (#54-#62) had kept its `api.js` facade and
`mock/*` fixture *because this hub fanned out to them*. With the hub on the real
layers, the eleven hub facades were deleted from `api.js` (listPeople,
getOrgSettings, listApiTokens, listRoutingRules, listEscalationPolicies,
getReopenPolicy, listMailboxes, listMacros, listTags, listCustomFields,
listApprovalRules), their now-orphaned fixture imports removed, and the three
fixture files left with zero importers deleted (`mock/team.js`, `mock/handling.js`,
`mock/vocabulary.js`), plus two pre-existing unused imports tidied in passing.
svelte-check 0 errors, eslint clean on `api.js`. No backend change; nothing
committed. This is the last page of the nine-page settings cluster (#52-#63).

---

## The customer portal: v2 becomes the real thing (2026-07-31)

The public pages a *customer* sees: the invoice and estimate links emailed to
them, and the CSAT survey, were the last fixtures. The v2 versions under
`v2/(public)/*` turned out to be design **previews** behind the `/v2` auth
guard; the actual customer portal already existed at
`(no-layout)/portal/{estimate,invoice}/[token]` and `(no-layout)/csat/[token]`
(v1 design, real `/api/public/` endpoints. The emailed link is `/portal/...`,
from `Estimate.public_url`). The decision was to **make the v2 redesign the real
portal** and retire the v1 design, rather than wire throwaway previews.

That is mostly a security story, because the portal did not actually work end to
end. Three layers each denied the anonymous customer, and all three are fixed:

1. **DRF middleware** exempt list (already fixed 2026-07-28).
2. **RLS.** An anonymous request runs with `app.current_org` empty, so under a
   correctly-configured non-superuser role the isolation policy hid the invoice
   / estimate / survey row, a 404 for every customer, masked in dev by the
   superuser DB user. Fixed with the `docs/PORTAL_RLS.md` **Option 1**: an
   unscoped `PortalAccessToken` table mapping `sha256(url_token) → org`
   (absent from `ORG_SCOPED_TABLES`, no policy), registered when each token is
   minted and backfilled once. The view resolves the org from the token, calls
   `set_rls_context`, then reads the row under full RLS. Proven under the real
   `crm_user`: empty context hides the row, the lookup resolves the org, the
   resolved context reveals it, a bogus context still returns zero.
3. **The SvelteKit guard.** `hooks.server.js` `PUBLIC_ROUTES` listed only
   `/login`, `/logout`, `/bounce`, so it redirected every anonymous visitor to
   `/portal/*` and `/csat/*` to `/login`. Added `/portal` and `/csat`. They
   read nothing but token-scoped public endpoints.

**The accept endpoint** also stopped taking a quote on trust. Accepting an
estimate now enforces `expiry_date` server-side and **requires the acceptor's
name and email**, recorded with the request IP and user-agent on new `Estimate`
fields, acceptance authorises an invoice, and the record of who gave it is now
more than "whoever held the link". The real portal collects name+email in the
two-step confirm and forwards the customer's real IP/UA to Django.

**The port.** A new `PortalShell` component loads `v2.css` and roots the page
under `.v2-root` (the `--v2-*` tokens are scoped there), so the three
`(no-layout)` pages render the v2 design with no app chrome. The loaders pass
the Django snake_case shape straight through, and use the **absolute** API URL
(the estimate/invoice loaders previously used a relative `/api/...` that only
resolves behind a production reverse proxy). The public serializers gained the
discount/tax breakdown fields the line-items table shows, and CSAT gained
`case_closed_on`. The redundant `v2/(public)` preview routes, `mock/portal.js`,
and the three api.js fixture functions were deleted.

Browser-verified all three as an anonymous visitor: estimate (expired state and
the full accept flow, with the acceptor identity/IP/UA landing in the DB),
invoice (overdue in ember, responsive at 400px), and CSAT (star rating submitted
through the real endpoint). svelte-check and eslint clean; nothing committed.
