# Inbound email

BottleCRM can turn email sent to a support address into a case (support ticket), and thread
replies onto the same case automatically. This is an inbound webhook: BottleCRM *receives* mail
through it. To have BottleCRM push events to a URL you configure (a ticket created, a public reply
added, and so on), see [Webhooks](webhooks.md).

## How inbound email becomes a ticket

The pipeline (`backend/cases/inbound/pipeline.py`, entry point `ingest()`) runs the same four
steps for every message that reaches a configured mailbox:

1. **Spam/noise filtering** (`cases/inbound/spam.py`). A message is dropped, recorded, but no
   case touched, if it looks like a bounce, carries `Auto-Submitted: <anything but "no">`,
   `Precedence: bulk|list|junk`, `X-Autoreply: yes`, or both `List-Id` and `List-Unsubscribe`
   headers (a mailing-list signature; either header alone isn't enough, since transactional mail
   sometimes carries `List-Unsubscribe` on its own). A message with no `Message-ID` at all is also
   treated as a drop, without one there's nothing to thread or de-duplicate on.
2. **Threading** (`cases/inbound/threading.py`, `find_existing_case`). `In-Reply-To` and every id
   in `References` are merged into one de-duplicated candidate list, and a **single** query
   matches that whole list against prior messages' `Message-ID`s, ordered by most-recently-received,
   so if more than one candidate id matches, the most recently received message wins; it is not
   "`In-Reply-To` first, then `References`" as two separate lookups. Only after that does the match
   fall through to a case's own thread id (`external_thread_id`, including one inherited from a case
   merged into it, via `alt_thread_ids`) and, as a last resort, a `[Case #<8-hex-chars>]` prefix
   surviving in the subject line. If the matched case was since merged into another, the reply is
   attached to the surviving primary instead. A match on the subject tag alone is trusted only when
   the sender is already a contact on that case: the tag rides on every outbound reply, so anyone
   who has seen one subject line could copy it. Mail from anybody else carrying only the tag becomes
   a new ticket (named without the tag), and the sender is never added to the tagged case.
3. **Contact resolution** (`cases/inbound/contacts.py`). The `From:` address is matched
   case-insensitively against existing contacts in the mailbox's org; if none matches, a new
   `Contact` is auto-created from the display name (or the email's local part, if there's no
   display name) and linked to the case.
