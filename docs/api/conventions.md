# Conventions

This page describes patterns that hold across most of the API: how the base URL is put together,
how a client authenticates, how list endpoints paginate and filter, and what a response body looks
like. It is deliberately drawn from the code that is actually there (a real pagination class, a
real list view, the real `REST_FRAMEWORK` settings) rather than from what a typical DRF API "should"
look like. Several of these conventions are less uniform than that would suggest, and the
differences matter to anyone integrating against them.

## Base URL

Every endpoint in this section, and every endpoint in [Endpoint index](endpoint-index.md), is
mounted under `/api/` at the Django root (`backend/crm/urls.py`: `path("api/",
include("common.app_urls", ...))`), which in turn includes `backend/common/urls.py`, the file most
of this section's page references point at, at the same, empty prefix. Individual apps are mounted
under their own segment from there, for example `path("leads/", include("leads.urls", ...))`, so a
lead endpoint is `/api/leads/…`.

Locally this is `http://localhost:8000/api/` (the port the Docker quick start publishes. Inside
the compose network the frontend reaches the same API as `http://backend:8000`. See
[Docker quick start](../getting-started/docker-quick-start.md)). In a self-hosted deployment it is
whatever host you put `DOMAIN_NAME` / `ALLOWED_HOSTS` on, still under `/api/`.

## Authentication header

Three credential types authenticate a request, tried by `common.middleware.get_company.GetProfileAndOrg`
in this order and, independently, by the DRF authentication classes in `DEFAULT_AUTHENTICATION_CLASSES`
(`backend/crm/settings.py`, `common.pat_auth.PATAuthentication`,
`rest_framework_simplejwt.authentication.JWTAuthentication`, `common.external_auth.APIKeyAuthentication`):

```
Authorization: Bearer <jwt-access-token>          # interactive sign-in (see authentication.md)
Authorization: Bearer bcrm_pat_...                 # personal access token
Token: bcrm_pat_...                                 # personal access token, alternate header
Token: <org-api-key>                                # organization API key
```

A personal access token is recognized by its `bcrm_pat_` prefix and can be presented either as a
bearer token or in the `Token` header (`backend/common/pat_auth.py`, `_extract_raw`); an organization
API key is only ever read from `Token` (`backend/common/external_auth.py`,
`APIKeyAuthentication.authenticate`). See [Authentication](authentication.md) for how a JWT is
obtained and [Tokens and API keys](tokens-and-api-keys.md) for the other two. Which identity a
request authenticates as, and which org it is scoped to, is never something the request supplies
directly. It is derived from whichever of these three the server can validate.

