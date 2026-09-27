# Cases

Routes in `backend/cases/urls.py`. CRUD and comments/attachments live in `backend/cases/views.py`;
CSV import in `backend/cases/import_views.py` / `backend/cases/services/csv_import.py`; approvals in
`backend/cases/approval_views.py` and `backend/cases/approvals.py`; the knowledge base in
`backend/cases/solution_views.py`. Object-level authorization for the case itself is centralized in
`backend/cases/access.py`; for knowledge-base articles, `backend/cases/kb_access.py`. See
[Conventions](conventions.md) for pagination, filtering and response-shape rules assumed rather than
repeated here, and [Errors](errors.md) for the 404-vs-403 principle referenced below.

Cases are also the largest module in this codebase: Kanban pipelines, merge/unmerge, parent/child
trees, time tracking, escalation, routing, inbound email and analytics all live under
`/api/cases/...` too. This page covers only the endpoints this documentation task scopes: list,
create, detail, comments/attachments, approvals, the knowledge base, and CSV import.

## A case's read, write and delete rules differ on purpose

Before anything else: **do not collapse a case's access rules into one check.** `cases/access.py`
states three explicit, different rules, and its own module docstring explains why, before this
module existed the three verbs had drifted into three different answers by accident, and a watcher's
ticket showed up in their list but answered `403` when they opened it:

```
read    admin · creator · assignee · watcher
write   admin · creator · assignee
delete  admin · creator
```

