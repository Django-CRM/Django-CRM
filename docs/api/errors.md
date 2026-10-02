# Errors

How to tell a failed request from a successful one, and how to read the body of a failed one.
`REST_FRAMEWORK["EXCEPTION_HANDLER"]` in `backend/crm/settings.py` is set to DRF's own
`rest_framework.views.exception_handler` (unmodified, no custom subclass), so an exception DRF
itself raises (a bad permission check, a missing object, an unhandled `ValidationError`) comes back
in DRF's own default shape. A view that catches its own errors and builds a `Response` by hand,
which most views in this codebase do, can and often does return something else. Both are documented
below with real examples, because a client has to handle both.

## Status codes

`200` for a successful read or a successful write. Many mutation endpoints return `200` rather
than `201` even on creation, wrapping the result in the `{"error": false, ...}` shape described in
[Conventions](conventions.md#response-shape) rather than relying on the status code alone (see
`POST /api/leads/`, `backend/leads/views/lead_views.py:402-404`). Some do use `201`: personal access
token creation, for instance, returns `201` (`backend/common/views/pat_views.py:69`). Neither is
"more correct" than the other here, check the endpoint.

Beyond that: `400` for a request the server understood but rejected (validation, or a
request-shaped error like a missing required field); `401` for a request with no valid credentials
at all; `403` for a request that authenticated fine but isn't allowed to do this; `404` for a
resource that doesn't exist, or that exists but the caller must not be able to distinguish from not
existing. See [Not found versus forbidden](#not-found-versus-forbidden); `502` where a view depends
on an external service and that service is unreachable, for example the Google token exchange in
[Google OAuth](authentication.md#google-oauth-web) (`backend/common/views/auth_views.py:110-114`).
`429` comes only from the anonymous endpoints that name their own throttles: web form submit, the
public help center, the calendar feed, and the magic-link and portal sign-in requests.
`REST_FRAMEWORK` sets no `DEFAULT_THROTTLE_CLASSES`, so no authenticated endpoint returns it. The
sign-in requests also cap tokens per address (five an hour), and that limit answers with their
normal `200` rather than a `429`, so the response can't be used to distinguish "rate limited" from
"sent" from "invalid address". See [Magic links](authentication.md#magic-links).

## Validation errors

The dominant pattern in this codebase is not DRF's automatic one. Most views call
`serializer.is_valid()` without `raise_exception=True`, check the boolean themselves, and build the
error response by hand as `{"error": true, "errors": serializer.errors}`. This exact shape appears
well over 200 times across the backend. A `validate_<field>` failure lands under that field's key,
DRF's normal behavior:

```python
def validate_email(self, value):
    ...
    if duplicates.exists():
        raise serializers.ValidationError(
            "Another lead in this organisation already uses that email address."
        )
    return value
```

(`backend/leads/serializer.py:107-133`, `LeadCreateSerializer.validate_email`. The comment above it
explains this exists because a duplicate email used to reach the database as an unhandled
`IntegrityError` and come back as an opaque `500`.) A `POST /api/leads/` with a duplicate email
returns:

```json
{"error": true, "errors": {"email": ["Another lead in this organisation already uses that email address."]}}
```

A `validate()` failure that isn't tied to one field lands under DRF's default
`non_field_errors` key (`NON_FIELD_ERRORS_KEY` is not overridden anywhere in this codebase), still
nested the same way: `{"error": true, "errors": {"non_field_errors": ["..."]}}`.

A smaller, newer set of views instead call `serializer.is_valid(raise_exception=True)`, for
example `MacroListCreateView.post` (`backend/macros/views.py:123`), and let DRF's exception handler
build the response itself, with no `error`/`errors` wrapper at all:

```json
{"email": ["Another lead in this organisation already uses that email address."]}
```

Both shapes exist in the API today. Check the specific endpoint's page (or the view, if it isn't
documented yet) rather than assuming one; do not assume the presence or absence of an `error` key
tells you whether the request succeeded. The status code is what does that.

## Authorization errors

Authentication and authorization failures go through DRF's permission machinery unmodified, so they
always come back as `{"detail": "<message>"}`, whether the message is DRF's own default or one this
codebase set:

```json
{"detail": "Authentication credentials were not provided."}
```

```json
{"detail": "Organization context is required. Please login again."}
```

The second is `HasOrgContext.message` (`backend/common/permissions.py:11-41`), one of the two
permission classes almost every org-scoped view lists: `permission_classes = (IsAuthenticated,
HasOrgContext)`, and often a role check on top such as `IsOrgAdmin`
(`backend/common/permissions.py:44-59`, `"You must be an organization administrator to perform this
action."`). Whether a failure comes back as `401` or `403` is not decided by which permission class
failed. DRF's `APIView.permission_denied()` raises `NotAuthenticated` (`401`) whenever no
authenticator on the request actually succeeded, and `PermissionDenied` (`403`, carrying the failing
permission class's `message`) whenever one did but the permission check itself failed. In practice:
no credentials, or invalid ones → `401`; a valid, authenticated session that isn't allowed to do
this → `403`.

## Not found versus forbidden

The rule that holds everywhere an object is looked up by id: a record outside the caller's org
returns `404`, the same as a record that never existed at all, never `403`. Every such lookup
filters on `org=request.profile.org` (or the RLS-protected queryset equivalent) before matching the
id, so a request for another org's record simply finds nothing;
`get_object_or_404(Macro, pk=pk, org=request.profile.org)` (`backend/macros/views.py:148`, repeated
at `:168` for the plain `GET`) is one of dozens of examples of the same shape.
`invoices/permissions.py` states the reasoning explicitly in its own module docstring:

```
1. Does this record exist *in the caller's org*?  -> 404 if not, so that a
   caller in another org cannot probe for record IDs.
2. May this caller act on it?                     -> 403 if not.
```

(`backend/invoices/permissions.py:5-9`.) A `403` response inherently confirms the record exists.
It's not possible to be forbidden from acting on nothing, so reserving `403` for "exists, but you
may not do this" and `404` for everything else is what stops an id becoming an oracle a caller in
another org can probe with.

Within a single org, whether a permission failure still returns `403` or drops all the way to `404`
depends on what the resource is. Invoices and estimates keep step 2 above as a real `403`: an org
member who is neither the record's creator, an assignee, nor an admin is refused with
`{"error": true, "message": "Permission denied"}` (`backend/invoices/permissions.py:80-84`). A
colleague can tell the invoice exists, just not read or change it. A personal (as opposed to
org-scope) macro goes further and hides itself completely from everyone except its owner and org
admins, `404` even for a same-org caller, precisely to avoid the invoice case's disclosure:

```python
if macro.scope == Macro.SCOPE_PERSONAL and macro.owner_id != request.profile.id:
    # A personal macro you don't own is invisible to you. The list and
    # GET hide it (404). The write verbs must not confirm it exists via
    # a 403 either, or the id space leaks which rows are somebody else's
    # personal macros. Mirror the GET: 404.
    return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
```

(`backend/macros/views.py:156-164`.) Both are deliberate, documented choices for the resource in
question, not an inconsistency to paper over, when you add an authorization check to a new
endpoint, decide which of the two your resource needs (does an org-mate being aware this record
exists leak anything?) rather than defaulting to whichever is easier to write.
