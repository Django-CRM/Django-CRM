# Lead capture

Leads arrive from outside BottleCRM three ways: [web forms](../integrations/web-forms.md) embedded
on your site, **lead webhooks** that other tools post to, and lead addresses that turn email into
leads. All three go through one service (`backend/leads/intake.py`, `receive_lead`), so a repeat
enquiry is recognised the same way whichever route it takes.

This page covers the webhooks, the settings screens that manage them, and what every route does
with an arriving lead.

## In the app

**Web: Settings, Lead capture** (`/settings/lead-capture`) lists, in one place:

- **Webhooks** (admins only). Each row shows the name, the preset, the source it records, its
  state (Off, Waiting for a first post, or Receiving) and whether the last post was refused.
  **New webhook** asks for a name, a preset and the source to record. The URL and the key are
  then shown once, with copy buttons.
- **Web forms** that make leads, linking to each form's own page.
- **Lead addresses** (inbound mailboxes set to make leads), linking to Inbound email.

A webhook's own page has three parts:

- **Where to send leads**: the URL with a copy button, the key's last four characters, set-up notes
  for the preset, and the actions **Send a test**, **Switch off** (or on), **Replace key** and
  **Delete**.
- **What a post becomes**: name, source, sub-source, owner (nobody or one member), tags, and the
  field mapping editor.
- **Deliveries**: the 50 newest posts, newest first, each with its result, time, error and a link to
  the lead it made or merged into.

**Phone app: Settings, Lead capture** lists the same webhooks. An admin can copy a URL, replace a
key (the new key is shown once), switch a webhook off or on, and read its deliveries. Creating a
webhook and editing its mapping are web only.

Members see the web forms and lead addresses but not the webhooks: a webhook's settings say where
the org's leads come from and who gets them, so every webhook route, reads included, is for admins.

## Sending to a webhook

```
POST /api/public/leads/<webhook id>/
```

The URL never changes. Hand it to the tool that should send leads, with the key in one of these,
checked in this order (the first one present decides):

