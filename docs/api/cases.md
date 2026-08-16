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
That recurs throughout this codebase. **The [Approvals](#approvals) endpoints are the exception**:
`cases/approval_views.py` never imports `cases.access` at all, and none of its case-touching views
call any of the three functions above. See [Approvals](#approvals) for exactly what that means in
practice.

## List cases

`GET /api/cases/` (`CaseListView.get`, `cases/views.py:285-287`) returns one paginated list, ordered
`-created_at, -id` (`:169-174`): a comment on the queryset explains the tiebreak exists because `-id`
alone is a random UUID and was, in practice, no ordering at all. Soft-deleted cases
(`is_active=False`) are excluded by default; an org admin can pass `?include_deleted=true` to see them
(`:176-180`). Merged duplicates (`merged_into` set, or `status="Duplicate"`) are excluded by default
too; `?show_merged=true` includes them (`:181-186`). A non-admin caller sees only cases where they are
creator, assignee, or watcher, the same `visible_cases_qs` the detail view's read check uses
(`:190-198`), so list and detail agree by construction.

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

(`cases/views.py:222-263`.) `open_count`, `urgent_count` and `awaiting_first_reply` are counted over
the whole filtered queryset, not the page. `awaiting_first_reply` counts open cases
(`status` in `New`/`Assigned`/`Pending`) with no `first_response_at` yet. It is not the same thing as
an SLA breach, which depends on the org's business calendar and is computed per-row rather than in
this aggregate. `accounts_list`/`contacts_list` feed the create form's pickers, org-wide for an admin,
but narrowed to accounts/contacts the caller created or is assigned to for a non-admin, the same split
the case queryset itself gets (`:187-206`); either way they can be heavy, and `?slim=true` omits both
(`:254-256`).

Filters read directly from `request.query_params` (`apply_case_list_filters`, `cases/views.py:85-155`,
shared with `GET /api/cases/watching/`): `name` (contains), `status` (repeatable. A list applies
`status__in`, a single value applies exact match), `priority` (exact), `account` (exact id),
`case_type` (exact), `assigned_to` (repeatable id list), `tags` (id list), `search` (`name` or
`description`, contains), `created_at__gte`/`created_at__lte` (date range), `sla_breached=true`
(a wall-clock approximation of the SLA badge, Postgres-only raw SQL), `cf_<key>` (custom field equals),
and `ordering`: whitelisted to `created_at`, `-created_at`, `priority`, `-priority`, `id`, `-id`,
`name`, `-name` (`:71-82,150-153`); anything else is silently ignored rather than erroring.

## Create a case

`POST /api/cases/` (`CaseListView.post`, `cases/views.py:306-416`) accepts `multipart/form-data` (for
an optional `case_attachment` file) or JSON, validated by `CaseCreateSerializer`
(`cases/serializer.py:139-281`; see [Fields](#fields)). On success (`200`):

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
  cross-tenant id with `"No such account."` (`cases/serializer.py:151-165`); the comment on it notes
  this closes both a cross-tenant *write* and a cross-tenant *read*, since the create response nests
  the account back through `CaseSerializer` → `AccountSerializer`.
- **`name` must be unique per org, case-insensitive** (`validate_name`, `:232-244`).

`contacts`, `teams`, `assigned_to` and `tags` are **not** part of `CaseCreateSerializer`. The view
reads each from the request body after the serializer succeeds, resolved against
`org=request.profile.org` (`:332-386`). Unlike [Leads](leads.md#fields), which documents a real
per-field, per-verb inconsistency in how these four are *parsed*, cases parses all four identically on
every verb: each is `json.loads`'d when it arrives as a string (which happens whenever the same
request also uploads `case_attachment` as multipart) before the id list is extracted. `case_attachment`
itself is read straight off `request.FILES` (`:388-395`): on `POST` only; see
[Retrieve, update, delete](#retrieve-update-delete) for why `PATCH` can't set it at all, and for how
*writing* these four fields (not just parsing them) differs sharply across verbs.

## Retrieve, update, delete

`GET /api/cases/{id}/` (`CaseDetailView.get`, `cases/views.py:612-731`) looks the case up scoped to
the caller's org (`get_case_or_404`, `cases/access.py:57-71`: a malformed UUID or a case in another
org both answer `404`, never `500`, matching [Errors](errors.md#not-found-versus-forbidden)). If the
case has been merged into another one, the response is a redirect hint instead of the case itself:
`{"redirect_to": "<uuid>", "merged_into": "<uuid>", "source_case_id": "<uuid>", "source_case_name": "..."}`,
unless the caller passes `?show_merged=true` (`:614-630`). **This merged-case branch returns before
any read-access check runs at all**; `assert_case_read_access` isn't called until `:633`, several
lines after the merged branch's own `return` at `:630`, so `source_case_name` is disclosed to any org
member who can supply a valid case id, including one with no read access to the case whatsoever, as
long as it happens to be a merged duplicate. On the ordinary, non-merged path, read access genuinely is
checked before the rest of the response body is built (`assert_case_read_access`, `:633`, the comment
on this line notes the previous order built the payload for a case the requester was about to be
refused); it's specifically the merged-redirect shortcut that skips the check entirely. The response nests
the record under `cases_obj`, alongside `attachments`, `comments` (public only), `internal_notes`,
`contacts`, `solutions`, `activities` (last 20), `email_messages` (last 50, inbound-email threads),
`merged_from_cases`, `custom_field_definitions`, `status`, `priority`, `type_of_case`,
`comment_permission` and `users_mention` (`:707-730`). `comment_permission` is computed with the same
`has_case_write_access` the comment-post endpoint enforces (`:642`), so the button a client shows and
the answer the server gives agree by construction, the comment on this line notes that used to not be
true (`comment_permission` was creator-or-admin while the write endpoint below also allowed assignees).

`PUT /api/cases/{id}/` (`:448-570`) and `PATCH /api/cases/{id}/` (`:823-942`) both call
`assert_case_write_access` before touching anything. **`account` cannot be changed by either verb.**
`CaseCreateSerializer.__init__` sets `self.fields["account"].read_only = True` whenever `self.instance`
is set (`cases/serializer.py:146-149`) (true for both update calls, never for create), so on `PUT`/
`PATCH` an `account` in the body is silently dropped before validation ever runs; `validate_account`
never executes and no error is returned. `PUT` is a non-partial serializer instantiation, so it still
requires `name`/`status`/`priority` the same as `POST` does; `PATCH` passes `partial=True`
(`:832`), so none of the three are required there. A `PATCH` may touch just one field.

Closing a case is validated as a *transition*, not just a target value: `CaseCreateSerializer.validate()`
(`cases/serializer.py:167-230`) requires `closed_on` whenever `status` is being set to `"Closed"` from
anything other than `"Closed"`, and, when an active `pre_close` `ApprovalRule` matches the case's
priority/case_type/team. Requires an `Approval` row in state `approved` for that case and rule before
the close is allowed. This exists because `Case.clean()` states the same two rules
(`cases/models.py:180-249`) but `Case.save()` (`:264-283`) never calls `full_clean()`, so without the
serializer-level check a matching rule could be armed and a `PATCH {"status": "Closed"}` would still
return `200` and record zero approvals. Proven live per the comment on `validate()`. Success on either
verb, identical literal string on both (`views.py:564,936`):
`{"error": false, "message": "Case Updated Successfully"}`.

**Three of `Case.clean()`'s parent-tree guards are dead code through the API, and one field that
looks like a partial re-implementation of the model isn't a re-implementation at all.**
`Case.clean()` (`cases/models.py:180-249`) states four rules about `parent`: a case cannot be its own
parent (`:222-223`); linking cannot create a multi-level cycle, walking the whole parent chain to check
(`:226-238`); the tree is capped at `PARENT_MAX_DEPTH = 3` levels (`:178,240-243`); and a case that is
itself `status="Duplicate"` cannot be given a parent at all (`:247-250`, the self-side of the merge
guard, distinct from `parent.status == "Duplicate"`, checked separately at `:245-246`). None of the
four run through any API write, because `Case.save()` (`:264-283`) never calls `full_clean()`. Of the
four, `CaseCreateSerializer.validate_parent()` (`cases/serializer.py:246-263`) independently
re-implements exactly two: the self-parent check, and refusing a parent whose own `status` is
`Duplicate`. **The cycle walk and the depth limit are simply absent from the API, not partially
covered, not approximated, and so is the "a Duplicate case cannot receive a parent" direction.**
`validate_parent()` also rejects a cross-org `parent`, but that check has no model-side counterpart at
all: `Case.clean()` never compares `org_id`, so this is a serializer-only addition, not a partial port
of anything the model already states. Net effect: a same-org, non-self parent assignment that would
create a longer cycle (A's parent is B, then B's parent is set to A), exceed three levels, or give a
merged case a parent, is accepted by every write endpoint today: reported here, not fixed, per this
documentation task's scope.

`DELETE /api/cases/{id}/` (`:586-593`) calls `assert_case_delete_access`, admin or creator only, no
assignee exception (see [above](#a-cases-read-write-and-delete-rules-differ-on-purpose)).

## Comments and attachments

Adding a comment or an attachment is the same `POST /api/cases/{id}/` route the detail endpoint uses,
just with a body (`CaseDetailView.post`, `cases/views.py:749-806`), gated by
`assert_case_write_access` (`:755`). The same rule `comment_permission` on `GET` reports. Send
`comment` (text, and optionally `is_internal`, a truthy string or boolean) and/or `case_attachment`
(multipart file), either or both. A comment created with `is_internal=true` is an internal note, not
visible to a customer-facing surface; the response splits both back out as `comments` (public) and
`internal_notes` (`:794-805`), and both are returned to any caller with read access; `is_internal` is
a display split, not an access-control split on this endpoint.

Editing or deleting an *existing* comment goes through `PUT`/`PATCH`/`DELETE /api/cases/comment/{id}/`
(`CaseCommentView`, `cases/views.py:945-1078`). Restricted to an admin, the `is_organization_admin`
flag (exposed as `.is_admin`, `common/models.py:243-246`: org-scoped, not the platform-level
superuser flag), or the comment's own author (`request.profile == obj.commented_by`, e.g. `:982`).
`Comment.commented_by` is a `Profile` foreign key (`common/models.py:275-277`), so this comparison is
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

`DELETE /api/cases/attachment/{id}/` (`CaseAttachmentView.delete`, `cases/views.py:1098-1142`) is
org-scoped (`.filter(pk=pk, org=request.profile.org)`, `:1119-1121`) and its ownership check compares
`request.profile.user_id` to `self.object.created_by_id`, both `User` ids, correctly typed
(`:1128-1130`). The view's own docstring documents two defects that were live in this exact endpoint
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
- `POST /api/cases/{id}/request-approval/` (`CaseRequestApprovalView.post`, `:201-279`), **no case
  access check of any kind.** The view fetches the case with a bare
  `get_object_or_404(Case, id=pk, org=org)` (`:209`), org-scoped, but nothing from `cases.access`;
  `approval_views.py` doesn't import that module at all. Any org member, including one refused the case
  itself with a `403` on `GET /api/cases/{id}/`, can file an approval request against it. The `201`
  response is `ApprovalSerializer(approval).data`, whose `case_summary`
  (`cases/serializer.py:1002-1013`) returns the case's `name`, `status`, `priority` and account name.
  The same read-around `CaseSolutionLinkView` was fixed for (see [Solutions](#solutions)), reintroduced
  here. Also note: because the lookup is `get_object_or_404` rather than the `get_case_or_404` helper
  every other case endpoint on this page uses, a malformed (non-UUID) `id` here isn't a clean `404`;
  `get_object_or_404` only catches `Model.DoesNotExist`, not the `ValidationError`/`ValueError` a bad
  UUID raises against `Case.id`, so it reaches the client as an unhandled `500`, the exact failure mode
  `get_case_or_404` (`cases/access.py:57-71`) exists to prevent elsewhere on this page. Beyond
  existence-and-org-scope, the request is validated against an explicit `rule_id` (which must actively
  match the case) or, when omitted, the best `find_matching_rule` result. A second request against the
  same case+rule while one is already `pending` is refused with `409`, not a duplicate row (`:244-258`).
- `GET /api/cases/approvals/` (`ApprovalInboxView.get`, `:282-341`), the inbox, also with no
  case-level access check: the queryset is `Approval.objects.filter(org=org)` (`:296`), not filtered
  through `visible_cases_qs` or any case-access helper, so a member's `mine=false` inbox view can
  include approval rows, and their `case_summary`, for cases that member has no access to.
  `?state=` filters (default `pending`; `all` drops the filter), `?case=<id>` scopes to one case, and
  `?mine=true` restricts to rows the caller can currently act on (see below), which deliberately
  excludes the caller's own requests, "mine to decide" rather than "mine to have filed" (`:318-332`).
  Neither gap is fixed here, reported per this documentation task's scope.

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

`ApprovalSerializer.can_act` and `.is_own_request` (`cases/serializer.py:1045-1062`) mirror the approve
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
(`CaseSolutionLinkView`, `cases/views.py:1145-1208`), gated by `assert_case_write_access` on the
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

`CaseCreateSerializer.Meta.fields` (`cases/serializer.py:265-281`) is what `POST /api/cases/`,
`PUT /api/cases/{id}/` and `PATCH /api/cases/{id}/` accept. "Required" below means required on
`POST` and on `PUT` (which is non-partial); `PATCH` passes `partial=True`, so nothing is required
there: see [Retrieve, update, delete](#retrieve-update-delete).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string, max 64 | **required** (POST/PUT) | Unique per org, case-insensitive |
| `status` | one of `STATUS_CHOICE` | **required** (POST/PUT) | `New`, `Assigned`, `Pending`, `Closed`, `Rejected`, `Duplicate` |
| `priority` | one of `PRIORITY_CHOICE` | **required** (POST/PUT) | `Low`, `Normal`, `High`, `Urgent` |
| `case_type` | one of `CASE_TYPE` | optional | `Question`, `Incident`, `Problem` |
| `closed_on` | date | **required when closing** | See [Retrieve, update, delete](#retrieve-update-delete); optional otherwise |
| `description` | text | optional | |
| `is_active` | boolean | optional | Defaults `true`; soft-delete flag, hidden from `GET /api/cases/` by default |
| `account` | uuid | optional on create; **write-once** | Must belong to the caller's org; silently `read_only` on `PUT`/`PATCH`. See [Retrieve, update, delete](#retrieve-update-delete) |
| `custom_fields` | object | optional | Validated against the org's `CustomFieldDefinition` rows |
| `parent` | uuid | optional | Same-org, non-self, parent not `Duplicate`; see the dead model-level guards [above](#retrieve-update-delete) |
| `is_problem` | boolean | optional | Defaults `false`; marks an ITIL "problem" (umbrella) ticket |
| `org` |. | **read-only** | Server-derived from `request.profile.org`; listed in `Meta.fields` but not writable |

Not part of the serializer, but accepted in the same request body and resolved by the view (each a
list of ids, org-scoped): `contacts`, `teams`, `assigned_to`, `tags`. Parsing is identical on every
verb (see [Create a case](#create-a-case)), but *writing* is not, and the difference is a real
data-loss trap: `POST` only ever adds. `PUT` calls `.clear()` on all four M2Ms **unconditionally**,
before checking whether the request even mentioned them (`views.py:485,501,515,531`), so a `PUT`
that omits `assigned_to` from the body unassigns every assignee on the case, not just leaves them
unchanged. `PATCH` clears a given M2M only when its key is present in the body at all
(`if "assigned_to" in params:`, `:903`, and identically for the other three). Omitting a key on
`PATCH` genuinely leaves it untouched. `case_attachment` (a multipart file) is accepted on `POST` and
`PUT` (`views.py:388,545`) but **not on `PATCH`**; `CaseDetailView.patch` never reads
`request.FILES` at all, so a multipart `PATCH` carrying `case_attachment` silently drops the file with
no error.

`GET /api/cases/{id}/` and `GET /api/cases/` additionally return, but never accept as input: `id`,
`created_by`, `created_at`, `escalation_count`, `last_escalation_fired_at`, the SLA fields
(`sla_first_response_hours`, `sla_resolution_hours`, `first_response_at`, `resolved_at`,
`sla_paused_at`, `first_response_sla_deadline`, `resolution_sla_deadline`,
`is_sla_first_response_breached`, `is_sla_resolution_breached`), `parent_summary`, `child_count`, and
`time_summary` (`CaseSerializer.Meta.fields`, `cases/serializer.py:97-136`).
