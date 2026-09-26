# Data model

This page describes the shape every model in this codebase follows, the handful of ways core CRM
entities connect to each other, and a few conventions that are easy to assume rather than verify,
which is exactly why they're written down here with the file and line that makes each one true.

## Core entities

- **Lead** (`backend/leads/models.py`), a prospect not yet qualified into a customer.
  `LeadPipeline`/`LeadStage` give each org its own configurable Kanban columns.
- **Account** (`backend/accounts/models.py`), a company or customer. `AccountEmail`/
  `AccountEmailLog` support account-linked inbound mailboxes.
- **Contact** (`backend/contacts/models.py`), a person, optionally linked to an `Account`.
- **Opportunity** (`backend/opportunity/models.py`). A sales deal in progress.
  `OpportunityLineItem` holds quoted products; `DealPipeline`/`DealStage` are the org's configurable
  deal stages, each stage carrying its kind (open, won, lost) and its "how many days is too long in
  this stage" rotting thresholds; `SalesGoal` is a quota/target tracked against `Opportunity` data.
- **Case** (`backend/cases/models.py`), a support ticket. By far the largest cluster of supporting
  models: `CaseWatcher`, `CsatSurvey`, `Solution` (knowledge base), `CasePipeline`/`CaseStage`,
  `ReopenPolicy`, `EscalationPolicy`, `InboundMailbox`/`EmailMessage` (email-to-ticket),
  `RoutingRule`/`RoutingRuleState`, and `TimeEntry`.
  `Case.account` is a direct foreign key (not nullable through an intermediate table); Cases carry
  their own SLA fields (`sla_first_response_hours`, `sla_resolution_hours`,
  `first_response_at`, `resolved_at`) directly on the model.
- **Task** (`backend/tasks/models.py`). A to-do item, with its own configurable
  `TaskPipeline`/`TaskStage`, and a separate `Board`/`BoardColumn`/`BoardTask`/`BoardMember` family
  for freeform Kanban boards distinct from the Task pipeline.
