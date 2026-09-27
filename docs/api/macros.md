# Macros

A macro (a "saved reply" in the phone app) is a canned reply for the ticket composer, optionally
carrying actions that change the ticket. Routes are in `backend/macros/urls.py`, views in
`backend/macros/views.py`, the placeholder renderer in `backend/macros/render.py`. Every route is
under `/api/macros/` and needs `(IsAuthenticated, HasOrgContext)`. See [Conventions](conventions.md)
and [Errors](errors.md) for the shapes assumed here.

## Scope and visibility

A macro is either `org` (every member of the org can see and use it; only an admin may create,
edit or delete it) or `personal` (visible to its owner only). A non-admin's `POST` with
`scope: "org"`, or an edit of an org macro, is a `403`:

```json
{"error": "Only admins can manage org-scope macros."}
```

Somebody else's personal macro answers `404` on every verb, exactly like an id that does not exist,
so the id space does not reveal whose private macros exist. An org macro a non-admin tries to edit
answers `403`, because they can already see it.

## List, create, update, delete

`GET /api/macros/` lists the macros the caller can see, newest edit first, and takes `active`
(`true` or `false`) and `search` (title or body, case-insensitive). The response is
`{"results": [...], "totals": {...}, "placeholders": [...]}`; `totals` counts the whole visible set
whatever the filters (`count`, `org`, `personal`, `inactive`, `with_unknown_placeholders`), and
`placeholders` is the list the renderer supports.

`POST /api/macros/` creates one; `GET`, `PUT`, `PATCH` and `DELETE /api/macros/{id}/` read and
change it. `DELETE` turns an org macro off (`is_active: false`) and removes a personal one.

| Field | Type | Notes |
| --- | --- | --- |
| `title` | string, max 255 | Required |
| `body` | text | May be empty when the macro carries at least one action |
| `scope` | `org` or `personal` | Defaults to `org`, which needs an admin |
| `is_active` | boolean | Defaults `true`. An inactive macro cannot be rendered or applied |
| `set_status` | a ticket status, or `""` | Any status except `Duplicate`, which only a merge sets. `""` means no change |
| `set_priority` | a ticket priority, or `""` | `""` means no change |
| `set_assignees` | list of profile ids | Replaces the ticket's assignees when applied |
| `add_tags` | list of tag ids | Added to the ticket's own tags when applied |

`owner`, `owner_name`, `usage_count`, `unknown_placeholders`, `created_at` and `updated_at` are
read-only. `set_assignees_details` and `add_tags_details` name each id (`id`, `email` and `name`,
or `id`, `name` and `color`) with its `is_active`, so a client can show an assignee deactivated
since, or a tag archived since, rather than dropping it.

Validation, each a `400`:

- **A macro has to do something.** No body and no action is refused with
  `{"body": ["A macro needs a body, an action, or both."]}`.
- **`set_assignees` and `add_tags` resolve in the caller's org only.** Another org's id is refused
  like an id that does not exist.
- **Only active members and active tags may be added.** A deactivated member or an archived tag the
  macro already carries is kept on save, because both clients resend the whole list every time.

## Render

`POST /api/macros/{id}/render/` with `{"case_id": "<uuid>"}` expands the placeholders
(`%customer_name%`, `%customer_email%`, `%case_id%`, `%case_subject%`, `%agent_name%`,
`%agent_email%`, `%org_name%`) against the ticket and returns `{"rendered_body": "..."}`. It counts
one use of the macro. A ticket the caller may not open, another org's ticket and a malformed id are
the same `404`, because the rendered text carries the ticket's subject and its contact's name and
email. An unknown placeholder is left in the text as written.

## Apply

From django-crm 1.13.0, `POST /api/macros/{id}/apply/` applies the macro's actions to a ticket:

```json
{"case_id": "<uuid>", "only": ["status", "tags"]}
```

`only` is optional. Its keys are `status`, `priority`, `assignees` and `tags`; it narrows the macro's
actions to the ones listed, which is how a client applies only the actions the agent kept. Omitted,
every action the macro carries is applied.

The write goes through `cases.updates.update_case`, the same function the ticket `PATCH` and the
bulk edit use, so every gate those run runs here too: the merged-ticket lock, the close gate (the
approval a matching `pre_close` rule asks for), `Duplicate` only by merge,
assignees and tags limited to active rows in the org, and the email to anyone newly assigned.
Assignees replace the ticket's; tags are added to the ticket's own. A `Closed` status takes the
ticket's existing `closed_on`, or today in the org's timezone when it has none. Applying does not
count as a use; rendering does.

Refusals, in the order they are checked:

| Status | When |
| --- | --- |
| `404` | The macro is missing, in another org, or somebody else's personal macro |
| `400` | The macro is inactive: `{"error": "Macro is inactive."}` |
| `400` | The macro carries no actions: `{"error": "This macro has no actions to apply."}` |
| `400` | `only` is not a list of the four keys: `{"error": "only must be a list of: status, priority, assignees, tags."}` |
| `400` | None of the keys in `only` is on this macro: `{"error": "None of the chosen actions are on this macro."}` |
| `400` | `case_id` is missing: `{"error": "case_id is required."}` |
| `404` | The ticket is one the caller may not open, in another org, or a malformed id |
| `403` | The caller may open the ticket but not change it (only an admin, its creator or an assignee may), as on the ticket `PATCH` |
| `400` | A gate refused the change: `{"error": true, "errors": {...}}`, the `PATCH`'s own errors, with nothing written |

Success is `200`:

```json
{
  "error": false,
  "message": "Macro applied",
  "applied": ["status", "tags"],
  "skipped": []
}
```

`applied` lists the actions that were applied. `skipped` lists `{"action", "reason"}` for an action
that had nothing left to apply: `assignees` when everyone the macro assigns is now deactivated (the
ticket keeps its assignees rather than being left with nobody), and `tags` when every tag it adds is
archived. When everything was skipped, nothing is written and `message` is `"Nothing was applied"`.

### In the clients

In the ticket composer on the web and the phone, picking a macro that has a body inserts the
rendered text as before and shows its actions as chips under **Also on send**. Each chip can be
removed. The actions left on apply right after the reply posts, with `only` set to them; if the
apply is refused, the reply has still gone out and the refusal is shown. A macro with no body shows
**Apply** instead, which applies its actions at once.