4. **Case creation or update.** No match in step 2 creates a new `Case`. Subject becomes the name
   (truncated to 64 characters), body becomes the description, and **`status` is always `"New"`**
   (`pipeline.py`: `status="New"` is hardcoded. The mailbox has no `default_status` field, so
   don't go looking for one). `priority` and `case_type` do come from the mailbox's
   `default_priority`/`default_case_type`, and if the mailbox has a `default_assignee`, that
   profile is added to the new case's `assigned_to`. A match reuses the existing case, attaches the
   contact if not already linked (which, after the rule above, only a header match can do), and can reopen a closed case (subject to the org's reopen policy)
   if the reply arrives inside the configured reopen window. The window counts calendar days since
   `closed_on` on the org's day (`Org.timezone`), which the webhook activates once it has found the
   mailbox's org; the request is anonymous, so otherwise it would be the server's UTC day.

Every message that reaches the pipeline, including dropped ones, is recorded as exactly one
`EmailMessage` row (`org` + `message_id` is the idempotency key, so a provider retry never creates
a duplicate case). Mail addressed to a different mailbox never reaches it: see
[Which mailbox a notification belongs to](#which-mailbox-a-notification-belongs-to).
That gives admins a forensic trail even for the mail that never became a ticket, and it's what
[the mailbox list's per-mailbox counts](#configuring-a-mailbox) are computed from.

## How replies go back out

A public reply on a case (and every status change) is emailed to each contact on the case, apart
from whoever wrote it, by `notify_portal_contacts` in `backend/cases/tasks.py`. Internal notes are
never emailed. A reply email carries the reply text itself, escaped, with a link to the case in
the customer portal. Each email is threaded: it gets a fresh `Message-ID`, `In-Reply-To` and
`References` built from the case's known messages (the thread root plus the latest ten), and the
subject `Re: <case name> [Case #<8-hex-chars>]`. Each one sent is recorded as an outbound
`EmailMessage` row, so a customer who answers by email lands on the same case through step 2
above, by header or, if their mail client dropped the headers, by the subject tag (from a contact
already on the case only).

Amazon SES replaces any `Message-ID` it is given with its own. After a send through
`django_ses.SESBackend`, the row stores the id the customer actually received:
`<ses-id>@email.amazonses.com` in `us-east-1`, `<ses-id>@<region>.amazonses.com` elsewhere, with
the region read from `AWS_SES_REGION_NAME`. Other backends (SMTP, console) keep the id we
generated. A case with no thread root yet (opened in the portal, by an agent, or from a web form)
gets one minted from our own domain on its first email, stored in `external_thread_id` and
carried in `References` of every email on the case, so a reply still threads by header if the
stored `Message-ID` ever fails to match.

A ticket opened by a public [web form](web-forms.md) is not sent status-change emails or a CSAT
survey until an agent has posted a public reply on it. Anybody can submit a form with anybody's
address, so until an agent chooses to engage, mailing that address would let a stranger make the
org's sender write to anyone. The agent's reply itself is emailed as usual, and from then on the
ticket is mailed like any other.

`Reply-To` is the mailbox the case's latest inbound email arrived through, or else the org's
mailbox when it has exactly one. Only a mailbox that can take mail counts: active, SES, with its
topic pinned. With none, the email has no `Reply-To` and asks the customer to answer through the
portal instead.

## Configuring a mailbox

`InboundMailbox` (`backend/cases/models.py`) is a per-org row managed at
`GET`/`POST /api/cases/mailboxes/` and `GET`/`PUT`/`DELETE /api/cases/mailboxes/{id}/`
(`InboundMailboxListCreateView` / `InboundMailboxDetailView`,
`backend/cases/inbound_views.py`), admin-only for every write (`POST`, `PUT`, `DELETE` all check
`role == "ADMIN"` or `is_admin`; a non-admin gets `403`). Reads are open to any org member.

The model declares four provider choices (`ses`, `mailgun`, `postmark`, `imap`), but **only `ses`
is actually implemented today**. The webhook itself checks this: any mailbox whose `provider` is
not `"ses"` returns `501 Not Implemented` for every notification it receives
(`InboundMailboxWebhookView.post`, `backend/cases/inbound_views.py`). The `imap_host` /
`imap_port` / `imap_username` / `imap_password_enc` fields exist on the model already so that a
future IMAP implementation doesn't need a schema migration, but nothing reads them yet, setting
`provider` to anything but `ses` configures a mailbox that will reject every message sent to it.

Fields worth knowing when creating one:

| Field | Notes |
| --- | --- |
| `address` | The inbound email address. `validate_address` pre-checks for a case-insensitive duplicate in your org on **both** create and update, excluding the mailbox from its own check so saving a record without changing its address still works, and returns a clean `400` when one exists. This used to be gated on `self.instance is None`, so a `PUT` that renamed a mailbox onto an address the org already had skipped the check and hit the database's `UniqueConstraint` on `(org, address)` instead, giving an unhandled `IntegrityError` (`500`) on every such rename rather than a `400`. |
| `provider` | Must be `ses` for the mailbox to accept mail (see above). |
| `webhook_secret` | Optional, and unused today. Nothing is generated for you, and the API never returns it. See [Securing the endpoint](#securing-the-endpoint). |
| `has_webhook_secret` | Read-only boolean, admin-only, telling you whether a secret is stored without disclosing it. |
| `topic_arn` | The SNS Topic ARN this mailbox accepts mail from, in the form `arn:aws:sns:<region>:<12-digit account id>:<topic name>`. Admin-only, to read and to write: an admin can set it on create, change it, or clear it with `""`. Anything that is not a well-formed topic ARN in the commercial `aws` partition (no `aws-cn`, no `aws-us-gov`, no `.fifo` topic) is a `400`. Left blank, it can be pinned by a confirmation from an allowed AWS account. See [Securing the endpoint](#securing-the-endpoint). |
| `has_topic_arn` | Read-only boolean, visible to every member: whether the mailbox has a Topic ARN yet. Without one it refuses every message. |
| `default_priority`, `default_case_type` | Set directly on any new case this mailbox creates; both optional. There is no `default_status`. A new case's `status` is always `"New"`, not configurable per mailbox (see [above](#how-inbound-email-becomes-a-ticket)). |
| `default_assignee_id` | Optional. If set, that profile is added to the new case's `assigned_to` (a many-to-many "add", not a status or ownership field of its own). |
| `is_active` | An inactive mailbox's webhook returns `404` for every notification, same as a mailbox id that doesn't exist. Set this to disable a mailbox without deleting its history. |

`GET /api/cases/mailboxes/` adds two per-mailbox fields on top of the serializer above,
`cases_last_30d` and `last_received_at`, plus an org-wide `totals` object
(`{"count", "active", "cases_last_30d"}`), all computed by `_mailbox_analytics`
(`backend/cases/inbound_views.py`) from the `EmailMessage.mailbox` foreign key, not stored on the
mailbox row itself. `cases_last_30d` counts distinct cases *created* in the last 30 days that have
an inbound message through that mailbox (a reply to an older case doesn't count as a new ticket);
`last_received_at` is the newest inbound message's `received_at`, including dropped ones, the
address still received mail even if the pipeline discarded it. Neither field is present on
`GET /api/cases/mailboxes/{id}/`, which returns only the plain serializer.

## The SNS webhook

Point an AWS SES receipt rule's SNS action at a topic, and subscribe the topic to:

```
POST /api/cases/inbound/<mailbox_id>/
```

This route is deliberately public: `authentication_classes = ()`, `permission_classes =
(AllowAny,)`, because SNS has no way to send your BottleCRM credentials. The `<mailbox_id>` in the
URL is what scopes each notification to one org, in this order:

1. **The route is reachable without an org claim.** `RequireOrgContext`
   (`backend/common/middleware/rls_context.py`) refuses every request that has no org with `403
   "Organization context is required"`. This one route is exempt by its URL name
   (`EXEMPT_VIEW_NAMES`), not by a path prefix, so the mailbox admin endpoints beside it still
   require an org like everything else.
2. **The org is resolved before any org-scoped read.** `inbound_mailbox` is protected by RLS, and
   an anonymous request starts with an empty `app.current_org`, under which a non-superuser
   database role sees no mailbox at all. So the view first looks the org up in the unscoped
   `portal_access_token` table, keyed on the SHA-256 of the mailbox id in canonical UUID form
   (resource type `inbound_mailbox`), the same mechanism the invoice, estimate and CSAT links use
   (`backend/docs/PORTAL_RLS.md`). A row is registered when a mailbox is created and removed when
   it is deleted; migration `common/0049` registered every mailbox that already existed.
3. **The context is set, then the mailbox is read** within that org, with `is_active=True`. Only
   then do the provider check, the signature check, the topic pin and the recipient check below
   run.

An id with no lookup row, an inactive mailbox, and a deleted mailbox all answer the same `404`
`{"error": true, "errors": "Mailbox not found"}`. Deactivating keeps the lookup row, so
reactivating needs nothing else. An id that is not a UUID at all never reaches the view: the
router answers a plain `404` for it, as it does on every id route. Any UUID spelling the router
accepts (upper case, no hyphens, braces) resolves to the same mailbox.

**Before django-crm 1.13.0 this endpoint never delivered on a correctly configured production
database**, for both reasons above: the middleware answered `403` to every SNS request, and past
that, the mailbox lookup ran under an empty context and answered `404`. A superuser database role
(the usual local setup) bypasses RLS and hid the second, and every test signed in as an admin,
which hid the first. Upgrading changes no URL, and `manage.py migrate` runs the backfill. The
`SubscriptionConfirmation` was refused too, though, so a subscription created while the endpoint
was failing is still pending in AWS and its mailbox is unpinned (`has_topic_arn` is `false`).
Once the upgrade is live, either enter the mailbox's Topic ARN in the app, or set
[`INBOUND_SNS_ACCOUNT_IDS`](../reference/environment-variables.md#inbound-email) to the AWS
account that owns the topic. Then request confirmation again for that subscription in the SNS
console, or delete and recreate it if AWS has already expired it. A confirmation from any other
account pins nothing.

On a `Notification`, the view acks with:

```json
{"ok": true, "case_id": "<uuid or null>", "dropped": false, "reason": "", "created_case": true}
```

`dropped`/`reason` reflect the spam-filtering outcome from [the pipeline](#how-inbound-email-becomes-a-ticket)
above; the response is still `200` even when the message was dropped, and deliberately so: SES
already accepted the message from the original sender, and returning a `4xx` here would just
trigger pointless provider retries of a message that was correctly classified as noise, not lost.

### Which mailbox a notification belongs to

The SNS `Message` is read in one of two shapes:

- **SES's JSON notification** (`"notificationType": "Received"`), which is what the SNS receipt
  action publishes. The raw email is in `content`. When the action's **Encoding** is Base64 the
  content is decoded first; UTF-8 content is used as it is. Both encodings work.
- **A bare raw RFC 5322 email**, for a publisher other than SES posting straight to the topic.

A notification is ingested only when the mailbox's `address` is one of the message's recipients.
With the SES notification the recipients are `receipt.recipients`: the envelope `RCPT TO`
addresses the receipt rule matched. SES sets them, they include Bcc recipients, and the sender
cannot forge them, so the `To` and `Cc` headers are ignored in this shape. An SES notification
with no `receipt.recipients` matches nothing. With a bare raw email there is no envelope, so the
recipients are every `Delivered-To` and `X-Original-To` header plus `To` and `Cc`; those are
headers, written by whoever published the message, so this shape is only as trustworthy as the
publisher on the pinned topic. Matching ignores case, surrounding spaces and display names, and is
otherwise exact: `support+billing@acme.com` is not `support@acme.com`.

A notification for any other address is acknowledged and discarded:

```json
{"ok": true, "dropped": true, "reason": "not_addressed_to_mailbox"}
```

It is a `200`, so SNS does not retry, and nothing is written in the mailbox's org: no case, and
unlike a spam drop, no `EmailMessage` row either, because the mail belongs to another mailbox
and a row here would copy it into this org. The server logs a warning naming the mailbox id only.

This is what makes it safe for **one SNS topic to serve several mailboxes**, in one org or many:
subscribe each mailbox's URL to the topic (a platform-wide SES receipt rule, say, with every
mailbox pinned to a topic in the account listed in `INBOUND_SNS_ACCOUNT_IDS`), and each mailbox
ingests only its own mail. Before django-crm 1.13.0 there was no recipient check, so every
mailbox subscribed to a shared topic turned every other mailbox's mail, from every org on it,
into its own tickets.

## Securing the endpoint

Because the endpoint takes no credential, trust is established entirely by verifying the message
itself, in two layers, and it takes both:

1. **SNS signature verification** (`cases/inbound/sns.py`, `verify_sns_message`). The payload's
   `Signature` is checked against the certificate at its `SigningCertURL`, which is pinned to the
   `sns.<region>.amazonaws.com` host family, fetched with redirects disabled and TLS verified
   against the system CA store, and itself checked for a matching Common Name and a currently-valid
   validity window before its public key is trusted. This proves the payload was genuinely signed
   by *some* SNS topic in *some* AWS account, nothing more.
2. **TopicArn pinning.** A valid SNS signature alone isn't enough, because anyone with an AWS
   account can create a topic, have SNS sign messages for it, and subscribe any HTTPS endpoint
   to it, including this one. Each mailbox is therefore pinned to the exact `TopicArn` it should
   accept, and every message, `SubscriptionConfirmation` or `Notification`, must carry that exact
   `TopicArn` or it is refused. A mailbox gets its pin one of two ways:
   - **An admin enters it** (`topic_arn` on the mailbox, from the web or mobile inbound email
     settings or the API). From then on only that ARN is accepted, whatever
     `INBOUND_SNS_ACCOUNT_IDS` says.
   - **A confirmation from an allowed account.** While a mailbox has no pin, a
     signature-verified `SubscriptionConfirmation` pins its `TopicArn` only when the topic's AWS
     account id (the fifth `:`-separated field of the ARN) is listed in
     [`INBOUND_SNS_ACCOUNT_IDS`](../reference/environment-variables.md#inbound-email). The
     confirmation is then fetched from its `SubscribeURL` as usual.

   With neither, every message is refused: a confirmation from any other account, one with a
   malformed or missing `TopicArn`, and every `Notification` to an unpinned mailbox. A refused
   confirmation is never fetched, so the subscription stays pending in AWS. Once pinned, a
   mailbox never re-pins itself; only an admin can change or clear the ARN. Before django-crm
   1.13.0 the first confirmation pinned whatever topic it came from, so anyone who read a
   mailbox id (every org member can list them) could subscribe it to their own topic first and
   then open tickets as any sender, including replies threaded into live customer conversations.

   Which to use:
   - **Self-hosting:** set `INBOUND_SNS_ACCOUNT_IDS` to the AWS account that owns your SES
     receipt-rule topics, then subscribe each mailbox URL and it pins itself. Or leave it unset
     and paste each topic's ARN into the mailbox before subscribing.
   - **Hosted BottleCRM:** the platform sets its own AWS account, so mailboxes wired up by the
     platform pin themselves. An org that routes mail through a topic in its own AWS account
     enters that Topic ARN on the mailbox instead.

Both failure modes return the same generic `403` (`"Signature verification failed"`) rather than
distinguishing "bad signature" from "wrong topic". A caller who gets past the existence check
below can't tell which of the two rejected them, or what the pinned `TopicArn` is. That existence
check is a separate, earlier step: a `mailbox_id` that doesn't match any active mailbox returns
`404` before either check above ever runs, which does let a caller distinguish "this id belongs to
no mailbox" from "this id belongs to a mailbox, but the request failed verification" (a `403`, a
`501` for an unsupported provider, and so on): a real, if narrow, way to enumerate valid mailbox
ids, notwithstanding the source comment's stated intent not to leak that. Mailbox ids are UUIDs,
not sequential, so this isn't practically brute-forceable, but it's not the airtight non-disclosure
the comment claims either.

**`webhook_secret` is not part of this verification.** Nothing in
`InboundMailboxWebhookView.post` reads or compares it. The two checks above are what actually gate
the endpoint, and the field is a placeholder for providers that sign with a shared secret, none of
which are implemented. Two consequences follow, and both changed recently:

- **Nothing generates one.** Creating a mailbox used to mint a `secrets.token_urlsafe(32)` when the
  body carried none. A random value that no code compares, and that (see below) cannot be read back,
  is indistinguishable from no value at all except that it makes a mailbox look configured. New
  mailboxes now leave the column empty unless you set it.
- **The API never returns it.** The field is `write_only`, so an admin can store a provider-issued
  key but no response contains it. It used to be returned to admins on both the list and the detail
  endpoint, which meant an admin's session, or any personal access token that admin had minted,
  could read every mailbox's stored secret out of a list response. Admins get `has_webhook_secret`,
  a boolean, instead. If you lose a stored key, overwrite it with a `PUT`.

`topic_arn` is returned to admins only, and is stripped for regular members along with
`has_webhook_secret`: it embeds your AWS account id. Members still get `has_topic_arn`. That is the reason to treat mailbox
configuration as admin-only, rather than any forgery risk from `webhook_secret`, which cannot exist
until a provider integration starts checking it.
