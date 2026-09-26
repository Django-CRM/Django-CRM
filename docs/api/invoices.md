# Invoices and estimates

Authenticated routes in `backend/invoices/api_urls.py`, all mounted under `/api/invoices/...`. **The
DRF views live in `backend/invoices/api_views.py`, not `views.py`**, which is a legacy,
non-API module in this app. Anonymous client-portal routes are in
`backend/invoices/public_urls.py`, served by `backend/invoices/public_views.py`, mounted at
`/api/public/...`. Authorization for every authenticated invoice/estimate/recurring-invoice endpoint
is centralized in `backend/invoices/permissions.py`. See [Conventions](conventions.md) for pagination,
filtering and response-shape rules assumed rather than repeated here, and [Errors](errors.md) for the
404-vs-403 principle this page's own module docstring states almost verbatim.

This page covers invoices, estimates, line items and the public portal, the endpoints this
documentation task scopes. Payments, products, templates, recurring invoices, dashboards/reports, and
invoice-from-opportunity/from-time-entries are also part of this module (`api_urls.py`) but are out of
scope for this page.

## One access rule for invoices, estimates and recurring invoices

`invoices/permissions.py` states a single rule, reused by all three record types via
`get_invoice_or_error`/`get_estimate_or_error`/`get_recurring_or_error`
(`invoices/permissions.py:101-122`):

```
1. Does this record exist in the caller's org?  -> 404 if not
2. May this caller act on it?                   -> 403 if not
```

