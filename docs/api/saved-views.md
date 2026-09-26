# Saved views

A saved view is a name and a set of list filters, stored for one person so they can put a filtered
list back with one tap. The six lists that [export to CSV](exports.md) take one: leads, contacts,
accounts, deals, tickets and invoices. Routes are in `backend/common/urls.py`, views in
`common/views/saved_view_views.py`, and what a view may hold in `common/saved_views.py`.

| Endpoint | Does |
| --- | --- |
| `GET /api/saved-views/` | The caller's own views; `?module=<list>` for one list's |
| `POST /api/saved-views/` | Save one |
| `GET /api/saved-views/{id}/` | One of the caller's views |
| `PATCH /api/saved-views/{id}/` | Rename it, or replace its filters |
| `DELETE /api/saved-views/{id}/` | Delete it (`204`) |

`PUT` is not offered; a view has two things to change and `PATCH` changes either.

## Private to the person who saved it

Every lookup filters on the caller's org and the caller's own profile, admins included: an admin
cannot list or open a member's views. Another person's id answers `404` on every verb, with the
same body as an id that does not exist, so ids cannot be probed (see
[Errors: Not found versus forbidden](errors.md#not-found-versus-forbidden)). The table is also
under row-level security keyed on the org.

`org` and `profile` are set by the server. A body that names either is refused with a `400`, rather
than having the key quietly dropped:

```json
{"profile": ["This is set by the server."]}
```

For personal access tokens the resource is `saved-views`: `saved-views:read` to list and open,
`saved-views:write` to save, rename and delete (see
[Tokens and API keys](tokens-and-api-keys.md#scopes)).

## The object

```json
{
  "id": "5b0c5c1e-6a0e-4f43-9d6e-1f0f3c1a2b3c",
  "module": "cases",
  "name": "Urgent, mine",
  "filters": {
    "status": ["New", "Assigned", "Pending"],
    "priority": ["Urgent"],
    "assigned_to": ["8a4e...-profile-id"]
  },
  "created_at": "2026-09-26T10:04:11.201Z",
  "updated_at": "2026-09-26T10:04:11.201Z"
}
```

`module` is the API name of the list: `leads`, `contacts`, `accounts`, `opportunities` (deals),
`cases` (tickets) or `invoices`. It is fixed once the view exists; a `PATCH` naming another list is
a `400`.

`filters` holds the list's own query parameters, each as a list of values, so a view is applied by
sending them to the list as they are: the example above is
`GET /api/cases/?status=New&status=Assigned&status=Pending&priority=Urgent&assigned_to=...`, and
the same query string to `GET /api/cases/export/` downloads it. A value sent as one string is stored
as a list of one, and blank values are dropped.

The list response carries the cap alongside the views:

```json
{"saved_views": [ { "...": "the object above" } ], "limit": 25}
```

## What a view may hold

A view is checked before it is stored, so a bad one is a `400` now rather than a list that breaks
every time the view is applied:

- **Only the parameters its list reads.** Each list's accepted set is written out in
  `LISTS` (`common/saved_views.py`), beside the function that list builds its queryset with, and a
  test reads those functions to prove the two agree. A `cf_<key>` custom-field filter is accepted on
  all six. Paging (`limit`, `offset`) and anything else is refused:
  `{"filters": ["This list does not filter by: limit."]}`.
- **Values the list can parse.** The filters are run through the list's own parameter parsing
  (the queryset is built, never evaluated), so a malformed id, date or number is refused with the
  list's own message: `{"filters": ["assigned_to: 'x' is not a valid id."]}`. A value that parses but
  matches nothing, a status that does not exist for example, is stored, exactly as the list would
  accept it.
- **Bounded.** At most 30 parameters, 50 values per parameter, and 200 characters per value. Every
  value must be text.

The ids a view holds are filter values, not references the server resolves: applying a view sends
them back to the list, which applies its own read rule, so a view cannot show anyone a record the
list would not.

## Names and the cap

A name is required, trimmed, and at most 100 characters. Names are unique per person and list,
ignoring case, so `Hot leads` and `hot leads` cannot both be saved for leads; a clash is a `400`,
also when two saves race each other past the check:

```json
{"name": ["You already have a view with this name for this list."]}
```

The same name may be used on another list, or by another person.

A person may keep at most 25 views per list (`MAX_VIEWS_PER_LIST`). Saving one more is a `400`;
renaming and changing the filters of an existing view still work at the cap:

```json
{"non_field_errors": ["You can save at most 25 views for one list."]}
```

## In the apps

The web lists and the mobile app both offer a saved-views menu on these six lists, over this API,
so a view saved on one opens on the other. A view means what the list endpoint means: on tickets,
no `status` is every status, so both clients save the open queue as its three statuses and open a
view without a status as All. Each client saves only the filters its own screen can show and set;
when a view holds a filter the screen has no control for, or several values where the screen takes
one, it still opens and the menu says how many values it left out.