1. `Authorization: Bearer <key>`. Use this whenever the tool lets you set a header.
2. The preset's own credential: Google Ads' `google_key` field, or Tally's `Tally-Signature`
   header (see [Presets](#presets)).
3. `?key=<key>` on the URL, for tools that only accept a URL. Proxies log full URLs, so prefer the
   header; BottleCRM cuts the key out of its own logs, and
   [Security hardening](../self-hosting/security-hardening.md#public-link-tokens-in-logs) shows
   how to do the same in your reverse proxy.

The key is shown in full only when the webhook is created and when it is replaced. Afterwards the
app shows its last four characters. **Replace key** issues a new one and keeps the old one working
for 7 days, so the sending tool can be updated without losing leads. Replacing it again within
those 7 days ends the older key at once. A key can create leads in this one org and nothing else.

The body is a JSON object or a form-encoded body, at most 64 KB and nested at most 32 levels deep.

### Replies

| Status | Body | When |
|---|---|---|
| 201 | `{"result": "created", "lead": "<id>"}` | A new lead |
| 200 | `{"result": "merged", "lead": "<id>"}` | A repeat enquiry, merged into an existing lead (or recorded on the contact a converted lead became) |
| 200 | `{"result": "duplicate", "lead": "<id>"}` | The same post again (same external id): nothing changed |
| 200 | `{"result": "test"}` | The sender's test post: logged, no lead made |
| 400 | `{"result": "rejected", "detail": ..., "errors": {"<field>": [...]}}` | A mapped value is not valid for its field (an email that is not one, a phone number with letters in it). A value that is only too long is not refused: it is cut to fit and kept whole in the lead's notes (an over-long email or phone number is left out of its field and kept in the notes) |
| 400 | `{"detail": ...}` | The body is not a JSON object or a form, is malformed, or has no lead fields at all |
| 401 | `{"detail": "Wrong key."}` | No key, or a wrong one |
| 403 | `{"detail": "Leads are switched off for this organization."}` | The org has switched Leads off (only a caller with the key learns this) |
| 404 | `{"detail": "Not found."}` | No such webhook, or it is switched off. The two are deliberately the same |
| 413 | `{"detail": "The body is larger than 64 KB."}` | |
| 429 | `{"detail": "Too many posts. Try again later."}`, with `Retry-After` | A post with the key, over 120 a minute or 5,000 a day for this webhook; or, from one address, too many refused posts (unknown webhook, wrong key, bad signature) |
| 500 | `{"detail": "The lead could not be saved. Try again."}` | Nothing was saved; senders that retry on a 5xx (Google Ads, Tally) will try again |

A reply never carries anything about the lead beyond its id.

Every post that carries the key is logged as a delivery, refused ones included (an invalid value, a
post over the limit, Leads switched off), so a lead that did not arrive leaves a trace on the
webhook's page, with the posted body (Google Ads' `google_key` is replaced with `[Filtered]`). A
post without the key writes nothing: no delivery, no "last post refused", and it uses none of the
webhook's limit, so someone who only knows the URL cannot hide or crowd out the real sender's posts.
Those refusals are limited per sending address instead, and a post with the key is never held back
by that limit, since tools such as Google Ads post for many accounts from shared addresses.
Deliveries hold personal data, so one older than 30 days is deleted the next time the webhook
receives a post or an admin reads it (`GET /api/leads/intakes/<id>/` or its `deliveries/`, which
the webhook's page on the web and the phone both ask for).

## Presets

A preset gives a new webhook a starting mapping and tells BottleCRM where that tool puts its own
lead id, its test flag, its timestamp and its key.

| Preset | Lead id (duplicate check) | Test posts | Timestamp | Reference (`source_ref`) | Key |
|---|---|---|---|---|---|
| Any tool (`generic`) | `external_id` | none | `occurred_at` | none | header or `?key=` |
| Google Ads lead form (`google_ads`) | `lead_id` | `is_test: true` | `lead_submit_time` | `campaign_id` | `google_key` in the body |
| Tally (`tally`) | `data.responseId` | none | `data.createdAt` | `data.formId` | `Tally-Signature` |
| Gravity Forms (`gravity_forms`) | `id` | none | `date_created` | `form_id` | header or `?key=` |

- **Google Ads**: in the lead form's webhook settings, paste the URL and put the key in the **Key**
  field. Google sends it back as `google_key` in every post. The mapping reads the answers in
  `user_column_data` by `column_id`: first name (or full name), last name, email (or work email),
  phone (or work phone), company, job title, city, region and postal code. Other answers are kept
  in the enquiry text.
- **Tally**: in the form's webhook integration, paste the URL and set the signing secret to the
  key. Tally signs each body with HMAC-SHA256 and sends it base64-encoded in `Tally-Signature`.
  Tally's question keys are different on every form, so the mapping matches the email and phone
  questions by type and the name, company and message questions by their usual labels (`First
  name`, `Last name`, `Full name`, `Name`, `Company`, `Message`). Choice answers are written as
  the option text.
- **Gravity Forms**: with the Webhooks Add-On sending all fields, inputs are keyed by field id,
  and the preset maps a Name field with id 1 (`1.2` prefix, `1.3` first, `1.6` last). Map your
  form's other field ids yourself, or choose **Select Fields** in Gravity Forms and name each
  field after a lead field (`email`, `phone`, `company_name`), which needs no mapping.
- **Any tool**: the mapping starts empty. A body whose top-level names are lead field names (`email`,
  `first_name`, `phone`) needs none; otherwise add rows.

## The mapping

Each row says which field of the post fills which lead field:
`{"from": "<path in the post>", "to": "<lead field>"}`.

- **To**: one of the web forms' lead fields (`salutation`, `first_name`, `last_name`, `email`,
  `phone`, `company_name`, `job_title`, `website`, `title`, `description`, `city`, `state`,
  `country`, `postcode`, `industry`), or `custom_fields.<key>` for one of your lead custom fields.
  Nothing else can be filled: owner, status, stage and value are never a sender's to set. A
  mapping naming anything else is refused when it is saved.
- **From**: a dot path, such as `contact.email`. At a list, a step picks the first item whose
  `column_id`, `key`, `label`, `name` or `type` matches it (in any case) and reads that item's
  `string_value` or `value`; a number picks an item by position. A key that itself contains dots
  (`1.3`) is matched whole first.
- Rows are read in order, and the first row that finds a value fills its field, so a fallback row
  (work email after email) only fills what the first left empty.
- Top-level names that match a lead field fill it when no row already did.
- `country` and `industry` take their code or their name (`GB` or `United Kingdom`).

**Nothing is lost.** Whatever the mapping does not use (other than the preset's own id, timestamp
and key) is written into the lead as `Label: value` lines: into its notes for a new lead, after the
mapped message if there is one, and into the enquiry for every arrival.

## What happens to an arriving lead

The same rules apply to web forms, webhooks and lead addresses.

**Matching**, against the org's leads of any status, in order:

1. The same sender and lead id (a webhook's `lead_id`, `responseId` and so on). This is an exact
   repeat: the answer is `duplicate` and nothing changes.
2. The same email address, ignoring case and surrounding spaces.
3. The same phone number in international form. Numbers are compared as E.164, read with the org's
   default country, so `+1 (555) 123-4567` and `555-123-4567` match in a US org. A number that
   cannot be read that way (a local number in an org with no country set) never merges anything:
   a wrong merge would lose data, while a missed one only leaves a duplicate that the duplicate
   panel shows.

**No match**: a new lead with status Assigned, the webhook's source, sub-source and tags, the lead
id and the reference, assigned to the webhook's owner if that member is active. The owner is
emailed that the lead is theirs.

**A match**, depending on the lead:

- **Open** (assigned, in process, recycled): merged. Blank fields are filled and nothing is
  overwritten, so whoever knows a person's email address cannot rewrite their record. A match by
  phone number alone never takes the incoming email address, and an address another lead already
  holds is never copied. An email or phone number that differs from the lead's is not lost: it is
  written into the enquiry as `Email: ...` or `Phone: ...`. The webhook's tags are added, the
  enquiry is recorded on the lead's timeline, and the lead's active owners are emailed that the
  person enquired again. Status, owner, source and pipeline stay as they are. If the lead had no lead id
  yet, it takes this one.
- **Closed**: the same, and its status becomes Recycled, so it is open again. A lead on a won or
  lost stage of a pipeline moves to that pipeline's first open stage.
- **Converted**: no new lead, and none of the old one's fields are filled (it takes the lead id if
  it had none). The enquiry is recorded on the timeline of the contact it became (found by the
  lead's email address, and linked to that contact's account; on the lead itself when there is no
  such contact), and that contact's owners are told.

**Retries.** A webhook post with a lead id the webhook has already received and placed on a lead
answers `duplicate` with that lead, even when the first post merged into a lead that already had
another sender's id. Posts to one webhook are received one at a time, so two copies of a post
arriving together make one lead and one enquiry.

**Every arrival is an enquiry.** Each one, new lead or merge, is recorded as an `enquiry`
[interaction](../api/interactions.md) on the lead's timeline (or the contact's, for a converted
lead): inbound, with the form, webhook or address as its subject and the message, plus any
`Label: value` lines, as its text, up to 10,000 characters. A `duplicate` records nothing.
Enquiries are read-only, and they never count as answering the lead. Enquiries from before this
was added are comments, and stay comments.

Every arrival stamps the lead's **last enquiry** time (`last_enquiry_at`).

**Late posts.** When a post's own timestamp is more than 24 hours old (a sender catching up after
an outage, or an import of old form answers), the lead is created or merged as above but no
automation rules run and nobody is emailed. The last enquiry time is the post's own time.

