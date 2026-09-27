# Web forms (web-to-lead)

## What this is

A web form is a form you build in the CRM and embed on your own website. Somebody fills it in
with no account and no login, and a `Lead` appears in your org with the owner, source and tags
the form was configured with. Nothing is copied by hand and no API key is pasted into your site's
HTML.

The whole feature lives in `backend/webforms/`. There are two halves and they have different
trust properties, so keep them apart when reading:

- **The management API**, `/api/webforms/...`, authenticated, org-scoped, admin-only for every
  write. This is what the Settings screen in the web app and the phone app talk to.
- **The public API**, `/api/public/forms/<org_id>/<form_id>/...`, anonymous. No JWT, no session,
  no token. This is what a stranger's browser reaches.

## Building one

`/settings/web-forms` in the web app, **Settings -> Web forms** in the phone app, or the API
directly. Reading the list is open to every member of the org; creating, editing, publishing and
deleting are refused to anyone who is not an admin (`is_org_admin`, `backend/webforms/views.py`).
That split is deliberate: a published form is an endpoint anyone on the internet can post to, and
every accepted post writes a lead into your org, so creating one is closer to minting a credential
than to editing a record.

A form is a list of ordered fields. Each field writes into either one `Lead` column or one of your
org's `Lead` custom field definitions, never both, and that is enforced by a database check
constraint (`web_form_field_exactly_one_target`) as well as by the serializer.

The `Lead` columns a form may collect are a fixed whitelist, in
`backend/webforms/constants.py`:

