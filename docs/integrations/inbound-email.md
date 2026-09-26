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
   if the reply arrives inside the configured reopen window.

Every message, including dropped ones, is recorded as exactly one `EmailMessage` row
(`org` + `message_id` is the idempotency key, so a provider retry never creates a duplicate case).
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
| `topic_arn` | Leave blank on create. It's pinned automatically from the first verified SNS `SubscriptionConfirmation`, not set by hand. See below. |
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

Point an AWS SES receipt rule (via an SNS topic, "SNS Notification with full content" or the
older JSON-envelope form, the parser handles both) at:

```
POST /api/cases/inbound/<mailbox_id>/
```

This route is deliberately public: `authentication_classes = ()`, `permission_classes =
(AllowAny,)`, because SNS has no way to send your BottleCRM credentials. The `<mailbox_id>` in the
URL is what scopes each notification to one org: the view looks the mailbox up by that id (and
`is_active=True`) before doing anything else, and because the webhook bypasses
`RLSContextMiddleware` entirely (there's no authenticated session to derive an org from), the view
sets the RLS session variable by hand from `mailbox.org_id` before touching any other org-scoped
table.

On a `Notification`, the view acks with:

```json
{"ok": true, "case_id": "<uuid or null>", "dropped": false, "reason": "", "created_case": true}
```

`dropped`/`reason` reflect the spam-filtering outcome from [the pipeline](#how-inbound-email-becomes-a-ticket)
above; the response is still `200` even when the message was dropped, and deliberately so: SES
already accepted the message from the original sender, and returning a `4xx` here would just
trigger pointless provider retries of a message that was correctly classified as noise, not lost.

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
   account can create a topic and have SNS sign messages for it. Each mailbox is pinned to the
   exact `TopicArn` it should accept: the pin is captured automatically, trust-on-first-use, from
   the first signature-verified `SubscriptionConfirmation` a mailbox receives, and every
   `Notification` after that must carry the exact same `TopicArn` or it's rejected. A mailbox with
   no pin yet rejects every `Notification` outright (only a `SubscriptionConfirmation` can
   establish the pin), and once pinned, a mailbox does not silently re-pin to a different topic,
   without this, anyone who learned a mailbox's UUID could point their own SNS topic at it and have
   AWS legitimately sign forged mail on their behalf, which, because the pipeline threads replies
   onto existing cases. Means injecting messages into a live customer conversation, not just
   spam tickets.

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

`topic_arn` is still returned, to admins only, and is stripped for regular members along with
`has_webhook_secret`: it embeds your AWS account id. That is the reason to treat mailbox
configuration as admin-only, rather than any forgery risk from `webhook_secret`, which cannot exist
until a provider integration starts checking it.
