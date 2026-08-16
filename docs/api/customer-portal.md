# Customer portal

The endpoints a **customer** calls, mounted under `/api/portal/`. Everything else in this section
is the internal API, which your own staff and scripts use. These two are separate credential
realms and neither one's token works against the other; see
[Architecture: Customer portal credentials](../architecture/authentication.md#customer-portal-credentials)
for why the separation is enforced in three independent places rather than one.

A portal caller is a `Contact` row, not a `User` and not a `Profile`. They have no password, no
Google account, no role, and no presence in your org's member list. What they can reach is the
support cases they are named on, plus the help articles their org has approved and published, and
nothing else.

!!! warning "Two different things are called "portal" in this codebase"
    The **invoice and estimate portal** (`/api/public/…`) is a tokenised link emailed to a
    customer, where holding the URL is the entire credential and there is no sign-in. The
    **customer portal** documented here is a sign-in: the customer proves they control the email
    address, and receives a session token. They share a word and nothing else. See
    [Glossary: Portal token](../reference/glossary.md#portal-token) for the two token types.

## Signing in

Sign-in is per organization. The org id is in the path because the customer arrived from an email
that org sent them, and a sign-in attempt only ever resolves contacts in that one org. There is
deliberately no org picker: `Contact` is one row per org, so the same address at two companies is
two unrelated relationships, and showing a chooser would disclose one tenant's customer list to
another.

### Request a code

`POST /api/portal/login/{org_id}/request/` (`PortalLoginRequestView`,
`backend/common/views/portal_auth_views.py`). Public: `permission_classes = []`,
`authentication_classes = []`.

```json
{"email": "customer@example.com"}
```

This endpoint **always returns `200`** with the same body, whether the address belongs to a contact
in that org, belongs to a contact in a *different* org, is not a contact at all, failed email
validation, or was rate limited:

```json
{"message": "If this email is valid, you will receive a sign-in code."}
```

There is no `400` to handle. The uniformity is the point: any difference in status, body, or
observable work would let a stranger test which of their competitors' customers you do business
with. A six digit code is emailed, stored only as a PBKDF2 hash, and expires after 10 minutes.
Five codes per contact per hour, matching the internal magic-link limit. Each new request retires
that contact's previous unused code, so a forwarded older email stops working the moment the real
customer asks again.

### Exchange it for a session

`POST /api/portal/login/{org_id}/verify/` (`PortalLoginVerifyView`):

```json
{"email": "customer@example.com", "code": "123456"}
```

```json
{
  "access_token": "<jwt>",
  "contact": {"id": "<uuid>", "name": "Riley Okonkwo", "email": "customer@example.com"},
  "org": {"id": "<uuid>", "name": "Acme"}
}
```

The row is read under `select_for_update`, so two concurrent guesses cannot both observe the same
attempt count, and it is burned after 5 failed attempts even if the correct code arrives
afterwards. The contact's `is_active` is re-checked at this moment rather than trusted from when
the code was minted.

Failure is a single `400` for every case (expired, already used, wrong code, unknown email, contact
deactivated, org not found):

```json
{"error": "That code is not valid any more. Request a new one."}
```

## Using the session token

Send it as `Authorization: Bearer <access_token>`. It is valid for **24 hours**.

**There is no refresh token.** When it expires the customer requests another code. That is a
low-friction action for a surface visited a few times per ticket, and it keeps token rotation,
refresh revocation, and privilege-change invalidation out of this surface entirely.

The token carries `typ: "portal"`, `org_id` and `contact_id`, and deliberately **no `user_id`**, so
SimpleJWT's own `get_user` refuses it. Presenting it anywhere outside `/api/portal/` is refused by
middleware before any view runs:

```json
{"error": "This credential is not valid for this endpoint."}
```

with status `403`. Deactivating the contact invalidates the token on the next request, because the
authentication class re-reads `is_active` every time rather than trusting the claim.

## Cases

All four endpoints below require a portal token. Every query is filtered by both the org from the
token and the contact from the token.

### List

`GET /api/portal/cases/`

```json
{
  "cases": [
    {
      "id": "<uuid>",
      "name": "Login broken",
      "status": "New",
      "priority": "High",
      "created_at": "2026-08-15T10:04:11Z",
      "closed_on": null
    }
  ],
  "cases_count": 1
}
```

Newest first, `LimitOffsetPagination` (`limit` and `offset` query parameters). Optional `?status=`
takes one of the case statuses; an unrecognised value is a `400` rather than a silently empty list.

**Scope: the contact's own cases, not the account's.** A colleague at the same company cannot read
a ticket they were not named on. Widening this later is a migration; narrowing it later would be a
breach notification.

### Create

`POST /api/portal/cases/`

```json
{"name": "Printer on fire", "description": "It started this morning.", "priority": "High"}
```

Returns `201` with `{"case": {…}}` in the detail shape below. Three fields, and that is the whole
serializer. `org`, `status` and the contact link are set by the server, and `created_by` is left
null because a customer is not a `User`. A body that also supplies `org`, `status` or `contacts` is
ignored rather than honoured, because those fields are not declared on the serializer at all.

An empty or whitespace-only `name` is a `400`.

### Detail

`GET /api/portal/cases/{id}/`

```json
{
  "case": {
    "id": "<uuid>",
    "name": "Login broken",
    "status": "New",
    "priority": "High",
    "description": "Cannot get past the sign-in screen.",
    "created_at": "2026-08-15T10:04:11Z",
    "closed_on": null
  },
  "comments": [
    {
      "id": "<uuid>",
      "comment": "We are looking into it now.",
      "commented_on": "2026-08-15T11:20:03Z",
      "author": "Support",
      "is_mine": false
    }
  ]
}
```

Three things this response deliberately does not contain:

- **Internal notes.** `is_internal=False` is applied in the query, not in the serializer, so an
  agent's private note is never loaded and cannot be leaked by a later change to how the response
  is rendered.
- **Which agent replied.** Every comment from your side is attributed to `"Support"`. Which
  colleague wrote it is internal information; that support replied is what the customer needs.
- **Everything else about the case.** Assignment, teams, watchers, SLA counters and tags are absent
  because the serializer never declares them, not because they are stripped out afterwards.

A case that does not exist and a case belonging to somebody else both return the same `404`
`{"error": "Case not found"}`, so an id cannot be probed to learn whether it belongs to a colleague.

### Reply

`POST /api/portal/cases/{id}/comment/`

```json
{"comment": "Still seeing the error on my phone."}
```

Returns `201` with the comment in the shape above, where `is_mine` is `true` and `author` is the
customer's own name. Maximum 10,000 characters; empty or whitespace-only is a `400`.

`is_internal` is **not** a field on this serializer. A customer cannot write into your team's
private thread, and sending `"is_internal": true` in the body has no effect.

On the agent side the reply arrives as an ordinary comment with `commented_by` null and
`commented_by_contact` naming the customer, which is the same shape an inbound email reply produces
(see [Cases](cases.md)). It counts as a customer reply everywhere that distinction matters:
first-response SLA stamping, and auto-reopen on a closed ticket.

## Help articles

The customer-readable half of the knowledge base. Three endpoints, all read-only, all requiring a
portal token.

**What a customer can see is narrower than what an agent can see.** The agent suggester
(`GET /api/cases/{id}/solution-suggestions/`, see [Cases](cases.md)) shows anything published; these
endpoints require the article to be **both `is_published` and `status="approved"`**, and to belong
to the token's org. `PortalBaseView._published_articles` is the single place that decides this, the
way `_my_cases` is for cases.

Requiring both is not belt and braces for its own sake. The published-implies-approved rule is
enforced on write by `SolutionSerializer.validate`, but it arrived after the model did, so rows
created before it can be published drafts. Those stay invisible to customers, and the first edit to
such a row repairs the pair.

### List

`GET /api/portal/articles/`

```json
{
  "articles": [
    {
      "id": "<uuid>",
      "title": "Resetting your password",
      "updated_at": "2026-08-16T09:12:00Z"
    }
  ],
  "articles_count": 1
}
```

Ordered by title, `LimitOffsetPagination`. Optional `?search=` matches the title or the body,
substring, case-insensitive. The search is narrowed from the same visible set as the list, never
applied to `Solution.objects`, so it cannot become a second and wider way in.

### Detail

`GET /api/portal/articles/{id}/`

```json
{
  "article": {
    "id": "<uuid>",
    "title": "Resetting your password",
    "description": "Open Settings, choose Security, then Reset password.",
    "updated_at": "2026-08-16T09:12:00Z"
  },
  "related": [
    {"id": "<uuid>", "title": "Changing your billing address"}
  ]
}
```

Four fields, and that is the whole projection. Absent on purpose: `status` and `is_published` (the
queryset has already decided the customer may read this, so repeating the decision in the payload
only creates something to leak), `created_by` (which colleague wrote the answer is internal, the
same rule that renders every agent comment as `"Support"`), and `linked_cases`, which would hand
this customer other customers' case ids.

`related` is computed from the **agents' tags** and never contains them. The tag vocabulary is
shared with leads and deals and reads like "At Risk" and "VIP", so it selects the rows and does not
appear in the response: ids and titles only, capped at 3, excluding the article itself. It is built
from `_published_articles`, so a shared tag cannot reach a draft or another org's article.
Relatedness narrows the visible set; it never widens it.

A draft, an unpublished article, and another org's article all return the same `404`
`{"error": "Article not found"}` as an id that never existed.

### Suggestions

`GET /api/portal/articles/suggest/?q=`

```json
{
  "articles": [
    {"id": "<uuid>", "title": "Resetting your password", "snippet": "Open Settings, choose…"}
  ]
}
```

Deflection for the new-request form: the customer types a summary, and this answers with up to
three matching articles before the request is sent. Query-driven rather than case-driven, because
at that moment no case exists yet, which is why it is a separate endpoint from the agent suggester.

A blank or missing `q` returns an empty list. There is no seeding from recent articles, unlike the
agent side: an agent scanning the newest answers is doing their job, while a customer shown three
unrelated articles just learns to ignore the panel.

## Notifications

When an agent posts a public reply, or a case changes status, every contact named on that case who
has an email address is notified. The contact who caused the change is excluded, so nobody is
emailed about their own reply, and an internal note never triggers anything.

The emailed link carries the case id and the org id, and **no credential**. Forwarding the email
does not forward access; the recipient still has to sign in. That is the difference between this
and the invoice and estimate emails, where the token in the URL is the whole credential.

`FRONTEND_URL` is the base for that link. See
[Environment variables](../reference/environment-variables.md).
