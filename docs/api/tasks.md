# Tasks

Routes in `backend/tasks/urls.py`. CRUD, comments and attachments are in
`backend/tasks/views/task_views.py`; Kanban pipelines/stages and the move endpoint in
`backend/tasks/views/kanban_views.py`; Kanban **boards**, a separate feature, in
`backend/tasks/views/board_views.py`. Object-level authorization is centralized in
`backend/tasks/access.py`. See [Conventions](conventions.md) for pagination, filtering and
response-shape rules assumed rather than repeated here, and [Errors](errors.md) for the
404-vs-403 principle referenced below.

## `Task.save()` calls `full_clean()`

Before anything else, one fact that changes how you should read every write endpoint below:
**`Task.save()` explicitly calls `self.full_clean()`** (`tasks/models.py:402-404`). This is not the
default, [Architecture: Data model](../architecture/data-model.md#conventions) documents that most
models here (`Lead`, `Opportunity`, `Case`) define real validation in `clean()` that is never invoked
through the API, because their `save()` methods don't call `full_clean()`. `Task` is one of a small
handful that do (alongside `Comment.save()` and `Attachments.save()`, which enforce that a comment or
attachment's `org` matches its parent record's org. Same page). The consequence is the opposite
problem: when `full_clean()` *does* run and finds a violation, it raises Django's own
`django.core.exceptions.ValidationError`, not DRF's, and [Errors](errors.md) states plainly, in its
own opening paragraph, that this codebase's `EXCEPTION_HANDLER` is DRF's default, unmodified, which
does not know how to render that exception. Unless the specific view code path catches it, a
model-level rule violation on a `Task` reaches the client as an unhandled **`500`**, not a clean `400`.

The one rule `Task.clean()` currently enforces is "at most one parent entity". A task may link to
at most one of `account`, `opportunity`, `case`, `lead` (`tasks/models.py:384-400`). The main
create/update surface guards against this twice: `TaskCreateSerializer.validate()`
(`tasks/serializer.py:331-365`) re-implements the same rule and returns a clean `400` with the field
named, *and* `TaskListView.post` / `TaskDetailView.put` / `TaskDetailView.patch` additionally wrap
`serializer.save()` in `try/except django.core.exceptions.ValidationError` as a backstop
(`_model_errors`, `tasks/views/task_views.py:56-66`, used at `:277-281,577-581,702-706`), so for
`POST /api/tasks/`, `PUT /api/tasks/{id}/` and `PATCH /api/tasks/{id}/` specifically, this rule
reaches the client as `{"error": true, "errors": {"account": "..."}}` with `400` today, not a `500`.

**Not every write path shares that backstop.** `PATCH /api/tasks/{id}/move/`
(`TaskMoveView.patch`, `tasks/views/kanban_views.py:253-318`), the Kanban drag-and-drop endpoint,
sets `task.stage`, `task.status` and `task.kanban_order` directly and calls `task.save()` with no
`try/except` around it (`:310`) and without going through `TaskCreateSerializer` at all. `full_clean()`
validates every field on the instance, not only the ones a given endpoint touched, so any model-level
violation it finds here, on this task's existing `account`/`opportunity`/`case`/`lead` combination, or
any other `clean_fields()` rule, surfaces as the unhandled `500` described above. This isn't only a
theoretical exposure through the parent-entity fields: `TaskStage.maps_to_status`
(`tasks/models.py:257-263`) is a plain `CharField`, `blank=True, null=True`, with no `choices=`
constraint. Free text an admin sets when configuring a pipeline stage, not validated against
`Task.STATUS_CHOICES` at all. When a task moves into a stage that has one set,
`if stage.maps_to_status: task.status = stage.maps_to_status` (`kanban_views.py:297-298`) assigns it
straight onto `task.status`. A field that *does* have `choices=` at the model level. A single typo'd
`maps_to_status` on one admin-configured stage turns every future move into that stage into an
unhandled `500` for every user, until an admin notices and fixes the stage. This endpoint has no
equivalent of the two-layer guard the main CRUD endpoints have.

## List tasks

`GET /api/tasks/` (`TaskListView.get`, `tasks/views/task_views.py:233-235`) returns one paginated
list. Visibility is `visible_tasks_qs` (`tasks/access.py:43-48`), admin sees every task in the org;
anyone else sees only tasks they created or are assigned to. This is the same rule the detail view
enforces (`tasks/access.py` module docstring: "the list has to agree with the detail view or the queue
lies").

```json
{
  "tasks_count": 24,
  "offset": 10,
  "tasks": [ { "...": "TaskListSerializer" } ],
  "totals": {"count": 24, "open": 18, "overdue": 3, "due_today": 1, "due_this_week": 5, "no_due_date": 6, "unassigned": 2},
  "status": [["New", "New"], ["In Progress", "In Progress"], ["Completed", "Completed"]],
  "priority": [["Low", "Low"], ["Medium", "Medium"], ["High", "High"]],
  "users": [{"id": "...", "user__email": "..."}],
  "accounts_list": [ { "...": "AccountSerializer" } ],
  "contacts_list": [ { "...": "ContactSerializer" } ]
}
```

(`tasks/views/task_views.py:130-209`.) `totals` is one aggregate query over the whole filtered,
visibility-scoped queryset, not the page, `overdue`/`due_today`/`due_this_week`/`no_due_date`/
`unassigned` all additionally require the task still be open (`status != "Completed"`), so a task that
finished late does not count as overdue (`:145-167`). `accounts_list`/`contacts_list` are the full org
catalogues for the parent-entity picker; `?slim=true` omits both (`:203-209`).

Filters, read directly from `request.query_params`
(`tasks/views/task_views.py:86-129`): `title` (contains), `status` (exact), `priority` (exact),
`assigned_to` (repeatable id list), `tags` (id list), `search` (matches `title`, contains, not a
combined search across other fields the way [Leads](leads.md#list-leads) or
[Contacts](contacts.md#list-contacts) define one), `due_date__gte`/`due_date__lte`,
`created_at__gte`/`created_at__lte`, `account`, `opportunity`, `case`, `lead` (each an exact parent-id
match), and `cf_<key>` (custom field equals).

## Create a task

`POST /api/tasks/` (`TaskListView.post`, `tasks/views/task_views.py:252-349`) accepts JSON or
`multipart/form-data`, validated by `TaskCreateSerializer` (`tasks/serializer.py:315-389`; see
[Fields](#fields)). On success (`200`): `{"error": false, "message": "Task Created Successfully"}`. A
validation failure returns `400` with `{"error": true, "errors": {...}}`, including the "at most one
parent" rule and the model's own `full_clean()` backstop, both described [above](#tasksave-calls-full_clean).

`account`, `opportunity`, `case` and `lead` are each scoped to the caller's org in
`TaskCreateSerializer.__init__` (`tasks/serializer.py:328-329`), the comment there notes a plain
`ModelSerializer` would otherwise build each as a `PrimaryKeyRelatedField` over *every* row in the
table, and that a task in one org was proven to accept another org's account, with the list endpoint
then rendering that org's account name back to the requester. `created_by` is listed in
`Meta.fields` but is `read_only` (`:389`), the comment explains that leaving it writable let a client
name any user in any org as a task's creator, which becomes a privilege-escalation vector the moment
the creator clause in `tasks/access.py` is consulted for anything (which it is. See
[Fields](#fields)).

`contacts`, `teams`, `assigned_to` and `tags` are **not** part of `TaskCreateSerializer`: the view
resolves each from the request body after the serializer succeeds, org-scoped, `json.loads`'d when
sent as a string (the same shape [Cases](cases.md#create-a-case) uses). *Parsing* is consistent across
all four fields on every verb. Tasks does not have the per-field inconsistency
[Leads](leads.md#fields) documents. *Writing* is a different story, and the same data-loss trap
[Cases](cases.md#fields) documents applies here verbatim: `POST` only adds
(`task_views.py:282,296,308,322`); `PUT` calls `.clear()` on all four **unconditionally**, before
checking whether the body mentioned them at all (`:582,597,610,625`), so a `PUT` that omits
`assigned_to` unassigns the task from everyone, including, via `visible_tasks_qs`
(`tasks/access.py:43-48`), removing it from those people's task lists entirely; `PATCH` clears a given
M2M only when its key is present in the body (`:709-758`).

## Retrieve, update, delete

`GET /api/tasks/{id}/` (`TaskDetailView.get`, `tasks/views/task_views.py:460-463`) org-scopes the
lookup (`get_task_or_404`, `tasks/access.py:51-65`, a malformed id or a cross-org task both answer
`404`) and enforces `assert_task_access` before building the response (`:365`): raised, not returned,
specifically because a `Response` object handed back through this method used to be wrapped in a
second `Response` and crash with a `500` instead of answering `403` (comment at `:361-364`). The
response nests the record under `task_obj`, alongside `attachments`, `comments`, `users_mention`,
`assigned_data`, `custom_field_definitions`, `users` (admins see every active member; non-admins see
only other admins, the same shape [Cases](cases.md#list-cases) uses for its assignee picker),
`users_excluding_team` and `teams`.

`PUT /api/tasks/{id}/` (`:545-650`) and `PATCH /api/tasks/{id}/` (`:667-781`) both enforce
`assert_task_access`: access here has only two tiers, not three the way a case does: **`access`
(read/write together) is admin · creator · assignee; `delete` is the narrower admin · creator**
(`tasks/access.py:1-10`). The module docstring is explicit about why a task collapses read and write
into one rule where a case does not: a case can have a *watcher*, someone following a ticket without
being handed it, and that extra read-only tier has no equivalent on a task; "everyone who may open one
may also work it." `contacts`/`teams`/`assigned_to`/`tags` are re-attached from the request body the
same way `POST` does. Success: `{"error": false, "message": "Task updated Successfully"}`.

`DELETE /api/tasks/{id}/` (`:797-805`) calls `assert_task_delete_access`: admin or creator only, no
assignee exception, same reasoning as [Cases](cases.md#retrieve-update-delete): being assigned a task
is a reason to work it, not to erase it.

## Comments and attachments

The same shape as [Cases](cases.md#comments-and-attachments): `POST /api/tasks/{id}/`
(`TaskDetailView.post`, `tasks/views/task_views.py:481-528`) adds a `comment` and/or a
`task_attachment` file to an existing task, gated by `assert_task_access`. The comment on this method
notes a real trap: validating the comment through `CommentSerializer` fails *silently* here, because
that serializer requires `object_id` and `org`, neither of which the client sends; `is_valid()`
returns `False` and the intended write is simply skipped, so the view constructs the `Comment` directly
with Django's ORM instead of going through the serializer (`:488-500`).

`PUT`/`PATCH`/`DELETE /api/tasks/comment/{id}/` (`TaskCommentView`, `:808-946`) edit or remove an
existing comment: admin, or the comment's own author (`request.profile == obj.commented_by`, a
`Profile`-to-`Profile` comparison, correct). **`PUT` cannot, in practice, edit a comment.** It builds
`CommentSerializer(obj, data=params)` with no `partial=True` (`:851`), and `CommentSerializer.Meta.fields`
includes the plain (not `read_only`) model fields `object_id` and `org`
(`common/serializer.py:239-250`): a body of just `{"comment": "..."}`, everything the documented
`TaskCommentEditSwaggerSerializer` contract asks for, fails validation on the missing `object_id`/`org`
and answers `400`. Only `.patch()` (`partial=True`, `:896`) can actually change a comment's text.
Separately, because `object_id`/`org` genuinely are writable on `PATCH`, a comment's own author can
supply both there to repoint the comment at a different record, including one in another org or one
they cannot otherwise read, `Comment.clean()` (`common/models.py:299-314`) only catches the mismatched
case (`org` doesn't match the new target's `org`), and only because `Comment.save()` calls
`self.full_clean()` (`:316-318`), which turns that mismatch into an unhandled `500` rather than a `400`
(same failure mode as [above](#tasksave-calls-full_clean), on a different model); a *matching* cross-org
pair saves without error. Neither behavior is fixed here, reported per this documentation task's
scope. The identical `PUT`/`PATCH` pattern exists on [Cases](cases.md#comments-and-attachments)' own
comment-edit endpoint too.

`DELETE /api/tasks/attachment/{id}/` (`TaskAttachmentView.delete`, `:949-1000`) is org-scoped
(`.filter(pk=pk, org=request.profile.org)`, `:975-977`) and its ownership check compares
`request.profile.user_id` to `self.object.created_by_id` (`:987`), both fixed here. The comment on
this method documents that both defects (an unscoped lookup across every org's attachments, and a
`Profile`-to-`User` comparison that silently made the endpoint admin-only) were proven live before the
fix. See [Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks)
for the current, cross-app state of the same defect pattern in other modules.

## Boards and columns

**Kanban *boards* are a separate feature from a task's own `stage`/`pipeline` fields, with their own
model family (`Board`, `BoardColumn`, `BoardTask`, `BoardMember`, `tasks/models.py:16-194`) and their
own endpoints mounted at `/api/boards/...`, not under `/api/tasks/...`.** Confirmed in
`backend/common/app_urls/__init__.py:26`: `path("boards/", include((board_urlpatterns, "api_boards")))`,
where `board_urlpatterns` is imported from `tasks/urls.py:58-75`. A `BoardTask` card is not a `Task`
row. It has its own title/description/priority/assignees and only optionally references an
`account`/`contact`/`opportunity` for context (`tasks/models.py:116-195`); the `/api/tasks/kanban/`,
`/api/tasks/pipelines/...`, `/api/tasks/pipelines/{pipeline_id}/stages/` and `/api/tasks/stages/{id}/`
endpoints documented implicitly above (via `stage` in [Fields](#fields)) operate on real `Task` rows and
are a different Kanban system entirely.

Access to a board is per-board membership, not org-wide: `Board.owner` plus `BoardMember` rows carry a
`role` of `owner`/`admin`/`member` (`tasks/models.py:43-81`). Every endpoint that acts on an *existing*
board checks membership before doing anything (the one exception is creating a board in the first
place; `POST /api/boards/` requires nothing beyond org membership, since there's no board to be a
member of yet); some further gate by role:

| Endpoint | Who |
| --- | --- |
| `GET /api/boards/` | Any authenticated member of the org; results are owner-or-member boards only (`board_views.py:82-89`) |
| `POST /api/boards/` | Any org member; creates the board and, by default, three starter columns (`:126-157`) |
| `GET /api/boards/{id}/` | Board owner or member (`404` otherwise, not `403`, `:165-192`) |
| `PUT/PATCH /api/boards/{id}/` | Board `owner` or `admin` role (`:206-271`) |
| `DELETE /api/boards/{id}/` | Board owner only (`:282-299`) |
| `GET /api/boards/{board_id}/columns/` | Board owner or member (`:316-329`) |
| `POST /api/boards/{board_id}/columns/` | Board `owner` or `admin` role (`:355-367`) |
| `GET/POST /api/boards/columns/{column_id}/tasks/` | Any board member (no role check, `:413-455`) |
| `PUT /api/boards/tasks/{id}/` | Any board member (no role check, `:490-503`) |
| `DELETE /api/boards/tasks/{id}/` | Any board member, the role check lists `owner`/`admin`/`member`, which is every role that can exist (`:570-583`) |

**A malformed board/column/card id is a `500`, not a `404`, on every endpoint in this table.** Every
lookup in `board_views.py` uses Django's `get_object_or_404` against a UUID primary key
(`:167,319,358,416,445,493,573`), and `get_object_or_404` only catches the model's `DoesNotExist`, not
the `ValidationError`/`ValueError` a non-UUID string raises against a UUID field. This is the exact bug
class `get_task_or_404`/`get_case_or_404` (`tasks/access.py:51-65`, `cases/access.py:57-71`) exist to
prevent on the rest of this API; boards never adopted that helper. Not fixed here, reported per this
documentation task's scope.

**There is no endpoint to update or delete a single column once created.** `board_urlpatterns`
(`tasks/urls.py:58-75`) defines only `GET`/`POST /api/boards/{board_id}/columns/`, a list-and-create
route, with no `columns/{id}/` detail path at all. A column's name, color, order or WIP `limit` cannot
be changed, and a column cannot be removed, through this API today.

**`GET /api/boards/{id}/` does not return each column's cards.** `BoardSerializer.columns` uses
`BoardColumnListSerializer` (`tasks/serializer.py:109-121,123-143`), which reports only a `task_count`
per column, not the cards themselves. To read the actual cards, call
`GET /api/boards/{board_id}/columns/` separately, which uses the full `BoardColumnSerializer` and
nests nested `BoardTaskSerializer` cards under each column (`board_views.py:334-343`).

`PUT /api/boards/tasks/{id}/` (`BoardTaskDetailView.put`, `board_views.py:490-559`) is also how a card
moves between columns: send `column` (validated to be a column of the same board, a column id from
another board, or from another org's board by construction, is a `400`, not a silent no-op,
`:512-529`) and/or `order`; the view renumbers both the source and target columns to a dense
`0..n-1` ordering after the move so drag positions survive a reload (`_resequence_column`, `:22-48`).

`BoardTaskSerializer`'s write surface is deliberately narrow (`tasks/serializer.py:36-90`): a card
write may set `title`, `description`, `order`, `priority`, `due_date`, and assignees (via the
write-only `assigned_to_ids`, applied by the view after save and org-filtered). Everything else is
locked down, by two different mechanisms: `id`, `created_at`, `updated_at`, `completed_at`, `org`,
`column`, `created_by`, `updated_by`, `contact` and `opportunity` are all in `read_only_fields`
(`:67-78`): `column` because moving a card is the dedicated `PUT` flow above, and the CRM-link FKs
because, unfiltered, they exposed the same cross-org `PrimaryKeyRelatedField` gap
`TaskCreateSerializer` was hardened against. `account` reaches the same outcome a different way: it's
overridden as a plain `SerializerMethodField()` (`:60`), not listed in `read_only_fields` at all,
overriding a model FK with a method field removes it from the write surface just as effectively,
per the field's own comment (`:82-84`).

## Calendar feed

From django-crm 1.13.0 each member can subscribe a calendar app (Google Calendar, Outlook, Apple
Calendar) to their open tasks. Management is `CalendarFeedView` and the feed itself
`PublicCalendarFeedView`, both in `backend/common/views/calendar_feed_views.py`; the iCalendar
writer is `backend/common/calendar_feed.py` and the token model `CalendarFeedToken` in
`backend/common/models.py`.

### Managing your feed

`/api/profile/calendar-feed/` manages the caller's own feed and nobody else's: every query filters
on `org=request.profile.org` and `profile=request.profile`. It is on the credential deny-list in
`common/scopes.py`, so a personal access token or the org API key is refused whatever its scopes
(see [Tokens and API keys](tokens-and-api-keys.md#what-no-token-may-do-whatever-its-scopes)). A feed
URL keeps working after the token that could have minted it is revoked, so only a signed-in session
may create one.

| Method | Does | Response |
| --- | --- | --- |
| `GET` | Reads the state | `200` `{"error": false, "enabled": true, "created_at": "...", "last_used_at": "..."}` |
| `POST` | Turns the feed on, or replaces it | `201`, the same fields plus `url` |
| `DELETE` | Turns it off | `200` with `enabled: false` |

The URL is returned by `POST` once and cannot be read back: only the SHA-256 of the token is stored.
`POST` on an enabled feed deletes the old token in the same transaction, so the old URL stops working
the moment the new one exists. `last_used_at` is when a calendar app last fetched the feed, moved
forward at most once an hour; it is `null` until the first fetch.

`url` is built from the `DOMAIN_NAME` setting (`common.links.api_url`), the API's public origin,
never from the request. The web app calls the API from its own server at `PUBLIC_DJANGO_API_URL`
(`http://backend:8000` in Docker), so a URL built from the request named a host no calendar service
can reach, and read `http://` behind a TLS terminator. Outside dev the backend refuses to start when
`DOMAIN_NAME` is a loopback or non-absolute URL; see
[Environment variables](../reference/environment-variables.md).

### The feed

`GET /api/public/calendar/{token}.ics` takes no credential except the token in the path and answers
`text/calendar`. It is what a calendar app polls, on its own schedule (Google refreshes a subscribed
calendar every few hours), so a change to a task reaches the calendar late.

- **Which tasks.** Status `New` or `In Progress`, with a due date from 90 days ago to 365 days ahead
  in the org's timezone. Only the member's own tasks: ones whose `created_by` is their user or that
  are assigned to them, for admins too (an admin's feed does not carry the rest of the org), and
  never a task outside `visible_tasks_qs`. At most 1,000, keeping the tasks nearest today: tasks due
  today or later, soonest first, then overdue tasks, most recent first, until the cap is reached. It
  is re-read on every fetch, so an unassignment or a removal from the org applies to the next poll.
- **What an event shows.** One all-day event on the due date, marked free rather than busy, with the
  task's title as the summary, `Priority: <priority>` and a link to `/tasks/{id}` in the web app as
  the description, and the same link as its `URL`. Nothing else: not the task's own description,
  and no contact, account or other record names, because a subscribed calendar is read by a
  third-party service on the member's behalf.
- **Misses.** A malformed, unknown, replaced or disabled token, and one whose profile, user or org
  is no longer active, all answer the same `404` `{"error": "Not found"}`.
- **Throttles.** `CALENDAR_FEED_THROTTLE_IP` (default `1000/hour`, per client IP; generous because
  calendar services fetch from shared addresses) and `CALENDAR_FEED_THROTTLE_TOKEN` (default
  `60/hour`, per feed, bucketed on the token's hash). See
  [Environment variables](../reference/environment-variables.md).
- The response carries `Cache-Control: private, max-age=300` and `X-Robots-Tag: noindex`, and Sentry
  events have the token cut from any URL they record.

`/api/public/calendar/` is in `RequireOrgContext.EXEMPT_PATHS`. The view finds the token row (the
`calendar_feed_token` table is not under RLS, like `personal_access_token`), takes the org from its
profile, sets the RLS context, and only then reads tasks.

The URL is a secret. Anyone holding it can read the titles and priorities of the member's open
tasks without signing in, so share it with nothing but the calendar app, and regenerate it (a new
`POST`) if it leaks. In the clients the feed is managed at `/profile/calendar-feed` on the web and
**Profile → Calendar feed** on the phone.

## Fields

`TaskCreateSerializer.Meta.fields` (`tasks/serializer.py:367-389`) is what `POST /api/tasks/`,
`PUT /api/tasks/{id}/` and `PATCH /api/tasks/{id}/` accept.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string, max 200 | **required** | Explicitly forced `required=True` in `__init__` (`:321`), matching the model |
| `status` | one of `Task.STATUS_CHOICES` | **required** | `New`, `In Progress`, `Completed`, a different vocabulary from `Case.status`, see [Cases: Fields](cases.md#fields) |
| `priority` | one of `Task.PRIORITY_CHOICES` | **required** | `Low`, `Medium`, `High`, a different vocabulary from `Case.priority` |
| `due_date` | date | optional | |
| `description` | text | optional | |
| `account` | uuid | optional | Org-scoped; at most one of `account`/`opportunity`/`case`/`lead` may be set. See [above](#tasksave-calls-full_clean) |
| `opportunity` | uuid | optional | Same one-parent rule |
| `case` | uuid | optional | Same one-parent rule |
| `lead` | uuid | optional | Same one-parent rule |
| `created_by` |. | **read-only** | Listed in `Meta.fields`, but server-derived; see [Create a task](#create-a-task) |

Not part of `TaskCreateSerializer`, but accepted in the same request body: `contacts`, `teams`,
`assigned_to`, `tags` (each a list of ids, org-scoped. See [above](#retrieve-update-delete) for how
*writing* them diverges across verbs); plus `task_attachment` (a multipart file) and `custom_fields`
(an object). Neither of the last two goes through the serializer either. The view reads
`custom_fields` straight off `request.data`, validates it against the org's
`CustomFieldDefinition` rows, and passes the cleaned result to `serializer.save(custom_fields=...)`
on all three write verbs (`task_views.py:256-276,556-574,681-699`); `task_attachment` is read off
`request.FILES` the same way it is on [Cases](cases.md#create-a-case).

`GET /api/tasks/{id}/` returns the record via `TaskSerializer`; `GET /api/tasks/` returns each row via
the slimmer `TaskListSerializer`, which additionally drops `description`, `custom_fields`, `contacts`
and `teams` (compare `TaskSerializer.Meta.fields`, `tasks/serializer.py:287-309`, against
`TaskListSerializer.Meta.fields`, `:256-271`). **Two fields declared on `TaskSerializer` never actually
reach the client.** `task_attachment` and `task_comments` are declared as nested, read-only serializer
fields (`:280-281`, listed in `Meta.fields` at `:306-307`), but `Task` has no attribute by either name.
There is no `GenericRelation` defined anywhere in this codebase (`grep -rn "GenericRelation"` outside
the virtualenv returns nothing), so DRF's attempt to read `task_attachment`/`task_comments` off the
instance raises `AttributeError`, which a read-only field with no explicit default converts into
`SkipField`: the keys are silently omitted from every response, not emitted empty. The comments and
attachments a client actually needs come from the separate top-level `attachments`/`comments` keys
`TaskDetailView.get` builds independently (`:421-430`, [above](#retrieve-update-delete)), a field
being present in `Meta.fields` is not proof it is ever emitted; check what the model can actually
resolve. **`stage` and `kanban_order` are not returned by either `GET`, and are not in `Meta.fields`
on either serializer at all**. They exist only on `TaskKanbanCardSerializer`
(`:519-540`), which backs the Kanban-specific endpoints (`/api/tasks/kanban/`,
`/api/tasks/{id}/move/`), not the create/update endpoints this page covers.