- **Invoice** (`backend/invoices/models.py`): billing, alongside `Estimate`, `RecurringInvoice`,
  `Product`, `Payment`, and `InvoiceTemplate` in the same app. This app also owns the anonymous
  client-portal views (see [Multi-tenancy and RLS](multi-tenancy-and-rls.md#portal-tokens)).
- **Order** (`backend/orders/models.py`), `Order`/`OrderLineItem`.

## Relationships

The lead-to-customer path is a one-time conversion, not a standing foreign key.
`leads.services.convert_lead_to_account(lead_obj, request, create_opportunity=True)`
(`backend/leads/services.py`) runs as a side effect when a lead's `status` is updated to
`"converted"` through the normal lead update views (`backend/leads/views/lead_views.py`). There is
no dedicated "convert" endpoint. It:

1. `get_or_create`s an `Account` (case-insensitive name match within the org).
2. `get_or_create`s a `Contact` from the lead's email, if the lead has one, and links it to the
   account both by direct FK and via `Account.contacts`.
3. Creates an `Opportunity` if requested and the lead carries enough data to justify one, copying
   `assigned_to`/`teams`/`tags` across.
4. Reassigns the lead's `Comment`/`Attachments` rows (generic-relation `ContentType`/`object_id`
   pairs) from the lead onto the new account.
5. Sets `lead_obj.status = "converted"`.

There is deliberately no persisted link back from the created `Account`/`Contact`/`Opportunity` to
the originating `Lead`, `Lead`'s own model file documents this: earlier `converted_account`/
`converted_contact`/`converted_opportunity`/`conversion_date` fields were removed because they were
"never populated, conversion just sets status." If you need to trace a record back to the lead it
came from, there's nothing in the schema to follow; only the account/contact name or email is
likely to match.

`Contact` and `Account` are linked two separate ways at once: `Contact.account` is a nullable
foreign key (`on_delete=SET_NULL`), and `Account.contacts` is an independent many-to-many. Lead
conversion populates both. Code that needs "the contacts belonging to this account" should decide
deliberately which of the two it means, rather than assuming they're always in sync.

`Opportunity.account` is a nullable FK to `Account`; `Opportunity.contacts` is a many-to-many to
`Contact`. `Case.account` is a required-in-practice (blank/null allowed, but `on_delete=CASCADE`)
FK to `Account`, and `Case.contacts` is a many-to-many to `Contact`.

## Conventions

Nearly every model in this codebase inherits `BaseModel` (`backend/common/base.py`), which via
`AuditModel` (`backend/common/mixins.py`) provides:

- `id`: a `UUIDField` primary key (`default=uuid.uuid4`), not an auto-incrementing integer.
- `created_at`/`updated_at`: set automatically (`auto_now_add`/`auto_now`).
- `created_by`/`updated_by`: foreign keys to **`User`**, not `Profile`, set from
  `crum.get_current_user()` in `BaseModel.save()`. This is worth being precise about: a permission
  check that compares `request.profile` directly to `some_object.created_by` is comparing a
  `Profile` instance to a `User` foreign key and will always be `False`. See
  [Permissions and roles](permissions-and-roles.md#object-level-checks) for the bug class that
  causes and how to compare them correctly (`profile.user_id == obj.created_by_id`).

Three models opt out of this entirely and inherit plain `models.Model` instead: `User`
(`backend/common/models.py:34`), `MagicLinkToken` (`:524`), and `PortalAccessToken` (`:910`). All
three still declare their own `id = models.UUIDField(default=uuid.uuid4, primary_key=True)` by
hand, so the UUID-primary-key convention holds even for them, but none of the three has
`created_at`/`updated_by`/`created_by` fields, which makes sense for what they are: `User` predates
and sits outside the org-membership model entirely (`Profile` is what's org-scoped), and
`MagicLinkToken`/`PortalAccessToken` are short-lived, auth-bootstrap rows with no meaningful
"who last touched this" to record.

Org-scoped models add one more field: an `org` foreign key to `common.Org`, either inherited from
`BaseOrgModel` or (the more common pattern in this codebase) declared by hand on top of
`BaseModel`. See [Multi-tenancy and RLS](multi-tenancy-and-rls.md#baseorgmodel) for which models do
which and why both are valid as long as the table is also registered and migrated for RLS.

**Every record type with a board has per-org stages, but `Opportunity.stage` is stored differently
from the other three.** `Lead.stage`, `Case.stage`, and `Task.stage` are nullable foreign keys to a
per-org `LeadStage`/`CaseStage`/`TaskStage` row (itself belonging to a `LeadPipeline`/`CasePipeline`/
`TaskPipeline`). `Opportunity` has a `pipeline` foreign key to a `DealPipeline` and a `stage`
`CharField` holding one of that pipeline's `DealStage.code` values, so the value on the wire is a
stable string (`PROSPECTING`, `CLOSED_WON`, or a code an admin's stage was given) rather than a row
id; the code never changes once the stage exists, while its label can. What a deal's stage means
(open, won, lost) is the `DealStage.kind`, which is what reports and goals count by. All three of the
other models also keep a separate, fixed-choices `status` field (`Lead.status`, `Case.status`,
`Task.status`) alongside the configurable `stage` FK. A stage's `maps_to_status` field is what keeps
the two in sync when a record moves between stages. A deal has no second field of that kind: its
stage's kind already says whether it is open, won or lost.

Several models (`Lead`, `Opportunity`, `Case` among them) carry a `custom_fields = JSONField()`
whose values are validated against `common.CustomFieldDefinition`, a per-org schema extension
mechanism that doesn't require a migration to add a field for one org.

Finally, one validation gotcha worth knowing before you rely on `Model.clean()`: DRF's
`ModelSerializer` calls `instance.save()`, not `instance.full_clean()`, so a model's `clean()`
method is invoked through the API only if that model's own `save()` explicitly calls
`self.full_clean()` itself: most don't. `Lead.clean()`, `Opportunity.clean()`, and `Case.clean()`
(`backend/cases/models.py:180`) all define real validation rules this way, and by default those
rules are silently unenforced through the API, because none of `Lead.save()`, `Opportunity.save()`,
or `Case.save()` calls `full_clean()`. A handful of other models do call it from their own
`save()`, so their `clean()` rules reach the API as a Django
`ValidationError`: a 500 unless the view happens to catch it, not a clean DRF 400: `Task.save()`
(`backend/tasks/models.py:403`), and `Comment.save()`/`Attachments.save()`
(`backend/common/models.py:316-318` and `:430-432`, both there specifically to enforce that a
comment or attachment's `org` matches the org of whatever record it's attached to). There's no
blanket rule for which models do this, check the individual `save()` method rather than assuming.
Where an unenforced `clean()` rule turned out to matter through the API, it's been reimplemented
directly in the serializer instead of relying on `full_clean()`, `opportunity/serializer.py`'s
`validate()` duplicates the two rules from `Opportunity.clean()` for exactly this reason, per its
own comment. If you add a `clean()` method to a model, verify whether that model's `save()` calls
`full_clean()` before assuming the rule is enforced anywhere outside a unit test that calls
`full_clean()` directly.