(`cases/access.py:20-22`.) This is the same "read wide, write narrow" principle
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#read-wide-write-narrow)
documents in general. That page is the place to read the reasoning; this page just applies it.
Every endpoint under [Retrieve, update, delete](#retrieve-update-delete) and
[Comments and attachments](#comments-and-attachments) calls one of
`assert_case_read_access`/`assert_case_write_access`/`assert_case_delete_access`
(`cases/access.py:90-113`), and the admin check itself is the two-spellings-of-admin form:
`profile.role == "ADMIN" or bool(getattr(profile, "is_admin", False))` (`cases/access.py:38-40`).
That recurs throughout this codebase. The [Approvals](#approvals) endpoints use `cases.access` too,
with one deliberate widening for the inbox; see that section.

## List cases

`GET /api/cases/` (`CaseListView.get`, `cases/views.py:310-312`) returns one paginated list, ordered
`-created_at, -id` (`case_list_queryset`, `:188-193`): a comment on the queryset explains the tiebreak exists because `-id`
alone is a random UUID and was, in practice, no ordering at all. Soft-deleted cases
(`is_active=False`) are excluded by default; an org admin can pass `?include_deleted=true` to see them
(`:194-198`). Merged duplicates (`merged_into` set, or `status="Duplicate"`) are excluded by default
too; `?show_merged=true` includes them (`:199-202`). A non-admin caller sees only cases where they are
creator, assignee, or watcher, the same `visible_cases_qs` the detail view's read check uses
(`:203-209`), so list and detail agree by construction.

```json
{
  "cases_count": 42,
  "offset": 10,
  "cases": [ { "...": "CaseSerializer" } ],
  "status": [["New", "New"], ["Assigned", "Assigned"], ["Pending", "Pending"], ["Closed", "Closed"], ["Rejected", "Rejected"], ["Duplicate", "Duplicate"]],
  "priority": [["Low", "Low"], ["Normal", "Normal"], ["High", "High"], ["Urgent", "Urgent"]],
  "type_of_case": [["Question", "Question"], ["Incident", "Incident"], ["Problem", "Problem"]],
  "open_count": 12,
  "urgent_count": 2,
  "awaiting_first_reply": 5,
  "accounts_list": [ { "...": "AccountSerializer" } ],
  "contacts_list": [ { "...": "ContactSerializer" } ],
  "users": [{"id": "...", "user__email": "..."}]
}
```

(`cases/views.py:217-288`.) `open_count`, `urgent_count` and `awaiting_first_reply` are counted over
the whole filtered queryset, not the page. `awaiting_first_reply` counts open cases
(`status` in `New`/`Assigned`/`Pending`) with no `first_response_at` yet. It is not the same thing as
an SLA breach, which depends on the org's business calendar and is computed per-row rather than in
this aggregate. `accounts_list`/`contacts_list` feed the create form's pickers, org-wide for an admin,
but narrowed for a non-admin by the accounts and contacts read rules (`visible_accounts_qs`,
`visible_contacts_qs`, `:224-227`), the same rules the save path accepts; either way they can be
heavy, and `?slim=true` omits both (`:279-281`).

Filters read directly from `request.query_params` (`apply_case_list_filters`, `cases/views.py:97-177`,
shared with `GET /api/cases/watching/`): `name` (contains), `status` (repeatable. A list applies
`status__in`, a single value applies exact match), `priority` (exact), `account` (exact id),
`case_type` (exact), `assigned_to` (repeatable id list), `tags` (id list), `search` (`name` or
`description`, contains), `created_at__gte`/`created_at__lte` (date range), `sla_breached=true`
(a wall-clock approximation of the SLA badge, Postgres-only raw SQL), `cf_<key>` (custom field equals),
and `ordering`: whitelisted to `created_at`, `-created_at`, `priority`, `-priority`, `id`, `-id`,
`name`, `-name` (`:83-94,172-175`); anything else is silently ignored rather than erroring.

## Create a case

`POST /api/cases/` (`CaseListView.post`, `cases/views.py:331-420`) accepts `multipart/form-data` (for
an optional `case_attachment` file) or JSON, validated by `CaseCreateSerializer`
(`cases/serializer.py:284-458`; see [Fields](#fields)). On success (`200`):

```json
{
  "error": false,
  "message": "Case Created Successfully",
  "id": "<uuid>",
  "cases_obj": { "...": "CaseSerializer" }
}
```

A validation failure returns `400` with `{"error": true, "errors": {...}}`. Two validators worth
knowing:

- **`account`, if given, must belong to the caller's org**: `validate_account` rejects a
  cross-tenant id with `"No such account."` (`cases/serializer.py:304-330`); the comment on it notes
  this closes both a cross-tenant *write* and a cross-tenant *read*, since the create response nests
  the account back through `CaseSerializer` → `AccountSerializer`.
- **`name` must be unique per org, case-insensitive** (`validate_name`, `:399-413`).

`contacts`, `teams`, `assigned_to` and `tags` are **not** part of `CaseCreateSerializer`. The view
reads each from the request body after the serializer succeeds, resolved against
`org=request.profile.org` (`cases/views.py:351-389`). Unlike [Leads](leads.md#fields), which documents a real
per-field, per-verb inconsistency in how these four are *parsed*, cases parses all four identically on
every verb: each is `json.loads`'d when it arrives as a string (which happens whenever the same
request also uploads `case_attachment` as multipart) before the id list is extracted. `case_attachment`
itself is read straight off `request.FILES` (`cases/views.py:391-396`): on `POST` only; see
[Retrieve, update, delete](#retrieve-update-delete) for why `PATCH` can't set it at all, and for how
*writing* these four fields (not just parsing them) differs sharply across verbs.

## Retrieve, update, delete

`GET /api/cases/{id}/` (`CaseDetailView.get`, `cases/views.py:579-747`) looks the case up through the
read rule first (`get_case_or_404`, `:584`: a malformed UUID, a case in another org and a same-org
case the caller may not open all answer the same `404`, never `500` or `403`, matching
[Errors](errors.md#not-found-versus-forbidden)). If the
case has been merged into another one, the response is a redirect hint instead of the case itself:
`{"redirect_to": "<uuid>", "merged_into": "<uuid>", "source_case_id": "<uuid>", "source_case_name": "..."}`,
unless the caller passes `?show_merged=true` (`:589-601`). The redirect answers only after the read
check, so `source_case_name` never reaches a caller who could not open the duplicate; it used to
answer first and disclosed the name to any org member holding the id. The response nests
the record under `cases_obj`, alongside `attachments`, `comments` (public only), `internal_notes`,
`contacts`, `solutions`, `activities` (last 20), `email_messages` (last 50, inbound-email threads),
`merged_from_cases`, `custom_field_definitions`, `status`, `priority`, `type_of_case`,
`comment_permission`, `can_merge`, `approval_rule` and `users_mention`. `approval_rule` is
`{"id", "name"}` of the rule that gates closing this case (`find_matching_rule(case, "pre_close")`,
the rule a `request-approval/` with no `rule_id` binds to), or `null` when none does; clients offer
"Request approval" only when it is set. `comment_permission` is computed with the same
`has_case_write_access` the comment-post endpoint enforces (`:612`), so the button a client shows and
the answer the server gives agree by construction, the comment on this line notes that used to not be
true (`comment_permission` was creator-or-admin while the write endpoint below also allowed assignees).

`PUT /api/cases/{id}/` (`cases/views.py:452-536`) and `PATCH /api/cases/{id}/` (`:842-860`) both call
`assert_case_write_access` before touching anything. **`account` cannot be changed by either verb.**
`CaseCreateSerializer.__init__` sets `self.fields["account"].read_only = True` whenever `self.instance`
is set (`cases/serializer.py:293-295`) (true for both update calls, never for create), so on `PUT`/
`PATCH` an `account` in the body is silently dropped before validation ever runs; `validate_account`
never executes and no error is returned. `PUT` is a non-partial serializer instantiation, so it still
requires `name`/`status`/`priority` the same as `POST` does; `PATCH` passes `partial=True`
(in `update_case`), so none of the three are required there. A `PATCH` may touch just one field. From
django-crm 1.13.0 the `PATCH` write is `update_case` in `cases/updates.py`, which a macro's actions
([Macros](macros.md#apply)) and the bulk edit also call, so all three run the same validation, the
same org and active filters on assignees and tags, and the same email to anyone newly assigned.

Closing a case is validated as a *transition*, not just a target value. `CaseCreateSerializer.validate()`
(`cases/serializer.py:332-397`) judges only a move into `"Closed"` from any other status, so an
already-closed case can still be edited. Two things happen on that move:

- **The close is dated by the server.** A close that sends no `closed_on` (absent or `null`) is dated
  today in the org's timezone (`Org.timezone`, which the middleware activates for the request), by
  `closing_date` in `cases/approvals.py`. A `closed_on` the client sends always wins, and a malformed
  one is a `400` against `closed_on` with nothing written. Clients should not compute "today"
  themselves: from django-crm 1.13.0 neither the web app nor the phone sends a date for a plain close,
  and an org stored under a legacy zone name such as `US/Eastern` is dated like any other. Leaving
  `"Closed"` clears `closed_on` (and `resolved_at`), so a reopened case that is closed again is dated
  afresh rather than keeping the old date.
- **A Closed case always has a date.** From django-crm 1.13.0 this holds for every write whose
  resulting status is `"Closed"`, not only the move into it. An absent or `null` `closed_on` on a case
  that is already Closed keeps the stored date (so `PATCH {"closed_on": null}` is a no-op there rather
  than leaving it undated), and only when there is no stored date is it dated today in the org's
  timezone. The same rule runs on `PUT`, `PATCH`, bulk update, macro apply, the board move and
  close-with-children, and the `Case` pre-save signal applies it to writers that skip the API
  serializer (CSV import rows with `status` `Closed` and no `closed_on`, vertical-pack sample
  tickets).
- **The approval gate runs.** When an active `pre_close` `ApprovalRule` matches the case's
  priority/case_type/team (the incoming values, so a request cannot re-target the case out of the
  rule and close it at once), an `Approval` row in state `approved` for that case and rule is
  required, or the answer is `400` with
  `{"errors": {"status": ["An approval is required before this case can be closed (rule: <name>)."]}}`
  and nothing is written.

The same dating rule applies on every path that closes a ticket: `POST` (a case created as
`"Closed"`), `PUT`, `PATCH`, the board move (`PATCH /api/cases/{id}/move/`), the bulk edit
(`POST /api/cases/bulk/update/`), a macro's actions, and `close-with-children`. The approval gate runs
on all of them except `POST`, since an approval can only be recorded against a case that already
exists; the bulk edit reports a gated ticket as `approval_required` and carries on with the rest.
The gate exists because `Case.clean()` (`cases/models.py:193-253`)
states the rules but nothing on the save path calls `full_clean()` (`Case` defines no `save()` of its
own; it inherits `BaseModel.save()`), so without the serializer-level check a matching rule could be
armed and a `PATCH {"status": "Closed"}` would still return `200` and record zero approvals. `PUT`
and `PATCH` answer success with the identical literal string `{"error": false, "message": "Case
Updated Successfully"}`.

**Parent links.** `Case.clean()` (`cases/models.py:193-253`) states four rules about `parent`: a case
cannot be its own parent, linking cannot create a cycle, the tree is capped at
`Case.PARENT_MAX_DEPTH = 3` levels (`:191`), and nothing is linked to or from a merged (`Duplicate`)
case. Since `Case.clean()` never runs on the API path, all four are enforced by
`check_parent_link` in `cases/parent_guards.py`, which both `CaseCreateSerializer.validate()` (when
the request carries `parent`) and the link endpoint call. The depth check counts the subtree the case
brings with it. `validate_parent()` (`cases/serializer.py:415-440`) additionally refuses a parent in
another org or one the caller may not open, with the same message as an unknown id.

`DELETE /api/cases/{id}/` (`cases/views.py:552-559`) calls `assert_case_delete_access`, admin or creator only, no
assignee exception (see [above](#a-cases-read-write-and-delete-rules-differ-on-purpose)).

## Comments and attachments

Adding a comment or an attachment is the same `POST /api/cases/{id}/` route the detail endpoint uses,
just with a body (`CaseDetailView.post`, `cases/views.py:765-825`), gated by
`assert_case_write_access` (`:771`). The same rule `comment_permission` on `GET` reports. Send
`comment` (text, and optionally `is_internal`, a truthy string or boolean) and/or `case_attachment`
(multipart file), either or both. A comment created with `is_internal=true` is an internal note, not
visible to a customer-facing surface; the response splits both back out as `comments` (public) and
`internal_notes` (`:817-822`), and both are returned to any caller with read access; `is_internal` is
a display split, not an access-control split on this endpoint.

Editing or deleting an *existing* comment goes through `PUT`/`PATCH`/`DELETE /api/cases/comment/{id}/`
(`CaseCommentView`, `cases/views.py:862-994`). Restricted to an org admin (`is_org_admin`: the
`ADMIN` role, and a superuser counts), or the comment's own author
(`request.profile == obj.commented_by`, `:900`, `:946`, `:981`).
`Comment.commented_by` is a `Profile` foreign key (`common/models.py:414-416`), so this comparison is
correct, unlike some `created_by` comparisons elsewhere in this codebase; see
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks).

`object_id`, `org`, `content_type`, `commented_by` and `commented_by_contact` are all
`read_only_fields` on `CommentSerializer` (`common/serializer.py:298-304`). Which record a comment
hangs off, which org owns it, and who wrote it are the server's to decide, and leaving the first two
writable had two consequences that are worth recording because both are now closed:

- A comment's own author could `PATCH` `object_id` and `org` to repoint their comment at any record
  in the org by id, including a case the access rules would refuse them, or restamp it into another
  org. `Comment.clean()` compares the new `org` against the new target's `org`, so a *matching*
  cross-org pair passed that check and saved. Only the RLS policy stopped it, and RLS is the safety
  net rather than the contract.
- `PUT` could not edit a comment at all. `.put()` builds a non-partial serializer, which requires
  every field that is not read-only, so an ordinary `{"comment": "..."}` body failed validation on
  the missing `object_id`/`org` and answered `400`. Four PUT handlers shared that defect.

A customer's reply written through the [customer portal](customer-portal.md) arrives in `comments`
like any other public comment, with two differences: `commented_by` is `null`, because a customer
has no `Profile`, and `commented_by_contact` names them instead. That null is the same signal an
inbound email reply carries, and it is what `_evaluate_reopen` and the first-response SLA stamp both
test to tell a customer's message from a colleague's.

`DELETE /api/cases/attachment/{id}/` (`CaseAttachmentView.delete`, `cases/views.py:1013-1055`) finds
the attachment only on a ticket the caller may open in their org (`get_on_visible_record_or_404`,
`:1036-1038`), so an attachment on a hidden ticket or on another module's record answers the same
`404` as a missing id, and its ownership check compares `request.profile.user_id` to
`self.object.created_by_id`, both `User` ids, correctly typed (`:1040-1043`). The view's own docstring documents two defects that were live in this exact endpoint
before the fix (unscoped lookup; a `Profile`-to-`User` comparison that made the endpoint silently
admin-only). Both are fixed here today. Whether the equivalent endpoint in another app still has
either defect is that app's own question; verify against that app's current source rather than this
docstring, which itself goes stale. See
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks)
for the current, cross-app answer.

## Approvals

Two kinds of endpoint: admin-configured rules, and per-case requests/decisions against them
(`cases/approval_views.py`). A rule (`ApprovalRule`, `cases/approvals.py:47-116`) matches a case on
`trigger_event` (only `"pre_close"` exists today), and optionally `priority`, `case_type` and `team`.
The most-specific active match wins ties by most recent (`find_matching_rule`, `:119-136`).

- `GET /api/cases/approval-rules/` (`ApprovalRuleListCreateView.get`, `approval_views.py:87-103`).
  Any org member; each rule row carries a `pending_count` computed from the `Approval` log.
- `POST /api/cases/approval-rules/` (`:105-133`), admin only. The approver list and match-team are
  each validated to belong to the caller's org before save (`:117-129`).
- `GET/PUT/DELETE /api/cases/approval-rules/{id}/` (`ApprovalRuleDetailView`, `:136-194`); `GET` is
  open to any member; `PUT`/`DELETE` are admin only. Deleting a rule with request history soft-disables
  it (`is_active=False`) instead of a hard delete (`:183-192`), since `Approval.rule` is
  `on_delete=PROTECT`.
- `POST /api/cases/{id}/request-approval/` (`CaseRequestApprovalView.post`). A case the caller may
  not read answers `404` (`get_case_or_404`, the same body as a missing id), and filing needs the
  case's write rule (`assert_case_write_access`, the rule `comment_permission` reports), so a reader
  who may not reply is refused with `403`. The request binds to an explicit `rule_id` (which must be
  active and match the case) or, when omitted, the `find_matching_rule` result, which is what the
  detail's `approval_rule` names; no matching rule is a `400`. A second request against the same
  case+rule while one is `pending` is refused with `409`, not a duplicate row. The `201` response is
  `ApprovalSerializer(approval).data`.
- `GET /api/cases/approvals/` (`ApprovalInboxView.get`), the inbox. A row is listed when the caller
  is an org admin, may read the case, is in the rule's approver pool, or filed the request
  (`_visible_approvals`): an approver routinely has no other stake in the case, and deciding it is the
  point of the queue. `?state=` filters (default `pending`; `all` drops the filter), `?case=<id>`
  scopes to one case (the ticket page's panel on both clients), and `?mine=true` restricts to rows the
  caller can currently act on, which deliberately excludes the caller's own requests, "mine to
  decide" rather than "mine to have filed".

Approve, reject and cancel find the approval through the same visibility rule as the inbox. One the
caller cannot see answers `404` with the body a missing id gets, so these endpoints cannot confirm
that a hidden approval exists; the rules below apply only to approvals the caller can see.

**Approving and rejecting both enforce that the requester cannot be the approver, with no admin
exception.** `ApprovalApproveView.post` (`:354-407`) and `ApprovalRejectView.post` (`:410-475`) both
check, in order: the approval is still `pending` (else `400`); the caller is in the rule's approver
pool via `can_be_acted_on_by` (else `403`); and, separately,
`approval.requested_by_id == request.profile.id` (else `403`, "You cannot approve/reject your own
request; another approver must decide it.", `:382-390` and `:443-451`). The comment on both is explicit
that this holds even for "an admin who defaults into every rule's pool", being an approver and being
the requester are mutually exclusive for a single decision, regardless of role.
`Approval.can_be_acted_on_by` (`cases/approvals.py:188-196`) itself is: explicitly listed in
`rule.approvers`, **or** `profile.role == rule.approver_role`, note this second clause checks only
`role`, not the `is_organization_admin` flag `is_org_admin` elsewhere in this codebase also treats as
admin, so a profile that is only an admin via that flag is not automatically an approver unless
explicitly added to `rule.approvers`.

`ApprovalSerializer.can_act` and `.is_own_request` (`cases/serializer.py:1359-1384`) mirror the approve
endpoint's checks exactly, so a client rendering the inbox from the list response gets the same answer
the action endpoints would give. Both need `context={"request": request}` to resolve; without it they
default to `False`, which the comment on the serializer notes is the safe default rather than a bug.

`POST /api/cases/approvals/{id}/cancel/` (`ApprovalCancelView.post`, `:478-518`) is a **different**
rule from approve/reject: only the original requester or an admin may cancel (`:498-505`). There is no
"not the requester" restriction here, because cancelling your own request is the ordinary case.

## Solutions

The knowledge base (`cases/solution_views.py`, `cases/kb_access.py`). Read access is org-wide by
design. Every member may read every article, "a knowledge base whose articles are hidden from the
agents answering the tickets is not a knowledge base" (`cases/kb_access.py` module docstring), but
write and release are two different, narrower rules:

```
write     author · admin      title, body, and moving draft ↔ reviewed
release   admin               status → approved, and publish / unpublish
delete    author · admin
```

`release` is deliberately narrower than `write`: the review workflow (`draft → reviewed → approved`,
plus a separate `is_published` flag) only means something if the person who approves an article is not
its own author. `assert_solution_release_access` takes no article argument at all. Being the author is
the one thing that must not grant it (`cases/kb_access.py:76-86`).

`GET /api/cases/solutions/` (`SolutionListView.get`, `cases/solution_views.py:120-180`) is the one
endpoint in this API that calls DRF's `get_paginated_response()` and returns its standard
`count`/`next`/`previous`/`results` envelope, [Conventions](conventions.md#pagination) names this
exact endpoint as the sole example. It adds one extra top-level key, `totals`
(`count`/`published`/`draft`/`reviewed`/`approved_unpublished`), computed over the *whole* knowledge
base rather than the current filter, so the four status cards stay meaningful under a `?status=`
filter (`:165-177`). Filters: `?status=`, `?is_published=` (accepts `true`/`1`/`yes`/`on`, not just the
literal string `"true"`), `?search=` (title or description, contains), and `?tags=` (comma-separated
tag ids, matching any of them, `distinct()` so an article carrying two of them is not returned twice).

`POST /api/cases/solutions/` (`:186-208`) is open to any member for an ordinary draft, but is gated by
`assert_solution_release_access` the moment the payload would create an already-`approved` or
already-`is_published` article in one request (`_wants_release`, `:32-50`): without this, an author
could write and publish an article in a single `POST`, bypassing the review workflow entirely.

`GET/PUT/PATCH/DELETE /api/cases/solutions/{id}/` (`SolutionDetailView`, `:210-278`): `GET` is open to
any member; `PUT`/`PATCH` require `assert_solution_write_access` (author or admin) plus, again,
`assert_solution_release_access` if the request would move the article to `approved` or flip
`is_published`; `DELETE` requires the same write rule (not a separate, narrower one. Deleting your own
draft is ordinary tidying).

**Tags.** Articles carry the same org-scoped `Tags` as leads, deals and tickets. They are read on
the list and detail payloads as `tags` and written by passing `tags` (a list of tag ids) to `POST`
or `PATCH`. The write is handled in the view rather than on the serializer, so the org filter is
unavoidable: ids are resolved against `Tags.objects.filter(org=…, is_active=True)`, and an id
belonging to another org is not found rather than attached.

Absent means unchanged, and only an explicit `"tags": []` clears them. This differs from the case
endpoints on purpose, where the tag set is cleared and rebuilt whenever the key is handled, so a
`PATCH` that never mentions tags drops them.

Tag names are **agent-facing only**. They drive `related` on the portal article endpoint but never
appear in a portal payload, because the vocabulary is shared with deals and reads like "At Risk"
and "VIP". See [Customer portal](customer-portal.md#help-articles).

**Publishing reaches customers.** An article that is both `approved` and `is_published` is readable
by any signed-in portal contact in the org and is suggested to them while they file a request, as
well as feeding the agent suggester. Unpublishing withdraws both.

`POST /api/cases/solutions/{id}/publish/` and `POST /api/cases/solutions/{id}/unpublish/`
(`SolutionPublishView`/`SolutionUnpublishView`, `:281-336`) both require
`assert_solution_release_access`. Publishing additionally requires `status == "approved"` (`400`
otherwise); the comment on the unpublish view is explicit that pulling a bad answer down is the *same*
switch as giving it to customers and therefore gated the same way, even though the urgency argument
runs the other direction.

Reading which solutions are linked to a case is not a separate endpoint. The linked set is returned
as part of `GET /api/cases/{id}/` (`solutions` key, [above](#retrieve-update-delete)). Linking is
`POST /api/cases/{id}/solutions/` and unlinking is `DELETE /api/cases/{id}/solutions/{solution_id}/`
(`CaseSolutionLinkView`, `cases/views.py:1058-1134`), gated by `assert_case_write_access` on the
*case*: the comment on `.post` notes this closes a read-around: before this check, a member refused a
case with `403` could still link an article to it and then read the case's name, description, account
and contacts back out through that article's own `linked_cases`. The read side of the same gap is
closed in `cases/solution_serializers.py`'s `SolutionDetailSerializer.get_linked_cases`, which filters
the cases it returns through the same `visible_cases_qs` the case list and detail views use, rather
than returning every case the article happens to be linked to.

## CSV import

Two endpoints, gated to org admins or members with `has_sales_access` (`_can_import`,
`cases/import_views.py:23-31`). Anyone else gets `403`.

`POST /api/cases/import/preview/` (`CaseImportPreviewView`, `import_views.py:56-88`) reads a
`multipart/form-data` upload in a field named `file` (`.csv` extension required). The 5 MB cap is
checked against `upload.size`, Django's reported size for the uploaded file, available before
`.read()` is called, **not** the bytes actually read, and the check is skipped entirely when
`upload.size` is falsy (`if upload.size and upload.size > MAX_UPLOAD_BYTES`, `import_views.py:48-51`).
It then validates every row without writing anything. Row numbers are 1-based over *data* rows, not
file lines. The header itself is never counted, so the first row of data is `"row": 1`
(`enumerate(data_rows, start=1)` over `rows[1:]`, `services/csv_import.py:216-217`):

```json
{
  "header_error": null,
  "valid": [
    {
      "row": 1, "name": "Cannot log in", "status": "New", "priority": "High",
      "description": "", "case_type": "Incident", "closed_on": null,
      "account_id": "<uuid or null>", "contact_ids": [], "assigned_ids": [],
      "team_ids": [], "tag_names": []
    }
  ],
  "errors": [{"row": 2, "field": "status", "message": "Status must be one of: New, Assigned, Pending, Closed, Rejected, Duplicate"}],
  "summary": {"total": 2, "valid": 1, "invalid": 1}
}
```

`POST /api/cases/import/commit/` (`CaseImportCommitView`, `:91-125`) re-validates the same file and
writes every valid row inside one transaction, **if any row fails validation, nothing is written**
(`services/csv_import.py` `commit_rows`, "Don't write anything if any row failed; users must fix the
file first"). On success (`200`): `{"error": false, "created": 12, "ids": ["<uuid>", "..."]}`.

Required headers: `name`, `status`, `priority`. Recognized optional headers: `description`,
`case_type`, `account_name`, `contact_emails`, `assigned_emails`, `team_names`, `tags`, `closed_on`
(`REQUIRED_HEADERS`/`OPTIONAL_HEADERS`, `services/csv_import.py:36-46`), an unknown header, or a
missing required one, fails the whole file with `header_error` before any row is checked.
`contact_emails`, `assigned_emails` and `team_names` accept `;`-separated multiple values per cell and
must resolve to an existing contact/active member/team in the caller's org, or the row errors
(`:396-438`). `status`/`priority`/`case_type` are matched case-insensitively against the same choice
sets [Fields](#fields) documents for the API proper. A case name must be unique per org, checked
against both existing cases and other rows earlier in the same file (`:338-355`). The file is capped
at 5,000 data rows (`MAX_ROWS`, `:49`).

## Fields

`CaseCreateSerializer.Meta.fields` (`cases/serializer.py:442-458`) is what `POST /api/cases/`,
`PUT /api/cases/{id}/` and `PATCH /api/cases/{id}/` accept. "Required" below means required on
`POST` and on `PUT` (which is non-partial); `PATCH` passes `partial=True`, so nothing is required
there: see [Retrieve, update, delete](#retrieve-update-delete).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string, max 64 | **required** (POST/PUT) | Unique per org, case-insensitive |
| `status` | one of `STATUS_CHOICE` | **required** (POST/PUT) | `New`, `Assigned`, `Pending`, `Closed`, `Rejected`, `Duplicate` |
| `priority` | one of `PRIORITY_CHOICE` | **required** (POST/PUT) | `Low`, `Normal`, `High`, `Urgent` |
| `case_type` | one of `CASE_TYPE` | optional | `Question`, `Incident`, `Problem` |
| `closed_on` | date | optional | A Closed case always has one: a sent date wins, an absent or `null` one keeps the stored date, else today in the org's timezone. See [Retrieve, update, delete](#retrieve-update-delete) |
| `description` | text | optional | |
| `is_active` | boolean | optional | Defaults `true`; soft-delete flag, hidden from `GET /api/cases/` by default |
| `account` | uuid | optional on create; **write-once** | Must belong to the caller's org; silently `read_only` on `PUT`/`PATCH`. See [Retrieve, update, delete](#retrieve-update-delete) |
| `custom_fields` | object | optional | Validated against the org's `CustomFieldDefinition` rows |
| `parent` | uuid | optional | Same-org and readable by the caller; no self-parent, no cycle, at most 3 levels, nothing merged on either side. See [above](#retrieve-update-delete) |
| `is_problem` | boolean | optional | Defaults `false`; marks an ITIL "problem" (umbrella) ticket |
| `org` |. | **read-only** | Server-derived from `request.profile.org`; listed in `Meta.fields` but not writable |

Not part of the serializer, but accepted in the same request body and resolved by the view (each a
list of ids, org-scoped): `contacts`, `teams`, `assigned_to`, `tags`. Parsing is identical on every
verb (see [Create a case](#create-a-case)), but *writing* is not, and the difference is a real
data-loss trap: `POST` only ever adds. `PUT` replaces all four M2Ms **unconditionally** (`.clear()` on
teams, assignees and tags, and `replace_visible_contacts` for contacts),
before checking whether the request even mentioned them (`views.py:496,500,506,514`), so a `PUT`
that omits `assigned_to` from the body unassigns every assignee on the case, not just leaves them
unchanged. `PATCH` clears a given M2M only when its key is present in the body at all
(`if "assigned_to" in data:` in `update_case`, `cases/updates.py`, and identically for the other
three). Omitting a key on
`PATCH` genuinely leaves it untouched. `case_attachment` (a multipart file) is accepted on `POST` and
`PUT` (`views.py:391,521`) but **not on `PATCH`**; `CaseDetailView.patch` never reads
`request.FILES` at all, so a multipart `PATCH` carrying `case_attachment` silently drops the file with
no error.

`GET /api/cases/{id}/` and `GET /api/cases/` additionally return, but never accept as input: `id`,
`created_by`, `created_at`, `escalation_count`, `last_escalation_fired_at`, the SLA fields
(`sla_first_response_hours`, `sla_resolution_hours`, `first_response_at`, `resolved_at`,
`sla_paused_at`, `first_response_sla_deadline`, `resolution_sla_deadline`,
`is_sla_first_response_breached`, `is_sla_resolution_breached`), `parent_summary`, `child_count`, and
`time_summary` (`CaseSerializer.Meta.fields`, `cases/serializer.py:239-281`).