The two non-interactive credentials are bounded in ways a signed-in session is not. A personal
access token is limited to its `scopes` (`<resource>:<action>`, enforced in middleware before the
view runs; an empty list means unrestricted). The organization API key is read-only. Neither may
reach `/api/profile/tokens/`, `/api/org/tokens/` or `/api/org/api-key/` at all: credential
management requires an interactive sign-in. See
[Tokens and API keys](tokens-and-api-keys.md#scopes).

One thing the generated OpenAPI schema does *not* reflect accurately: a large minority of
operations (112 of the schema's 350, about a third) declare an `org` header parameter
(`organization_params_in_header`, defined in `backend/common/swagger_params.py` and copied into the
`swagger_params.py` of six other apps: `leads`, `cases`, `opportunity`, `contacts`, `accounts` and
`tasks`). Nothing in the
request-handling code reads a header by that name. Org context comes only from the JWT's `org_id`
claim, the PAT's owning profile, or the API key's associated org, as above. Sending an `org` header
has no effect; do not rely on it.

## Pagination

List endpoints paginate with `common.pagination.CappedLimitOffsetPagination`, a
`LimitOffsetPagination` (`DEFAULT_PAGINATION_CLASS` in `backend/crm/settings.py`), using the query
parameters `limit` and `offset`. The default page size is `PAGE_SIZE = 10` from the same settings
block. `limit` is capped at 1000: a larger value is clamped to 1000 rather than refused, and the
response shape does not change. A few lists set a lower cap of their own (the audit log, automation
runs and the help center, 100).

That default pagination class is not applied automatically, though. There is no
`generics.ListAPIView` anywhere in this codebase. Every list endpoint is a hand-written `APIView`
that mixes `CappedLimitOffsetPagination` in directly and calls `self.paginate_queryset(...)` itself, for
example:

```python
class LeadListView(APIView, CappedLimitOffsetPagination):
    ...
    def get_context_data(self, **kwargs):
        ...
        results_leads_open = self.paginate_queryset(
            queryset_open.distinct(), self.request, view=self
        )
```

(`backend/leads/views/lead_views.py:51`, `:167`.) The same shape repeats in `common/views/user_views.py`,
`common/views/team_views.py`, `common/views/document_views.py`, `common/views/tags_views.py`,
`cases/views.py`, `contacts/views.py`, `accounts/views.py`, `opportunity/views/opportunity_views.py`,
`tasks/views/task_views.py` and `invoices/api_views.py`, among others.

Because pagination is applied by hand, the response envelope is a per-endpoint choice, not a
guarantee. The common case in this codebase is that a view builds its own shape rather than calling
DRF's `get_paginated_response()`: `GET /api/leads/` reports pagination per section, since the list
is split into open and closed leads:

```python
context["open_leads"] = {
    "leads_count": self.count,   # LimitOffsetPagination's own running total
    "open_leads": open_leads,     # this page's serialized results
    "offset": offset,             # offset to request for the next page, or None on the last page
}
```

(`backend/leads/views/lead_views.py:180-184`.) But `get_paginated_response()`, and with it, DRF's
standard `count`/`next`/`previous`/`results` shape, does appear: `GET /api/cases/solutions/`
(`SolutionListView`, `backend/cases/solution_views.py:85`, routed at `backend/cases/urls.py:59`)
calls it directly and adds one extra key on top:

```python
response = self.get_paginated_response(serializer.data)
response.data["totals"] = counts
return response
```

(`backend/cases/solution_views.py:178-180`.) So both shapes are real. Treat the exact pagination
field names, and whether the standard DRF envelope or a hand-rolled one is used, as per-endpoint:
`limit`/`offset` as query parameters are consistent, but where the resulting count, next-offset and
results end up in the response body is not; check the endpoint you're calling.

## Filtering and search

There is no shared filter backend; `DEFAULT_FILTER_BACKENDS` is not set in `REST_FRAMEWORK`, and
no view uses `django_filters`. Each list view reads `request.query_params` directly inside its own
`get`/`get_context_data` and applies whatever filters it defines. `GET /api/leads/`
(`backend/leads/views/lead_views.py:105-160`) is representative of how rich these can get:

| Query parameter | Matches |
| --- | --- |
| `name` | `first_name` or `last_name`, case-insensitive contains |
| `salutation` | case-insensitive contains |
| `source` | exact |
| `assigned_to` | repeatable; `assigned_to__id__in` |
| `status` | exact |
| `tags` | repeatable; `tags__id__in` |
| `city` | case-insensitive contains |
| `email` | case-insensitive contains |
| `rating` | exact |
| `search` | `first_name`, `last_name`, `company_name` or `email`, case-insensitive contains, OR'd together |
| `created_at__gte`, `created_at__lte` | date range |
| `close_date__gte`, `close_date__lte` | date range |
| `cf_<key>` | custom field `<key>` equals the given value, parsed by the field's type (see below) |

For example, `GET /api/leads/?status=assigned&source=partner&search=acme`; `status` and `source`
are exact matches against `LEAD_STATUS` (`backend/common/utils.py`: `assigned`, `in process`,
`converted`, `recycled`, `closed`) and a source key from the org's
[lead sources](leads.md#lead-sources), so a filter value outside those exact strings matches nothing
rather than erroring. Other list endpoints define
their own, generally smaller, filter sets in the same style. There is no single reference for
"every filter the API supports"; read the view (or watch for a dedicated section on that resource's
page, where one exists) rather than assuming this table applies elsewhere.

`cf_<key>` works the same way on every list with custom fields (leads, contacts, accounts, deals,
tickets and the tickets you watch, tasks, invoices, estimates and recurring invoices), through
`common.custom_fields.custom_field_filter`:

- Only the org's active fields for that list count. A `cf_` key that names no active field is
  ignored, so a saved view on a field an admin has since turned off still opens, without that
  filter.
- The value is parsed by the field's type, so number and checkbox filters match: `cf_bedrooms=3`
  matches a stored `3.0`, and `cf_pets=true` and `cf_pets=false` each match their own records
  (`yes`/`no`, `1`/`0` and `on`/`off` work too). A date is `YYYY-MM-DD`; dropdown and text values
  are an exact match. Before 1.36.0 the raw text was compared, so number and checkbox filters
  matched nothing.
- A value that does not parse is a `400` naming the parameter, for example
  `{"cf_bedrooms": ["must be a number"]}`.
- The key is always one key of `custom_fields`: `cf_rooms__gte=3` matches a field whose key is
  `rooms__gte`, never a lookup on `rooms`.

### Global search

`GET /api/search/?q=<text>` is the web app's search palette. A query of fewer than two characters
answers no results, and each type gives at most six matches:

```json
{
  "query": "acme",
  "results": [
    {
      "type": "account",
      "id": "6f1d2c3e-8a4b-4c5d-9e6f-7a8b9c0d1e2f",
      "title": "Acme Corp",
      "subtitle": "ops@acme.example",
      "group": "Accounts"
    }
  ]
}
```

- The types searched come from the object registry (`common/object_registry.py`): each entry
  whose `search` part names a function, among the entries the org has, so a switched-off module
  is never searched. The built-in types are `lead`, `contact`, `account`, `deal`, `ticket` and
  `invoice`. An installed app that adds registry entries through `register_hook` adds its types
  the same way.
- Each function starts from its module's read rule, the one the record's detail view uses, so a
  hit is always a record the caller can open.
- Knowledge base articles (`type` `solution`) are searched while that module is on. They are not
  record objects, so they come from a short named list in `common/search_views.py`, and every
  member finds every article.
- `group` is the type's plural label: `Leads`, `Contacts`, `Accounts`, `Deals`, `Tickets`,
  `Invoices`, an installed object's own label, or `Knowledge base`. Hits come grouped in registry
  order, then the knowledge base. `group` and this order are new in 1.36.0; before, deals came
  second.

## Tags across record types

`GET /api/tags/` returns each tag with `usage`, a count per kind of holder, and a top-level
`usage_labels` that names each key:

```json
{
  "tags_count": 1,
  "tags": [
    {"id": "...", "name": "VIP", "is_active": true, "usage": {"leads": 2, "contacts": 0, "opportunities": 1}}
  ],
  "usage_labels": {"leads": "Leads", "contacts": "Contacts", "opportunities": "Deals"},
  "totals": {"count": 1, "active": 1, "unused": 0}
}
```

(Shortened: every row carries every key of `usage_labels`, in the same order.)

- The keys are, first, each object registry entry with a `tags_key`: `leads`, `contacts`,
  `accounts`, `opportunities` (deals), `cases` (tickets), `tasks`, `projects`, and any an installed
  app's entries add. Then the holders that are not records, from `NON_OBJECT_TAG_HOLDERS` in
  `common/views/tags_views.py`: `api_settings`, `solutions`, `web_forms`, `lead_intakes` and
  `macros`.
- A label is the entry's plural label, or the holder's own (`API keys`, `Articles`, `Web forms`,
  `Lead webhooks`, `Macros`). Show `usage_labels[key]`, falling back to the key itself.
  `usage_labels` is new in 1.36.0.
- Counts cover every record in the org, not only those the caller can open.
- A switched-off module's keys leave both `usage` and `usage_labels`. `totals.unused` still counts
  their records, so a tag used only there is never reported as unused.
- `POST /api/tags/{id}/merge/` with `{"into": "<tag id>"}` (admins only) moves every holder's
  records, off modules included, onto `into`, walking each model once, and then archives the
  source tag.

## Response shape

There is no single response envelope used across the whole API. Three shapes recur, and which one
a given endpoint uses is part of that endpoint's own contract:

**Mutations wrapped in an `error` flag.** The large majority of create/update/delete endpoints
return `{"error": bool, ...}`, checked explicitly rather than relying on the HTTP status code alone,
for example a successful lead create (`backend/leads/views/lead_views.py:402-404`):

```json
{"error": false, "message": "Lead Created Successfully", "id": "<uuid>"}
```

and a failed one (`backend/leads/views/lead_views.py:406-408`):

```json
{"error": true, "errors": {"email": ["Another lead in this organisation already uses that email address."]}}
```

The same `{"error": false, "tokens": [...]}` shape is used by the personal-access-token list in
[Tokens and API keys](tokens-and-api-keys.md#personal-access-tokens).

**Bare serializer data.** Some newer endpoints skip the `error` wrapper entirely and return
`serializer.data` (or the created/updated object's serialization) directly, relying on
`serializer.is_valid(raise_exception=True)` and the HTTP status code to signal failure. For
example `MacroListCreateView.post` returns `Response(MacroSerializer(macro).data, status=201)`
(`backend/macros/views.py:136`).

**Single-object detail responses nest the record, not return it bare.** `GET /api/leads/{id}/`
does not return a `Lead` object at the top level; it returns the record under `lead_obj` alongside
related data fetched in the same call:

```json
{
  "lead_obj": {"id": "...", "first_name": "...", "...": "..."},
  "attachments": [...],
  "comments": [...],
  "users_mention": [...],
  "assigned_data": [...]
}
```

(`backend/leads/views/lead_views.py:412-517`.) Don't assume a detail endpoint returns its resource
at the top level of the response; check the endpoint.

## Content types

`REST_FRAMEWORK` sets no `DEFAULT_PARSER_CLASSES`, so DRF's own defaults apply: a request body may
be `application/json`, `application/x-www-form-urlencoded`, or `multipart/form-data`
(`JSONParser`, `FormParser`, `MultiPartParser`, in that order). `multipart/form-data` is what every
endpoint that accepts a file upload alongside other fields needs. For example `POST /api/leads/`
reads an optional `lead_attachment` file straight off `request.FILES` in the same call that creates
the lead (`backend/leads/views/lead_views.py:330`), so a lead with an initial attachment is created
with one multipart request rather than a create-then-upload round trip.

`REST_FRAMEWORK` sets no `DEFAULT_RENDERER_CLASSES` either, so both of DRF's defaults are live:
`JSONRenderer` for `application/json` (what every example on this page assumes, and what every
client integration should send `Accept: application/json` for) and `BrowsableAPIRenderer` for
`text/html`: request the same endpoint with a browser's default `Accept` header and DRF's
interactive HTML form is what comes back instead of JSON.
