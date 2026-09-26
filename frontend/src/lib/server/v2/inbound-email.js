/**
 * Inbound mailboxes: the wiring behind `/settings/inbound-email`.
 *
 * Server-only. Reads `GET /cases/mailboxes/`, the addresses that turn email
 * into tickets. Each row carries its defaults (priority, case type, assignee)
 * plus two backend-computed metrics, `cases_last_30d` (tickets opened from the
 * address in 30 days) and `last_received_at` (newest mail seen), both attributed
 * exactly via the `EmailMessage.mailbox` FK. Totals carry `count`, `active`, and
 * the org's `cases_last_30d`.
 *
 * NO SECRETS. `webhook_secret` is the credential that proves a delivery really
 * came from the provider. Anything holding it can forge tickets into this org.
 * The backend already strips it for non-admins, but this layer drops it for
 * everyone: it is never needed to render the page, so it never reaches the
 * browser, admin or not. `topic_arn` embeds the AWS account id and is held to
 * the same bar. Rotation and a one-time reveal on create are an explicit,
 * separate feature this task does not build, not a field on a page you can
 * browse to.
 *
 * The rule extends to the write side, below: `CREATE_FIELDS` and
 * `UPDATE_FIELDS` exclude both fields too, on purpose, not by oversight. See
 * the comment above `CREATE_FIELDS` for why omitting them is strictly better
 * than any form could do.
 *
 * Create, edit, turn off, turn on and delete are wired below. The backend
 * returns `default_assignee` as a full profile object (name under
 * `user_details.name`); the page wants `{ id, name }`.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';

/** Profile → the `{ id, name }` the card reads, or null. */
function shapeAssignee(p) {
  if (!p) return null;
  return { id: p.id, name: p.user_details?.name ?? null };
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ mailboxes: any[], totals: any, can_edit: boolean }>}
 */
export async function getMailboxes({ cookies }) {
  const { mailboxes, totals } = await apiRequest('/cases/mailboxes/', {}, { cookies });
  return {
    // Rebuild each row from a fixed allowlist of fields; webhook_secret and the
    // imap_* columns are deliberately not among them, so a secret can never ride
    // along into the page even if the serializer starts returning one.
    mailboxes: (mailboxes ?? []).map((m) => ({
      id: m.id,
      address: m.address,
      provider: m.provider,
      is_active: m.is_active,
      default_priority: m.default_priority,
      default_case_type: m.default_case_type,
      default_assignee: shapeAssignee(m.default_assignee),
      cases_last_30d: m.cases_last_30d ?? 0,
      last_received_at: m.last_received_at ?? null,
      // Whether the mailbox has its SNS TopicArn pin, not the ARN itself. An
      // active SES address with no pin rejects every notification, so without
      // this the page can only read `is_active` and draws a never-connected
      // address as one creating tickets. See `./delivery.js`. Defaults to
      // false, which reads as "not connected yet": failing closed is the safe
      // direction for a status this page draws a green pill from.
      has_topic_arn: m.has_topic_arn === true
    })),
    totals: totals ?? { count: 0, active: 0, cases_last_30d: 0 },
    // A display hint: POST/PUT/DELETE on `/cases/mailboxes/` each start with
    // `_is_admin(request.profile)` server-side and 403 regardless of what this
    // says. This only decides whether the page offers the controls. GET itself
    // is not role-gated, so a member can still see this list.
    can_edit: viewerIsAdmin(cookies)
  };
}

/** No `webhook_secret`, no `topic_arn`.
 *
 *  `webhook_secret` is reserved for providers that sign deliveries with a
 *  shared secret, none of which are implemented: nothing in the backend
 *  compares the column, and SES delivery is authenticated by the SNS signature
 *  plus the `topic_arn` pin. An earlier version of this comment said the view
 *  mints one when the body carries none, which it did until the field became
 *  write-only, and the page repeated the claim to admins as though a
 *  credential were protecting their mail. It is admin-writable so a
 *  provider-issued key can be stored ahead of an integration, and a form field
 *  for it would mean a credential travels to a browser and back on every edit
 *  while an empty one blanks the column, so this page has neither.
 *
 *  `topic_arn` is set by the webhook from the first verified
 *  SubscriptionConfirmation, so nothing here should be writing it either. Its
 *  presence reaches the page as the `has_topic_arn` boolean above. */
const CREATE_FIELDS = [
  'address',
  'provider',
  'default_priority',
  'default_case_type',
  'default_assignee_id',
  'is_active'
];

/** `address` is the mailbox's identity and its uniqueness key. It is not
 *  frozen by the backend (`validate_address` only checks duplicates on
 *  create, and `put` is partial), so an edit may change it. It stays in the
 *  update allow-list for that reason, with a hint on the form saying mail to
 *  the old address stops becoming tickets. */
const UPDATE_FIELDS = CREATE_FIELDS;

/** @param {string[]} allowed @param {{ [key: string]: any }} values */
function buildBody(allowed, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of allowed) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }
  // Two nullable fields whose "none selected" option is an empty `<option>`
  // value. `''` fails the ChoiceField and the PK lookup alike; `null` is what
  // clears them, and both are `allow_null=True` on the serializer.
  for (const nullable of ['default_case_type', 'default_assignee_id']) {
    if (body[nullable] === '') body[nullable] = null;
  }
  if (body.is_active !== undefined) body.is_active = Boolean(body.is_active);
  if (body.address !== undefined) {
    body.address = String(body.address).trim().toLowerCase();
  }
  return body;
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function createMailbox({ cookies }, values) {
  const body = buildBody(CREATE_FIELDS, values);
  if (!body.address) throw new Error('A mailbox needs an address.');
  return await apiRequest('/cases/mailboxes/', { method: 'POST', body }, { cookies });
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function updateMailbox({ cookies }, id, values) {
  if (!id) throw new Error('Which mailbox? No mailbox id was given.');
  const body = buildBody(UPDATE_FIELDS, values);
  return await apiRequest(`/cases/mailboxes/${id}/`, { method: 'PUT', body }, { cookies });
}

/**
 * Delete a mailbox, permanently.
 *
 * `InboundMailboxDetailView.delete` calls `obj.delete()`. Hard delete, and it
 * takes the row's `webhook_secret` with it, so mail already in flight from the
 * provider stops verifying the moment this runs, not just stops opening tickets.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function deleteMailbox({ cookies }, id) {
  if (!id) throw new Error('Which mailbox? No mailbox id was given.');
  return await apiRequest(`/cases/mailboxes/${id}/`, { method: 'DELETE' }, { cookies });
}
