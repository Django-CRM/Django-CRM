# Glossary

Terms used throughout this documentation that are either specific to BottleCRM's own model, or
that sound generic but resolve to a particular model or field in this codebase. Each entry links to
the page that covers the concept in full; this page is the short definition, not the whole story.

## Terms

### Organization

`Org` (`backend/common/models.py`, db table `organization`), the tenant boundary. Every org-scoped
record carries an `org` foreign key to this model, either declared directly or inherited from
`BaseOrgModel`, and Row-Level Security enforces that a request in one organization's context never
returns another organization's rows. Carries a `default_currency`/`default_country` locale pair, the
[organization API key](#organization-api-key), and, once a vertical pack has been applied,
`vertical` and `terminology` fields that are **descriptive only**: the model's own comment states
they are never used in a permission check or a queryset filter.

### Profile

`Profile` (`backend/common/models.py`), a user's membership in one organization: a `(user, org)`
pair, unique together, carrying that membership's role (`ADMIN`/`USER`, from `ROLES`),
`is_organization_admin`, `has_sales_access`/`has_marketing_access`, and `is_active`. One `User` can
hold a separate `Profile`, with a separate role, in each organization they belong to.
`request.profile`, set by the `GetProfileAndOrg` middleware from the JWT on every request, is what
every permission check and org filter in this codebase is written against, never `request.user`
directly. See [Permissions and roles](../architecture/permissions-and-roles.md).

### Tenant

Not a distinct model or field in this codebase; the general term this documentation (and the wider
industry) uses for "one organization's data, isolated from every other organization's." In
BottleCRM, an organization *is* the tenant boundary. See
[Multi-tenancy and RLS](../architecture/multi-tenancy-and-rls.md).

### RLS context

`app.current_org`: the PostgreSQL session variable Row-Level Security policies read to decide
which rows a query is allowed to see. `CONTEXT_VARIABLE` in `backend/common/rls/__init__.py` names
it; the `RequireOrgContext` middleware (`backend/common/middleware/rls_context.py`) sets it once per
request, via `SELECT set_config('app.current_org', <org_id>, false)`, and clears it afterward. The
org id it sets comes from `request.org`, one hop removed from `request.profile.org`: the earlier
`GetProfileAndOrg` middleware (`backend/common/middleware/get_company.py`) is what actually resolves
the org, from the JWT, a personal access token, or an organization API key, depending on which
authentication path the request took, and assigns it to `request.org` directly (as well as setting
`request.profile`); `RequireOrgContext` only reads `request.org`, not `request.profile.org`, when
deciding what to set. A request with no context set sees zero rows from every RLS-protected table.
The fail-safe every isolation policy's `NULLIF(current_setting(...), '')` clause is built around.
See [Multi-tenancy and RLS](../architecture/multi-tenancy-and-rls.md).

### Vertical pack

A small, declarative JSON manifest (`backend/packs/*.json`, validated by
`backend/common/packs/schema.py`) that shapes an *already-existing* organization toward one
industry: a starter pipeline (with stages) for leads, cases, and tasks; custom field definitions;
tags; products; terminology overrides (for example relabeling "Lead" as "Enquiry"); and a short list
of named sample records that reference each other within the manifest. Applying a pack is a
one-time, idempotent action, distinct from `seed_data`. See
[Management commands](management-commands.md#seed_data), which generates a large randomized
dataset instead of a curated one. See
[Demo data and packs](../getting-started/demo-data.md#vertical-packs).

### Portal token

`PortalAccessToken` (`backend/common/models.py`): a token minted for an anonymous, unauthenticated
visitor (someone following a shared invoice/estimate link, or answering a CSAT survey) to reach
exactly one record without signing in. Deliberately absent from `ORG_SCOPED_TABLES` and carrying no
RLS policy of its own, because a portal request has no `request.profile` to derive an org context
from in the first place: the table instead maps `sha256(url_token)` to an `org_id` directly, so the
public view can hash the token it was handed, resolve the org, set the RLS context, and only then
query the actual resource. See
[Multi-tenancy and RLS: Portal tokens](../architecture/multi-tenancy-and-rls.md#portal-tokens).

Not to be confused with the **portal session token** below, which is a different mechanism that
unfortunately shares the word.

### Portal session token

The credential a customer holds after signing in to the [customer portal](../api/customer-portal.md):
a 24 hour JWT carrying `typ: "portal"`, `org_id` and `contact_id`, minted by `mint_portal_token`
(`backend/common/portal_auth.py`). Its principal is a `Contact`, not a `User`, and it is refused
on every path outside `/api/portal/`.

The distinction from the [portal token](#portal-token) above is worth holding on to: that one is
*held* (the URL is the credential, anyone with the link is in, and it reaches one record), this one
is *earned* (the customer proves control of an email address first, and it reaches every case they
are named on). A separate `PortalLoginToken` row carries the emailed six digit code that is
exchanged for it.

### Portal contact

A `Contact` who has signed in to the customer portal. Not a user of your CRM: no `User` row, no
`Profile`, no role, and no org membership. Comments they write carry `commented_by_contact` and
leave `commented_by` null, which is how every reader tells a customer's reply from a colleague's.

### Personal access token

`bcrm_pat_…` (`PersonalAccessToken`, `backend/common/models.py`): a token a signed-in user creates
for themselves (`POST /api/profile/tokens/`) to authenticate scripts, AI agents, or other
programmatic access without a browser session. It authenticates as the profile that created it and
inherits that profile's role and org in full; its `scopes` field is stored but not enforced.
Deactivating the owning profile invalidates the token immediately; `resolve_valid_pat` checks
`profile.is_active` on every use, with no separate revocation step required. See
[Tokens and API keys: Personal access tokens](../api/tokens-and-api-keys.md#personal-access-tokens).

### Organization API key

`Org.api_key`: a single key per organization, not per user, managed through `GET`/`POST
/api/org/api-key/`, admin-only in both directions and only from an interactive session. A request
bearing it in the `Token` header authenticates as the organization's first active `ADMIN` profile
rather than any specific person. It is read-only and cannot reach a credential endpoint, and
`DJANGO_ORG_API_KEY_AUTH=false` disables it outright, but it still reads every record in the org and
cannot be revoked per integration, so a personal access token (which authenticates as one specific
individual, expires, and carries scopes) is the better choice for anything new. See
[Tokens and API keys: Organization API keys](../api/tokens-and-api-keys.md#organization-api-keys).

### Stage versus status

Two different kinds of field that sound alike but aren't. `status` (`Lead.status`, `Case.status`,
`Task.status`) is a plain `CharField` over a fixed set of choices, three separate tuples, not one
shared definition: `LEAD_STATUS` and `STATUS_CHOICE`, defined in `backend/common/utils.py`, back
`Lead.status` and `Case.status` respectively, while `Task.STATUS_CHOICES` is defined inline on the
`Task` model itself in `backend/tasks/models.py`. Each one is still fixed and identical across every
organization. That part of the "status vs. stage" distinction holds for all three. `stage`, on those
same three
models, is a nullable foreign key to a per-org `LeadStage`/`CaseStage`/`TaskStage` row, itself
belonging to a `LeadPipeline`/`CasePipeline`/`TaskPipeline` (see [Pipeline](#pipeline)). Each
organization can define its own Kanban columns, and a stage's `maps_to_status` field is what keeps
the fixed `status` in sync when a record moves between stages. `Opportunity.stage`, despite sharing
the field name, is the *fixed* kind: a plain `CharField` over `common.utils.STAGES` (`PROSPECTING`
… `CLOSED_LOST`), the same six values for every organization, with no configurable pipeline behind
it and no separate `status` field at all. See
[Data model: Conventions](../architecture/data-model.md#conventions) for the full explanation this
entry summarizes.

### Pipeline

`LeadPipeline`, `CasePipeline`, `TaskPipeline`: an org-owned, named container of ordered `Stage`
rows for one entity type (see [Stage versus status](#stage-versus-status)). An organization can
define more than one pipeline for the same entity type. Each model's own docstring gives an
example set of names (Inbound/Outbound/Enterprise for leads, Support/Engineering/Billing for cases,
Development/Support/Marketing for tasks), and *at most* one per organization may be marked
`is_default`, enforced by a `UniqueConstraint` on `org` scoped to `is_default=True`; nothing requires
that one exist. The field's own `help_text` says a default pipeline is "where new [records] without
explicit pipeline go", but that's a statement of intent, not a description of running code: nothing
in this codebase currently reads `is_default` to route a new `Lead`, `Case`, or `Task` onto a
pipeline. A record's `stage` foreign key is simply `NULL` until something sets it explicitly, which
its own `help_text` calls "use status-based kanban": the fixed `status` field, not a pipeline stage,
is what a newly created record actually falls back to. `Opportunity` has no equivalent pipeline
model: its stage list is the fixed set above, not org-configurable.