The exact two-step [Errors: Not found versus forbidden](errors.md#not-found-versus-forbidden) quotes
as this module's own docstring. Step 2, `has_object_access` (`:32-58`), grants access to an org admin,
a Django superuser, the record's creator, or any profile in its `assigned_to` set. There is no
separate, narrower rule for write vs. delete the way [Cases](cases.md#a-cases-read-write-and-delete-rules-differ-on-purpose)
has; one rule covers read, write and delete alike for invoices and estimates.

**The two comparisons inside that rule are against deliberately different types.** `created_by` is a
`User` FK (from `AuditModel`), so it is compared against `request.user`; `assigned_to` holds `Profile`
rows, so it is compared against `request.profile` (`:42-58`). The module docstring calls out that
getting this backwards (comparing `request.profile` to `created_by`, as the estimate views used to)
is not merely always-`False` the way the same mistake is elsewhere in this codebase: because it reaches
the ORM as `Q(created_by=<Profile instance>)`, Django raises `ValueError` on the type mismatch, and the
non-admin estimate list answered `500` rather than an empty or wrong list. That specific defect is
fixed in the current source (`_get_or_error`/`has_object_access` as shown), but it is a concrete example
of why this codebase keeps insisting a permission check be provably tested in both directions.

## Invoices

`GET /api/invoices/` (`InvoiceListView.get`, `invoices/api_views.py:176-194`) hand-builds a response
with the same `count`/`next`/`previous`/`results` key names DRF's `get_paginated_response()` would
produce, plus a `totals` block computed over every invoice the caller can see (not just the page or the
current filter). This is the hand-rolled shape, not the DRF-generated one,
[Conventions](conventions.md#pagination) and [Cases](cases.md#solutions) both name
`GET /api/cases/solutions/` as the only endpoint in this API that actually calls
`get_paginated_response()`; this view reaches the same four key names by building the dict itself:

```json
{
  "count": 41,
  "next": null,
  "previous": null,
  "results": [ { "...": "InvoiceListSerializer" } ],
  "totals": {
    "count": 41, "outstanding": "12500.00", "overdue": "3200.00",
    "due_this_month": "4100.00", "paid_this_quarter": "9800.00",
    "draft": "600.00", "action_needed": 5,
    "by_currency": [
      { "currency": "USD", "count": 41, "action_needed": 5, "outstanding": "12500.00", "...": "..." }
    ]
  }
}
```

`totals` is computed over `Invoice.objects.filter(pk__in=queryset.values("pk"))` rather than the
visibility-scoped queryset directly (`_totals`, `:196-235`), the comment explains why: a non-admin's
queryset joins `assigned_to` (a many-to-many), so summing across that join would multiply an invoice's
amount by its number of assignees; re-querying by `pk__in` drops the join before the `Sum`.
Money is grouped by currency, as in the `/api/invoices/reports/` endpoints: each amount is the plain figure when the
visible invoices use at most one currency and `null` when they use several, and `by_currency` carries
one row per currency either way. `count` and `action_needed` are totalled across currencies.
`action_needed` is a **count**, not an amount, invoices still `Draft`, plus anything unpaid and past
`due_date` (`:209`). A non-admin caller sees only invoices they created or are assigned to (`:96-103`,
the same rule [above](#one-access-rule-for-invoices-estimates-and-recurring-invoices) applies at list
scope).

Filters (`filter_queryset`, `:105-174`): `search` (matches `invoice_title`, `invoice_number`,
`client_name` or `client_email`, contains, capped to 100 characters), `status` (exact),
`account`/`contact`/`opportunity` (exact id), `assigned_to` (exact id), `created_by` (exact id),
`issue_date_gte`/`issue_date_lte`, `due_date_gte`/`due_date_lte`, `cf_<key>`, and `sort`: whitelisted
to `created_at`/`due_date`/`issue_date`/`total_amount`/`status`, each optionally `-`-prefixed
(`:163-172`); an unrecognized `sort` value is silently ignored, leaving the default `-created_at`.

`POST /api/invoices/` (`:237-292`) is validated by `InvoiceCreateSerializer`
(`invoices/serializer.py:542-618`; see [Fields](#fields)). On success (`201`):

```json
{"error": false, "message": "Invoice created successfully", "invoice": { "...": "InvoiceSerializer" }}
```

Creating an invoice queues `create_invoice_history` (an audit-trail Celery task) and, if the invoice
already has assignees, `send_email` to notify them (`:261-278`).

`GET /api/invoices/{id}/` (`InvoiceDetailView.get`, `:295-341`) returns
`{"invoice": ..., "attachments": [...], "comments": [...], "history": [...], "custom_field_definitions": [...]}`.
`PUT /api/invoices/{id}/` (`:343-400`) accepts a **partial** body (`partial=True` is passed even though
this is `PUT`, `:376`) through the same `InvoiceCreateSerializer`. **`InvoiceDetailView` defines only
`get` and `put`, no delete method of any kind exists for an invoice**; an invoice is retired via
`POST /api/invoices/{id}/cancel/`, never removed. (Contrast [Estimates](#estimates), which does support
a hard delete.)

Three lifecycle actions, each `get_invoice_or_error`-gated and each a `POST` with no request body:

- `POST /api/invoices/{id}/send/` (`InvoiceSendView.post`, `:403-440`) refuses to re-send a `Paid` or
  `Cancelled` invoice (`400`); moves `Draft` → `Sent`, stamps `sent_at`/`is_email_sent`, and queues
  `send_invoice_to_client`.
- `POST /api/invoices/{id}/mark-paid/` (`InvoiceMarkPaidView.post`, `:443-481`) records a `Payment`.
  It defaults `amount` to the current `amount_due`, `payment_method` to `OTHER`, and `payment_date` to
  today when the caller omits them, but routes the whole thing through
  `PaymentCreateSerializer` (`invoices/serializer.py:365-396`) either way, so a payment amount of `0`
  or one exceeding the outstanding balance is still rejected (`400`).
- `POST /api/invoices/{id}/cancel/` (`InvoiceCancelView.post`, `:563-599`) refuses an already-`Cancelled`
  invoice and refuses a `Paid` one (`400` either way). Cancelling is for money that will never be
  collected, not for reversing a completed payment.

`POST /api/invoices/{id}/duplicate/` (`InvoiceDuplicateView.post`, `:484-560`) copies the invoice's
client/billing/discount/tax/template fields and every line item into a new `Draft` invoice with a fresh
`invoice_number`, `issue_date` reset to today, and no payments; `201` with the new
`InvoiceSerializer` payload.

`GET /api/invoices/{id}/pdf/` (`InvoicePDFView.get`, `:602-631`) streams
`application/pdf`; a missing PDF backend answers `503`, any other generation failure `500`.

## Estimates

The same list/detail/lifecycle shape as invoices, with one structural difference: **an estimate can be
hard-deleted; an invoice cannot** (see [above](#invoices)).

`GET /api/invoices/estimates/` (`EstimateListView.get`, `:1027-1064`). Same hand-built
`count`/`next`/`previous`/`results` shape as the invoice list above, without a `totals` block. Filters:
`status`, `account`, `search` (matches
`estimate_number`, `title` or `client_name`), `cf_<key>`. Visibility follows the same rule as invoices,
non-admin sees only estimates they created or are assigned to.

`POST /api/invoices/estimates/` (`:1066-1100`) is validated by `EstimateCreateSerializer`
(`invoices/serializer.py:892-1018`; see [Fields](#fields)). **Unlike `InvoiceCreateSerializer`,
`EstimateCreateSerializer` does not mark `status` read-only**, nothing stops an authenticated,
same-org caller with write access from `POST`ing or `PUT`ing an estimate directly into
`status: "Accepted"`. Doing so does **not** run any of the checks or side effects the public accept
flow enforces (see [Public portal endpoints](#public-portal-endpoints)): no expiry check, and none of
`accepted_by_name`/`accepted_by_email`/`accepted_ip`/`accepted_user_agent` get populated, even though
the model comment above those fields calls acceptance "an authorisation" that the app records who gave.
This is reported here, not fixed, per this documentation task's scope.

`GET/PUT/DELETE /api/invoices/estimates/{id}/` (`EstimateDetailView`, `:1103-1187`); `DELETE` is a
real hard delete (`estimate.delete()`, `:1182`), gated by the same access rule as read/write.

`POST /api/invoices/estimates/{id}/convert/` (`EstimateConvertView.post`, `:1189-1266`) creates a new
`Draft` invoice from the estimate's client/billing/discount/tax fields and line items, then sets
`estimate.status = "Accepted"`, `accepted_at = now()`, and `estimate.converted_to_invoice = invoice`
(`:1253-1257`). **This is the field that stops a second conversion from duplicating the invoice**: the
view's very first check is `if estimate.converted_to_invoice: return 400` (`:1200-1204`), before
anything else runs. Any client rendering an estimate list needs to read this field, not just `status`,
to decide whether to offer "raise an invoice",
`EstimateListSerializer.converted_to_invoice` (`invoices/serializer.py:832,846,866-870`) is on the list
row specifically so the worklist can tell "accepted, not yet billed" apart from "already billed" without
a second request per row; the class docstring is explicit that omitting it "would offer to raise a
second invoice for an estimate that already has one."

`POST /api/invoices/estimates/{id}/send/` (`EstimateSendView.post`, `:1269-1291`) moves `Draft` → `Sent`
and queues `send_estimate_to_client`. `GET /api/invoices/estimates/{id}/pdf/`
(`EstimatePDFView.get`, `:1294-1334`) streams the PDF the same way the invoice one does.

## Line items

Two ways line items reach the database, with different validation coverage.

**Nested in an invoice create/update.** `InvoiceCreateSerializer`'s `line_items`
(`InvoiceLineItemCreateSerializer`, many, `invoices/serializer.py:288-304`) is written inside the same
request as the invoice itself; `validate()` additionally rejects any line item whose `product` belongs
to a different org (`:689-704`: the comment notes the nested serializer's own `product` field is an
unscoped `PrimaryKeyRelatedField` over every org's products, so this cross-org check has to happen at
the parent serializer, where org context exists). On `PUT`, sending `line_items` **replaces** the set.
Existing rows are deleted and recreated (`InvoiceCreateSerializer.update`, `:731-753`); omitting the key
entirely leaves existing line items untouched.

**Standalone, via `POST /api/invoices/{invoice_id}/line-items/`**
(`InvoiceLineItemListView.post`, `invoices/api_views.py:653-679`) and
**`PUT`/`DELETE /api/invoices/{invoice_id}/line-items/{id}/`**
(`InvoiceLineItemDetailView`, `:682-751`). Both route through `InvoiceLineItemCreateSerializer`
directly, with no wrapping parent-serializer `validate()`, and **neither has an application-code check
that `product` belongs to the caller's org**: `InvoiceLineItemCreateSerializer` declares no `product`
field of its own (`invoices/serializer.py:288-304`), so DRF derives a plain
`PrimaryKeyRelatedField(queryset=Product.objects.all())`, every org's products, and both views use it
raw, unlike the *nested* path, which does check (`InvoiceCreateSerializer.validate()`, `:689-704`; grep
confirms this is the only `product`-org check anywhere in `invoices/serializer.py`). This is a real gap
against [the ORM-filter contract](../architecture/multi-tenancy-and-rls.md#the-two-layer-contract),
the explicit application-level filter that page calls non-optional is missing here, but it is not an
unconditional cross-tenant read. `product` (the table, along with
`invoice_line_item`) is itself RLS-protected: both are in `ORG_SCOPED_TABLES`
(`common/rls/__init__.py:97`), with policy enabled by
`common/migrations/0008_enable_rls_product_invoice_line_item.py`. Under the non-superuser database role
[PostgreSQL and RLS](../self-hosting/postgresql-and-rls.md) mandates, the unscoped
`Product.objects.all()` queryset sees zero rows for a foreign
org's id under RLS's fail-safe default, and the request answers `400` ("invalid pk") rather than
attaching the row. The missing ORM filter is caught by the safety net. Under the *default*
configuration, `DBUSER` unset, which falls back to the Postgres superuser, and therefore bypasses RLS
entirely, per [Multi-tenancy and RLS](../architecture/multi-tenancy-and-rls.md#the-two-layer-contract),
nothing stops it: the row attaches, and `InvoiceLineItemSerializer.product_name`
(`source="product.name"`, `:254,263`) discloses that other org's product name on every subsequent read.
Whether this is exploitable in a given deployment is exactly the question "is `DBUSER` a superuser?"
answers, which is also why it's worth fixing regardless of how any one deployment is configured: the
ORM filter is the contract, RLS is the net, and this endpoint is missing the contract. Reported here,
not fixed, per this documentation task's scope.

Every write to a line item (either path) recalculates and saves the parent invoice's totals
immediately afterward (`invoice.recalculate_totals(); invoice.save()`). A client does not need a
separate call to refresh `total_amount`/`amount_due` after adding, editing or removing a line item.

`InvoiceLineItemCreateSerializer.Meta.fields` (`invoices/serializer.py:291-303`):
`product`, `name`, `description`, `quantity`, `unit_price`, `discount_type`, `discount_value`,
`tax_rate`, `order`. Every one of these is optional; `product` is nullable, `name` is `blank=True,
null=True`, `description` is `blank=True`, `quantity` defaults `1`, `unit_price`/`discount_value`/
`tax_rate`/`order` all default `0`, and `discount_type` is `blank=True` over `DISCOUNT_TYPES`
(`PERCENTAGE`, `FIXED`) (`invoices/models.py:512-565,71-74`). Posting `{}` is valid and creates a
line item with `quantity=1`, `unit_price=0`. **Estimate line items work identically**;
`EstimateLineItemCreateSerializer` has the same field list (`invoices/serializer.py:306-321`), except
there is no standalone endpoint for them at all: estimate line items are only ever written nested
inside `POST`/`PUT /api/invoices/estimates/{id}/`, and `EstimateCreateSerializer.validate()`
(`:969-980`) has **no application-level product-org check whatsoever**, not even the
parent-serializer-level check `InvoiceCreateSerializer` has. The same RLS caveat applies here as above:
`product` is RLS-protected, so this is a missing-ORM-filter gap caught by the safety net under a
correctly-configured (non-superuser) database role, and a real cross-tenant read under the default,
superuser one.

## Public portal endpoints

**Mounted at `/api/public/...`, not under `/api/invoices/...`**: `backend/crm/urls.py:23`:
`path("api/public/", include("invoices.public_urls", ...))`. Every view here sets
`authentication_classes = []` and `permission_classes = []` (`invoices/public_views.py`). These
endpoints are genuinely anonymous, reachable by anyone holding the URL, by design (an emailed invoice
or estimate link).

**Resolving the org happens before the RLS context is set, from an unscoped token lookup. This is the
whole reason the endpoint works at all under RLS.** `_resolve_org_context(token, resource_type)`
(`invoices/public_views.py:30-46`) calls `common.portal_tokens.resolve_portal_org`, which hashes the
raw URL token (`sha256`) and looks it up in `common.models.PortalAccessToken`, a small table carrying
no RLS policy. To get an `org_id`, then calls `set_rls_context(org_id)`. Only *after* that does the
view run its own `Invoice`/`Estimate` query. An unknown or malformed token leaves the RLS context
empty; the subsequent scoped query then returns nothing under RLS's fail-safe default, and the caller
gets the same `404` a disabled link would give, a stranger probing token values learns nothing about
whether one is real. [Architecture: Multi-tenancy and RLS](../architecture/multi-tenancy-and-rls.md#portal-tokens)
documents this mechanism in full, including why the lookup table has to be unscoped in the first place
(you cannot set `app.current_org` until you know the org, and you cannot look up the row that would
tell you the org until the context is set). Read that page for the general pattern; this page is the
concrete instance of it for invoices and estimates. Every endpoint below additionally requires
`public_link_enabled=True` on the record, independent of the token resolving at all. An admin can
revoke a previously-shared link without rotating the token.

- `GET /api/public/invoice/{token}/` (`PublicInvoiceView.get`, `:61-163`). Returns the invoice's
  client-facing fields, billing address, line items and payment history. First view stamps `viewed_at`
  and flips `Sent` → `Viewed`.
- `GET /api/public/invoice/{token}/pdf/` (`PublicInvoicePDFView.get`, `:166-206`), same PDF the
  authenticated endpoint generates, `include_payments=True`.
- `GET /api/public/estimate/{token}/` (`PublicEstimateView.get`, `:209-301`), same shape, for
  estimates; also stamps `viewed_at` on first view.
- `GET /api/public/estimate/{token}/pdf/` (`PublicEstimatePDFView.get`, `:304-344`).
- `POST /api/public/estimate/{token}/accept/` (`PublicEstimateAcceptView.post`, `:347-430`). The
  authenticated `status` write does not exist for the anonymous caller, so acceptance is its own
  endpoint with its own rules, all enforced server-side because "the frontend is not a trust boundary"
  (comment at `:359-363`): the estimate must currently be `Sent` or `Viewed` (`400` otherwise); it must
  not be expired (`estimate.is_expired`, checked against `expiry_date`, `400` with the expiry date in
  the message if it is, enforced here for the first time; the model comment notes `expiry_date` was
  previously "stored and printed on the estimate but enforced nowhere"); and the request body must
  supply `name` and a syntactically valid `email` (`400` otherwise). On success, the view records who
  accepted: `accepted_by_name`, `accepted_by_email` (truncated to the column's max length), `accepted_ip`
  (best-effort, from `X-Forwarded-For` or `REMOTE_ADDR`, spoofable, treated as evidence, not proof),
  and `accepted_user_agent`, alongside `status = "Accepted"` and `accepted_at`.
- `POST /api/public/estimate/{token}/decline/` (`PublicEstimateDeclineView.post`, `:433-468`): same
  `Sent`/`Viewed` precondition, no identity capture, sets `status = "Declined"` and `declined_at`.

## Fields

`InvoiceCreateSerializer.Meta.fields` (`invoices/serializer.py:558-618`) is what `POST /api/invoices/`
and `PUT /api/invoices/{id}/` accept.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `invoice_title` | string, max 100 | **required** | |
| `account_id` | uuid | **required** | Must belong to the caller's org; the model itself allows null, but the serializer makes it mandatory |
| `contact_id` | uuid | **required** | Must belong to the caller's org; must belong to `account_id` if both given |
| `opportunity_id` | uuid | optional | Must belong to the caller's org |
| `template_id` | uuid | optional | Must belong to the caller's org, without this check a cross-org template's branding/markup would render on this invoice's PDF |
| `client_name`, `client_email`, `client_phone` | string | optional | Denormalized for the PDF and portal; both `client_name`/`client_email` default `""` |
| `billing_*` / `client_*` address fields | string | optional | `billing_country`/`client_country` are one of `COUNTRIES` |
| `discount_type` | one of `PERCENTAGE`, `FIXED` | optional | |
| `discount_value`, `tax_rate`, `shipping_amount` | decimal | optional | Default `0` |
| `currency` | one of `CURRENCY_CODES` | optional | Defaults `USD` |
| `issue_date` | date | optional | Defaults today |
| `due_date` | date | optional | |
| `payment_terms` | one of `PAYMENT_TERMS` | optional | Defaults `NET_30` |
| `reminder_enabled`, `reminder_days_before`, `reminder_days_after`, `reminder_frequency` |, | optional | |
| `public_link_enabled` | boolean | optional | Gates the portal endpoints independently of the token; defaults `true` |
| `notes`, `terms` | text | optional | |
| `billing_period`, `po_number` | string | optional | |
| `line_items` | list | optional | See [Line items](#line-items) |
| `status` | one of `INVOICE_STATUS` | **read-only** | Always created `Draft`; changed only via `send`/`mark-paid`/`cancel` |

`invoice_number` is not part of the serializer at all, `Invoice.save()` generates and assigns it
server-side the first time the row is saved (`invoices/models.py:384-411`).

`EstimateCreateSerializer.Meta.fields` (`invoices/serializer.py:904-928`) mirrors the invoice shape
closely: `title` (**required**), `account_id`/`contact_id` (**required**, org-validated),
`opportunity_id` (optional, org-validated), the same client/address/discount/tax/currency/notes/terms
fields, `expiry_date` (optional. This is what the public accept endpoint checks), `public_link_enabled`,
and `line_items`. **`status` is writable here**. See the caveat in [Estimates](#estimates). Like
invoices, `estimate_number` is server-generated on first save, not part of the serializer.

`GET` responses on both (`InvoiceSerializer`/`EstimateSerializer`, `invoices/serializer.py:444-539,
873-889`) additionally return, but never accept as input: `id`, `subtotal`, `discount_amount`,
`tax_amount`, `total_amount`, `amount_paid`, `amount_due` (all computed by
`recalculate_totals()`), `sent_at`/`viewed_at`/`paid_at`/`cancelled_at` (invoice) or
`sent_at`/`viewed_at`/`accepted_at`/`declined_at`/`accepted_by_*` (estimate), `public_token`,
`public_url`, `created_by`, `created_at`, `updated_at`, and, on the estimate only,
`converted_to_invoice` (see [Estimates](#estimates)).