**Web forms** differ in two ways: a web form has no lead id of its own, so it never answers
`duplicate`, and it keeps its own notification, one email to the form's notify list and the lead's
owners (the contact's owners for a converted lead, from 1.42.0), instead of the emails above.

## Limits and settings

| Setting | Default | What it limits |
|---|---|---|
| `LEAD_INTAKE_THROTTLE_MINUTE` | `120/min` | Posts per webhook per minute, from every sender together |
| `LEAD_INTAKE_THROTTLE_DAY` | `5000/day` | Posts per webhook per day |

Both count only posts that carry the key. Refused posts are limited per sending address (60 a
minute, `lead_intake_refused_ip` in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` to change it). All of
them need the shared cache (`CACHE_URL`) to hold across workers.

## API

Admins only, every route; a member gets 403 and another org's webhook id 404. With Leads switched
off the routes answer the module's 404.

| Route | Does |
|---|---|
| `GET /api/leads/intakes/` | `{"intakes": [...]}`, by name |
| `POST /api/leads/intakes/` | Create; the answer carries `key`, once |
| `GET`, `PATCH`, `DELETE /api/leads/intakes/<id>/` | Read, edit, delete (204) |
| `POST /api/leads/intakes/<id>/key/` | Replace the key; the answer carries the new `key`, once |
| `GET /api/leads/intakes/<id>/deliveries/` | `{"deliveries": [...]}`, the 50 newest within 30 days |
| `POST /api/leads/intakes/<id>/test/` | Log a `test` delivery (refused while the webhook is off); answers `{"delivery": {...}}` |

A webhook: `id`, `name`, `preset` (`generic`, `google_ads`, `tally`, `gravity_forms`), `url`,
`key_hint` (the last four characters), `key` (create and replace only), `mapping`,
`default_source` (a key from the org's active lead sources, or empty; a stored source that has
since been retired is kept), `source_detail`, `tags` (tag ids), `assignee` (a member's profile id
or null; a deactivated member cannot be chosen), `assignee_details`, `is_active`,
`last_received_at`, `last_error`, `created_at`. A new webhook with no mapping or source given
starts with its preset's. `key`, `org` and the two delivery stamps cannot be written.

A delivery: `id`, `received_at`, `result` (`created`, `merged`, `duplicate`, `test`, `rejected`),
`lead` (id or null), `external_id`, `error`.

## Lead addresses

A lead address is an [inbound mailbox](../integrations/inbound-email.md) set to make leads
(`creates: "leads"`) instead of tickets. Forward an enquiry to it, or give it to a portal or a
website form as the address its notifications go to, and each email becomes a lead through the same
service as the webhooks, so the [matching and merging](#what-happens-to-an-arriving-lead) above
apply unchanged.

**Setting one up.** On **Settings, Inbound email**, an admin creates an address that makes leads,
with the source its leads record (`email` unless chosen), a sub-source, and an owner (the mailbox's
default assignee), who owns every lead the address makes. For ownership to follow the address, make
one address per agent. When the server has an inbound domain (`INBOUND_EMAIL_DOMAIN`), **Generate an
address** makes one such as `acme-k3x9q2@in.example.com` that works at once: every generated address
arrives through the server's catch-all webhook, `POST /api/cases/inbound/email/`
([Generated addresses](../integrations/inbound-email.md#generated-addresses)). Otherwise type your own
address and deliver its mail with Amazon SES, Postmark or Mailgun to the mailbox's own webhook,
`POST /api/cases/inbound/<mailbox id>/`, as [Inbound email](../integrations/inbound-email.md#providers)
describes.

### Who the lead is

The sender is not always the person enquiring, so each email is read in this order:

1. **A forwarded email** is read for the original message inside it: Gmail's "Forwarded message",
   Apple Mail's "Begin forwarded message", and Outlook's "Original Message" or rule line when the
   subject starts with `Fwd:` or `FW:`. A forward of a forward is followed to the original. The
   forwarder's own note above it is not kept.
2. **An automated sender** is never the lead. A sender is automated when its address starts
   `noreply`, `no-reply`, `donotreply`, `do-not-reply`, `notifications`, `notification`, `alerts` or
   `mailer` (before any `+tag`), or when the email carries `Auto-Submitted` (anything but `no`) or
   `Precedence: bulk`, `list` or `junk`. This is how portal notifications arrive. The lead's email
   is then the `Reply-To`, or else the first address in the body, preferring one on a line labelled
   as an email, that is none of these: the sender, an address at the sender's domain (so a portal's
   `support@` footer is skipped), any of the org's inbound addresses, a member of the org, a
   recipient of the email, or another no-reply style address. The name comes from a `Name:` line
   (or `First name:` and `Last name:` lines), and the **sender's domain** (`portal.example.com`) becomes the
   lead's sub-source.
3. **A person's email**: the lead is the `Reply-To` when it differs from `From` (and is not a
   no-reply style address or one of the org's own), else `From`, with its display name as the name.
   The mailbox's sub-source is kept.

**A member is never made a lead.** An email from a member of the org (any profile, active or not)
that is not a forward is dropped, and so is one whose original sender, or the address it names as
the lead, is a member.

### What the lead gets

- **Email and name**, as above. An address that is not a valid email is left out.
- **Phone**: the first number on a line labelled as a phone (`Phone`, `Tel`, `Mobile`, `Cell`,
  `WhatsApp`, `Contact number`), else the first in the body, that reads as a number for the org's
  default country (any international `+` number reads without one). Toll-free and premium-rate
  numbers are skipped, since in a notification they are the portal's own. Only the body's first
  200 lines (and first 20,000 characters) are searched, and at most 20 candidate numbers are tried.
- **Title**: the subject (the original's, for a forward), up to 255 characters.
- **Notes**: the body as plain text (an HTML-only email is converted), with quoted history removed:
  `On ... wrote:` and everything below it, Outlook's reply header, and `>` lines. At most 20,000
  characters.
- **Attachments** become the lead's attachments, each through the same size limit as an upload
  (25 MB); a larger file is skipped and the lead is still made.
- **Owner, source and sub-source** from the mailbox, as above; **lead id** (`external_id`) is the
  email's `Message-ID`, with `external_source` `email:<mailbox id>`.
- **No email address and no phone number**: the lead is still made, from the subject and the body,
  and its notes start with "No email address or phone number was found in this email." Such a lead
  matches nothing, so two of them stay two leads: an automated notification never collects
  everybody into one shared "noreply" lead.

The owner is emailed about a new lead, and the owners of a merged one that the person enquired
again, as for webhooks. An email's `Date` header is not used as its time (it is the sender's
clock): a lead address always treats an email as arriving now.

### What is ignored

| Ignored | Why |
| --- | --- |
| Bounces (delivery-status reports) | Not an enquiry |
| Email from the address itself, or from any of the org's inbound addresses | A loop |
| Email the provider marked as spam or a virus, or as faking its sender | SES's spam or virus verdict `FAIL`, or a DMARC `FAIL` where the sender's domain asks for `quarantine` or `reject`; Postmark's `X-Spam-Status: Yes`, Mailgun's `X-Mailgun-Sflag: Yes` |
| A member's own email | See above |
| The same email again | Each email is recorded once per org by its `Message-ID` (by a hash of its content when it has none), so a provider's repeat delivery never makes or merges a lead twice, and the same email to two lead addresses in one org arrives once |
| More than 300 emails a day | Per address, over the last 24 hours, counting the emails that made or merged a lead; `INBOUND_EMAIL_DAILY_LIMIT` changes the number. An email over the limit is not recorded, so the same email can still arrive at the org's other lead addresses |

Unlike ticket addresses, lead addresses keep `Auto-Submitted`, bulk, mailing-list and
`X-Autoreply` mail, because portal notifications carry those headers, and a lead address never
replies, so there is no mail loop to guard against. A lead address is also never offered as the
`Reply-To` of a ticket's emails.

Every email to a lead address leaves an `EmailMessage` row on the mailbox (which is what its "last
received" time reads), holding no sender, subject or body: those are on the lead, and deleting the
lead must not leave a copy behind. Ignored emails leave one too, except past the daily limit or
past that many ignored emails in a day; a
member's own email is recorded against that one address only, never for the whole org. The same
email sent to a lead address and a ticket address of one org reaches both.

**Leads switched off.** A lead address takes mail only while Leads is on, and a ticket address only
while Tickets is on; mail to the other answers as for an unknown address. Switching Leads off is
refused while a lead address is active (`Leads still receives email at ...`), as Tickets is for
ticket addresses.

### Lead address API

The [inbound mailbox endpoints](../integrations/inbound-email.md#configuring-a-mailbox) carry the
lead address fields, and work while Tickets or Leads is on:

| Field | Notes |
| --- | --- |
| `creates` | `tickets` (the default) or `leads`. Choosing one whose module is switched off is a `400`. |
| `lead_source` | A key from the org's active [lead sources](../api/leads.md); defaults to `email`. A source retired later stays on the mailbox. |
| `lead_source_detail` | The sub-source, up to 100 characters. Automated mail records the sender's domain instead. |
| `is_generated` | Read only: the address was made under the server's inbound domain. |
| `generate_address` | Create only, write only: `true` makes the address instead of taking a typed one. |

`GET /api/cases/mailboxes/` also answers `generated_domain`: the inbound domain, or `null` when this
server makes no addresses.