`salutation`, `first_name`, `last_name`, `email`, `phone`, `company_name`, `job_title`, `website`,
`title` (the lead's subject line), `description` (the message), `city`, `state`, `country`,
`postcode`, `industry`.

Assignment, pipeline stage, probability and deal value are deliberately absent. A form is filled
in by an anonymous stranger, so what they can reach has to be a decision somebody made rather than
whatever the model happens to expose. `country` and `industry` are validated against the same
choice tuples `Lead` declares, and `description` is capped at 5,000 characters.

### Publishing

`POST /api/webforms/<id>/publish/`. A form only accepts submissions once published, and publishing
validates the form's shape rather than just flipping a flag:

- **An email field is required.** It is what lets a repeat submission update the existing lead
  instead of colliding with `Lead`'s `UniqueConstraint(Lower("email"), "org")`.
- **A form set to redirect on success must have a redirect URL**, and that URL must be `http` or
  `https`. Other schemes are refused, because the embed navigates to it and a `javascript:` value
  there would be stored XSS on your own site.
- **Publishing an already-published form is a 400, not a no-op**, since a caller believing the
  form is in a state it is not is worth telling.

`POST /api/webforms/<id>/unpublish/` stops it. Unpublish the moment you take the snippet off your
site: an embed you have removed from the page is not the same thing as an endpoint that has
stopped accepting posts.

## Assigning new leads

From django-crm 1.13.0 a lead form assigns each new lead in one of two ways, set by
`assignment_mode`:

- **`person`** (the default): every new lead goes to `assign_to`, as before.
- **`rotation`**: each new lead goes to the next member of `rotation_members` in turn.

| Field | Notes |
| --- | --- |
| `assignment_mode` | `person` or `rotation` |
| `rotation_members` | Profile ids in the caller's org. A deactivated member cannot be added; one already stored is kept on save, since both clients resend the whole list |
| `rotation_cap` | Optional, at least 1. The most open leads a member may hold before the rotation passes them over. `null` means no cap |
| `rotation_last_assigned` | Read-only: the member who received the last rotated lead |
| `rotation_members_details`, `rotation_last_assigned_details` | Read-only: `id`, `email`, `name` and `is_active` for each |

Like every other write here, only an admin may set these. The rules, each a `400`:

- **Lead forms only.** A ticket form with `assignment_mode: "rotation"` is refused
  (`"Rotation is for lead forms only. Tickets from a ticket form are assigned by your routing
  rules."`), and so is changing a rotation form's target to tickets.
- **Somebody to rotate between.** Rotation with no members is refused
  (`"Choose at least one member to rotate between."`).

How the turn is chosen (`rotation_assignee` in `backend/webforms/service.py`):

- **Eligible** means an active member of the form's org who, when a cap is set, holds fewer open
  leads than the cap. **Open** means an active lead whose status is neither `converted` nor
  `closed`. The count covers every lead in the org, not just this form's, because the cap is about
  the member's workload.
- The eligible members are taken in id order, and the first whose id comes after
  `rotation_last_assigned` gets the lead, wrapping to the first. The cursor names a person rather
  than a list position, so adding or removing a member does not skip or repeat anybody's turn. The
  form row is locked while the choice is made, so two submissions arriving together go to two
  members.
- **Nobody eligible** (everyone deactivated or at the cap) leaves the lead unassigned and the cursor
  where it was. The notify list is still emailed.
- **Only new leads rotate.** A repeat address merges into the existing lead and never changes its
  owner.

On a rotation form `assign_to` is kept but ignored, so switching back to `person` restores it. The
new lead's `created_by` is the chosen member, or the form's creator when nobody was chosen.

In the clients, **Assign new leads** on the form's settings page, on the web and the phone, chooses
between one person and rotating between members, with **Rotate between** for the members, **Most
open leads per member** for the cap, and the last assigned member shown beside them.

## Embedding

Both snippets are built server-side and returned on the form's detail response as `embed_html` and
`embed_js`, because they need this API's own base URL. A browser only knows the frontend's origin,
so a snippet assembled in the client would point at the wrong host.

**iframe (the default).** Self-contained HTML, no external assets, no framework:

```html
<iframe src="https://crm.example.com/api/public/forms/<org_id>/<form_id>/embed/"
        style="width:100%;border:0" height="500" title="Contact us"></iframe>
```

The frame posts its own height to the parent window (`bottlecrm:webform:height`) if you want to
size it dynamically. Nothing else crosses that boundary.

**Script.** Renders into a div on your page, so it inherits nothing and collides with nothing:

```html
<div id="bottlecrm-webform-<form_id>"></div>
<script src="https://crm.example.com/api/public/forms/<org_id>/<form_id>/embed.js" async></script>
```

The field configuration is inlined into the script at render time rather than fetched, which keeps
the cross-origin surface down to the single submit route.

!!! note
    The iframe embed needs `X-Frame-Options` off for that one view, which
    `@xframe_options_exempt` does. It is a deliberate hole in a site-wide clickjacking protection,
    scoped to the one view whose entire purpose is being framed. The response carries no session
    and no tenant data, so an attacker who frames it can only submit a form that was already
    public. When the form lists allowed origins, the response also carries a
    `Content-Security-Policy: frame-ancestors` header naming them.

## Spam and abuse controls

Three are always on and cannot be turned off:

- **A honeypot field.** Named to look attractive to a form-filling bot, positioned off-screen
  rather than `display:none` so it still looks fillable to anything that skips hidden inputs. A
  submission that fills it is recorded as spam, writes no lead, and receives the **same success
  response an accepted submission gets**. A bot that can tell it was caught retries differently.
- **Two rate limits.** `WEBFORM_THROTTLE_IP` (default `10/hour`) buckets per client IP per form.
  `WEBFORM_THROTTLE_GLOBAL` (default `200/day`) caps a single form across all clients. The second
  one matters most: the first keys on the client address (`common.request_meta.client_ip`, see
  `NUM_PROXIES`), so a sender with many addresses gets many buckets, and none of them lifts the
  second. Raise the global limit for a form on a high-traffic page.
- **Disposable address rejection**, from the `disposable-email-domains` package. Per form, on by
  default (`reject_disposable_email`).

!!! warning "Set `CACHE_URL` in production"
    DRF throttling counts in Django's default cache. With no `CACHE_URL`, that is a per-process
    `LocMemCache`, so with N Gunicorn workers the effective limit is roughly N times the configured
    one and it resets on every restart. Point `CACHE_URL` at Redis (you already run one for Celery)
    and the limits become real. See [Environment variables](../reference/environment-variables.md).

**Cloudflare Turnstile** is optional, per form, and off by default. Configure a site key and a
secret on the form; the secret is write-only and is never returned by the API, only a
`has_captcha_secret` boolean saying whether one is stored. Verification **fails closed**: a
timeout, a connection error, a malformed response or a missing secret all refuse the submission.
Failing open would mean an attacker's first move is making Cloudflare unreachable.

### Origin restrictions

`allowed_origins` is a list of exact origins (scheme, host and optional port, no paths and no
wildcards). An empty list means unrestricted, which is the usable default for somebody pasting a
snippet for the first time; the honeypot, the throttles and the captcha do not depend on it, so an
unconfigured form is not an unprotected one.

Understand what this control is. It is browser-enforced, like CORS. A non-browser caller sets its
own `Origin` and `Referer` headers, so this never was and never will be a defence against a
scripted client. The throttles and the captcha are what apply there. CORS headers are added on the
`/submit/` route only, and only for an origin on the list.

## What happens to a submission

`webforms/service.py` is the single write path, shared with the deprecated endpoint below.

- **A new address creates a lead**, with `status="assigned"`, `source` from the form's configured
  lead source, the form's assignee (or, on a rotation form, the member whose turn it is; see
  [Assigning new leads](#assigning-new-leads)), and the form's tags.
- **A repeat address merges**, matched case-insensitively within the same org. The merge **fills
  blank fields only and never overwrites a populated one**. Anyone who knows a prospect's email
  address can post your form, so an overwrite would let a stranger rewrite that prospect's record.
  Assignment, status, source and the pipeline columns are never touched by a merge: they belong to
  whoever owns the lead now.
- **The message becomes a comment**, not an overwrite. `description` is deliberately outside the
  mergeable set, so a returning visitor's second message is added to the lead's timeline rather
  than replacing the first.
- **Every submission is stored**, accepted or rejected, which is what makes spam review and an
  honest conversion rate possible. `GET /api/webforms/<id>/submissions/` lists them. The internal
  reject reason is never returned: naming the control that caught a bot is how the next bot gets
  past it.

A **ticket form** opens a `Case` instead of a lead, and finds or creates the org's contact for the
submitted address. Because that address is only the submitter's claim, the ticket's contacts are
not sent status-change emails or a CSAT survey until an agent posts a public reply on the ticket
(see [How replies go back out](inbound-email.md#how-replies-go-back-out)). Closing a spam ticket
without answering it therefore emails nobody.

The form's active notify list and the record's assignee are emailed once per accepted submission,
in one message. For a lead that is whoever the lead is actually assigned to: the member a rotation
picked, or the existing owner when a repeat address merged into a lead somebody already holds (from
django-crm 1.13.0; before, it was always the form's `assign_to`). For a ticket it is the form's
active `assign_to`. Rejected submissions notify nobody, because an org told about every bot learns to ignore the notification
and then misses the real one.

`GET /api/webforms/<id>/analytics/` returns a fixed trailing 30 days of views, submissions, spam
and a conversion rate. Views are counted per form per day when an embed renders; submissions are
counted from the submission rows, so each number has exactly one source of truth. The series is
zero-filled, so a quiet day is a real zero rather than a gap.

## Multi-tenancy

The org id is in the URL path, before the form id, and the RLS context is set from it **before**
anything queries. This is not stylistic. `web_form` is an org-scoped table, so under an empty
context the lookup returns zero rows on a correctly configured Postgres, and resolving the form
first to read its org afterwards would answer 404 in production while passing every test.

The org id is not a credential and is not treated as one. It selects the tenant; the form row is
then filtered on it, so a mismatched pair answers 404 like any other miss. Missing, unpublished,
and belonging to another org are all deliberately indistinguishable.

`/api/public/forms/` is in `RequireOrgContext.EXEMPT_PATHS`, prefix-matched, which is why the org
id sits after that fixed prefix rather than before it.

## The deprecated endpoint

`POST /api/leads/create-from-site/` is the original web-to-lead endpoint. It still works and its
request and response bodies are unchanged, but it now routes through the same write path as
everything above, and its backing form is created from the API key's own configuration on first
use. Set that form to rotation and this endpoint rotates too; a contact it creates takes the
lead's active assignees (the rotated member on a new lead, the owner on a merged one).

Prefer the public endpoint for anything new. `create-from-site` requires an authenticated caller
with org context, so it was never actually usable from a static page without putting a credential
in it, and it has no origin restriction, no rate limit and no captcha.

## Not built

Two things people ask for that this deliberately does not do yet:

- **Conditional fields** (show field B only when field A has a given value).
- **Multi-step forms.**

Both were scoped out of the first release rather than half-built. A form is a flat ordered list of
fields.
