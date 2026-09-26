/**
 * Contacts: the fourth v2 module wired to the real API.
 *
 * Same rules as `leads.js`, `deals.js` and `accounts.js`: this file lives under
 * `$lib/server` because SvelteKit refuses to bundle that directory into client
 * code and the access token is an httpOnly cookie. The org is never a
 * parameter; it is a claim inside the JWT and the backend reads it from there.
 *
 * WHY THIS MODULE, AND WHY NOW
 * Three wired pages already show people: a lead's converted contact, the
 * deal's contact rail, the account's people rail, and not one of them could
 * link to a person, because `/contacts/<uuid>` answered 404. The rails were
 * written as dead text for exactly that reason. Contacts was also the last
 * object in the core record graph still on fixtures: leads become an account,
 * a contact and a deal, and three quarters of that sentence was live.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * - `relationship` ("Champion", "Blocker") is gone for the fourth time. Contact
 *   has `title` and `department`. It was a table column, a rail row, a subtitle
 *   under every colleague and part of the headline sentence.
 * - `last_activity_at` is gone. Nothing records when anyone last spoke to a
 *   contact: Lead has `last_contacted`, Contact has nothing like it, and there
 *   is no activity table to derive one from. The list sorted by it, coloured a
 *   column by it, and the detail page built its whole "gone quiet" argument on
 *   it. What is real is `updated_at`, when the record was last edited, and
 *   that is what the pages show, under a label that says so.
 * - `account_id` was a single account. A contact is joined to an account twice
 *   over (`Contact.account`, the model's "primary", and membership of
 *   `Account.contacts`) and the second is many-to-many: people here belong to
 *   nought, one, two or three accounts. Both links are published now, and this
 *   layer picks the primary if there is one and the first membership otherwise.
 * - `organization` survives but is demoted. It is free text and, across the
 *   seeded org, routinely names a different company from the account the person
 *   is actually attached to, so it is shown only where there is no account
 *   link to show instead, labelled as what somebody typed.
 *
 * WHAT THE FIXTURE GOT RIGHT
 * Splitting "do not call" from "inactive": one is a rule about how you may
 * contact this person, the other is whether they still work there. Both are
 * real fields and both are still marked separately.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';
import { attachmentHref } from '$lib/server/v2/files.js';

/** See the note on FILTER_FIELDS in tickets.js. */
export const FILTER_FIELDS = ['assigned_to', 'tags', 'city'];

/** DRF decimals arrive as strings. `null` stays `null`. It means "not recorded". */
function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * @param {any} profile
 */
function profileName(profile) {
  return profile?.user_details?.email || profile?.user?.email || 'Unknown';
}

/**
 * Which company to print, and how sure we are.
 *
 * `account_detail` is the FK the model calls primary; `linked_accounts` is
 * membership of `Account.contacts`. They are independent, so this returns the
 * primary where there is one, the first membership otherwise, and how many
 * others there are. A person attached to three accounts should not silently
 * look like they belong to one.
 *
 * @param {any} contact
 */
function accountLink(contact) {
  const linked = contact.linked_accounts ?? [];
  const primary = contact.account_detail ?? linked[0] ?? null;
  const others = linked.filter((/** @type {any} */ row) => row.id !== primary?.id);
  return { account: primary, other_accounts: others };
}

/**
 * The subset of a Django contact the v2 pages read.
 *
 * @param {any} contact
 */
function toRow(contact) {
  const owners = contact.assigned_to ?? [];
  const { account, other_accounts } = accountLink(contact);
  return {
    id: contact.id,
    first_name: contact.first_name ?? '',
    last_name: contact.last_name ?? '',
    name: [contact.first_name, contact.last_name].filter(Boolean).join(' ').trim(),
    title: contact.title ?? '',
    department: contact.department ?? '',
    // Free text, and frequently not the same company as `account`.
    organization: contact.organization ?? '',
    account,
    other_accounts,
    email: contact.email ?? '',
    phone: contact.phone ?? '',
    do_not_call: Boolean(contact.do_not_call),
    linkedin_url: contact.linkedin_url ?? '',
    city: contact.city ?? '',
    state: contact.state ?? '',
    country: contact.country ?? '',
    address_line: contact.address_line ?? '',
    postcode: contact.postcode ?? '',
    description: contact.description ?? '',
    is_active: contact.is_active !== false,
    owner: owners.length ? profileName(owners[0]) : null,
    owner_count: owners.length,
    created_at: contact.created_at,
    updated_at: contact.updated_at ?? null
  };
}

/**
 * People in this org, most recently added first.
 *
 * `count` is the size of the whole filtered queryset and `results` is one page
 * of it, so the header counts the former. `active_count` / `inactive_count`
 * are counted before the active filter splits the list, which is what lets the
 * page say how many it is holding back rather than guessing from the rows it
 * happens to hold.
 *
 * The API narrows this list for non-admins to contacts they created or are
 * assigned to, so what comes back is already what this person may see.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listContacts({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response = await apiRequest(`/contacts/?${query}`, {}, { cookies });
  const rows = (response.results ?? []).map(toRow);

  return {
    results: rows,
    totals: {
      count: response.count ?? rows.length,
      active: response.active_count ?? rows.length,
      inactive: response.inactive_count ?? 0,
      shown: rows.length,
      // Two facts worth surfacing on a page about reaching people: how many of
      // them you are not allowed to phone, and how many have no email at all.
      do_not_call: rows.filter((/** @type {any} */ row) => row.do_not_call).length,
      no_email: rows.filter((/** @type {any} */ row) => !row.email).length
    }
  };
}

/**
 * One person, and everything that person is involved in.
 *
 * All of it arrives in a single request. `ContactDetailView.get` now returns
 * the deals and cases this contact is named on and the colleagues at their
 * account alongside the tasks it already returned. Before that, "their deals"
 * could not be asked for at all, because Contact is on the far side of a
 * many-to-many from both Opportunity and Case.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getContact({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  const contact = toRow(response.contact_obj);

  return {
    contact,
    owners: (response.contact_obj.assigned_to ?? []).map(profileName),
    teams: (response.contact_obj.teams ?? []).map((/** @type {any} */ team) => team.name),
    deals: (response.opportunities ?? []).map((/** @type {any} */ deal) => ({
      id: deal.id,
      name: deal.name ?? '',
      stage: deal.stage,
      // Named and classified by the deal's own pipeline, never by its code.
      stage_label: deal.stage_label ?? deal.stage,
      stage_kind: deal.stage_kind ?? null,
      amount: num(deal.amount) ?? 0,
      // Priced in the deal's own currency; the API sends the org's default for
      // a deal with none. The page adds up the open ones per currency.
      currency: deal.currency || 'USD',
      closed_on: deal.closed_on ?? null
    })),
    // Computed on the server over every open deal the viewer may open. The
    // `deals` list stops at 10 rows, so totalling it undercounted.
    openDeals: {
      count: response.open_deals?.count ?? 0,
      by_currency: (response.open_deals?.by_currency ?? []).map((/** @type {any} */ row) => ({
        currency: row.currency,
        amount: num(row.amount) ?? 0
      }))
    },
    tickets: (response.cases ?? []).map((/** @type {any} */ ticket) => ({
      id: ticket.id,
      name: ticket.name ?? '',
      status: ticket.status,
      priority: ticket.priority,
      created_at: ticket.created_at
    })),
    tasks: (response.tasks ?? []).map((/** @type {any} */ task) => ({
      id: task.id,
      title: task.title ?? '',
      status: task.status,
      priority: task.priority,
      due_date: task.due_date ?? null
    })),
    colleagues: (response.colleagues ?? []).map((/** @type {any} */ person) => ({
      id: person.id,
      name: [person.first_name, person.last_name].filter(Boolean).join(' ').trim(),
      title: person.title ?? ''
    })),
    activity: buildContactActivity(response)
  };
}

/**
 * The record's real events, newest first: notes, files, and creation.
 *
 * Three event KINDS, all backed by rows that exist. A note is a `Comment`, a
 * file is an `Attachments`, and the record always has its own creation. The old
 * shape read `response.comments` alone, so every file uploaded against a contact
 * was dropped from the page it should have appeared on. Nothing records status
 * changes or calls/emails/meetings on a Contact, so those are not invented here
 * (the same discipline as `leads.js`).
 *
 * `ContactDetailView.get` returns `comments` and `attachments` as siblings, and
 * an attachment's `created_by` is a bare user id rather than a nested user, so a
 * file's author is left unnamed rather than printing an id, the note author,
 * which arrives as a nested profile, is shown.
 *
 * @param {any} response
 */
function buildContactActivity(response) {
  /** @type {Array<{id:string,type:'note'|'file'|'status',at:string,by:string|null,body:string,href?:string|null}>} */
  const events = (response.comments ?? []).map((/** @type {any} */ c) => ({
    id: `note-${c.id}`,
    type: 'note',
    at: c.commented_on,
    by: c.commented_by?.user_details?.email || null,
    body: c.comment
  }));

  for (const a of response.attachments ?? []) {
    events.push({
      id: `file-${a.id}`,
      type: 'file',
      at: a.created_at,
      by: null,
      body: a.file_name || 'Attachment',
      href: attachmentHref(a.id)
    });
  }

  const contact = response.contact_obj;
  if (contact?.created_at) {
    events.push({
      id: `created-${contact.id}`,
      type: 'status',
      at: contact.created_at,
      by: null,
      body: 'Contact added'
    });
  }

  return events.sort(
    (/** @type {any} */ a, /** @type {any} */ b) =>
      new Date(b.at).getTime() - new Date(a.at).getTime()
  );
}

/**
 * Log a note against a contact, optionally with a file. A call made, a reply,
 * a document shared.
 *
 * `ContactDetailView.post` creates a Comment scoped to the contact's org and
 * stamps `commented_by` from `request.profile`; the only field it reads from the
 * body is `comment`. It answers 403 through `assert_contact_access` for anyone
 * who is not the contact's creator, an assignee, an admin, or assigned to the
 * account the person belongs to, the same gate that governs *reading* the
 * contact, so the composer this feeds appears only to someone who could already
 * open the page, and the note is attributed server-side, never by the client.
 *
 * Unlike a lead, a contact's attachment save is a SEPARATE top-level block in
 * the view (not nested inside the comment check), so a file may ride with a note
 * or arrive on its own. The field name is `contact_attachment`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {string} comment
 * @param {File|null} [file]
 */
export async function addContactNote({ cookies }, id, comment, file = null) {
  if (file) {
    const form = new FormData();
    if (comment) form.set('comment', comment);
    form.set('contact_attachment', file);
    return await apiRequest(`/contacts/${id}/`, { method: 'POST', body: form }, { cookies });
  }
  return await apiRequest(`/contacts/${id}/`, { method: 'POST', body: { comment } }, { cookies });
}

/** Scalar fields the contact forms own. Everything else is server-derived. */
export const EDITABLE_FIELDS = [
  'first_name',
  'last_name',
  'email',
  'phone',
  'title',
  'department',
  'organization',
  'linkedin_url',
  'do_not_call',
  'address_line',
  'city',
  'state',
  'postcode',
  'country',
  'description',
  'account',
  'is_active'
];

/**
 * The choice lists the forms need, taken from the API rather than hardcoded.
 *
 * `countries` is the same list the model validates against, so a select built
 * from it cannot offer a value the serializer will reject.
 *
 * The account picker holds up to 200 accounts. Past that it needs to become a
 * search rather than a select, and this will quietly stop offering the rest,
 * so the form says how many it is showing instead of pretending it is all of
 * them.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listChoices(cookies) {
  const [contactsResponse, accountsResponse] = await Promise.all([
    apiRequest('/contacts/?limit=1', {}, { cookies }),
    apiRequest('/accounts/?limit=200', {}, { cookies })
  ]);
  const active = accountsResponse.active_accounts ?? {};
  const accounts = (active.open_accounts ?? []).map((/** @type {any} */ account) => ({
    id: account.id,
    name: account.name
  }));
  accounts.sort((/** @type {any} */ a, /** @type {any} */ b) => a.name.localeCompare(b.name));

  return {
    countries: (contactsResponse.countries ?? []).map((/** @type {any} */ pair) => ({
      value: pair[0],
      label: pair[1]
    })),
    owners: (contactsResponse.users ?? []).map((/** @type {any} */ user) => ({
      id: user.id,
      name: user.user__email
    })),
    accounts,
    account_total: active.open_accounts_count ?? accounts.length
  };
}

/**
 * Everything the create form needs, in one call.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string | null} [accountId] account to preselect, when arriving from one
 */
export async function getContactFormOptions({ cookies }, accountId = null) {
  const choices = await listChoices(cookies);
  const known = choices.accounts.some((/** @type {any} */ row) => row.id === accountId);
  return { ...choices, defaults: { country: '', account: known ? accountId : '' } };
}

/**
 * The contact plus what the edit form needs in order to explain itself.
 *
 * `form` holds only `EDITABLE_FIELDS`, so no input can bind to a
 * server-derived field by accident.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getContactForEdit({ cookies }, id) {
  const [response, choices] = await Promise.all([fetchDetail(cookies, id), listChoices(cookies)]);
  const raw = response.contact_obj;
  const contact = toRow(raw);

  /*
   * The linked account is always an option, even when the picker does not
   * carry it: an account the caller cannot open (the list holds only the ones
   * they can), a closed one, or one past the 200 cap. A select whose value
   * matches no option shows nothing and submits nothing or its first entry, so
   * a save would unlink it. The server keeps an unchanged link whoever saves,
   * and refuses a newly chosen account the caller cannot open.
   */
  const stored = raw.account_detail;
  const accounts =
    stored && !choices.accounts.some((/** @type {any} */ row) => row.id === stored.id)
      ? [{ id: stored.id, name: stored.name }, ...choices.accounts]
      : choices.accounts;

  return {
    contact,
    ...choices,
    accounts,
    form: {
      first_name: contact.first_name,
      last_name: contact.last_name,
      email: contact.email,
      phone: contact.phone,
      title: contact.title,
      department: contact.department,
      organization: contact.organization,
      linkedin_url: contact.linkedin_url,
      do_not_call: contact.do_not_call,
      address_line: contact.address_line,
      city: contact.city,
      state: contact.state,
      postcode: contact.postcode,
      country: contact.country,
      description: contact.description,
      is_active: contact.is_active,
      // The FK only. Membership of other accounts is not this form's to edit,
      // and the form says so rather than dropping it silently.
      account: raw.account ?? '',
      // Binds to a Profile id, not a display name.
      assigned_to: raw.assigned_to?.[0]?.id ?? ''
    },
    server: {
      // Read-only context so the form can explain what it is not editing.
      owner_count: (raw.assigned_to ?? []).length,
      team_count: (raw.teams ?? []).length,
      tag_count: (raw.tags ?? []).length,
      linked_account_count: contact.other_accounts.length,
      // The API's uncapped count; the `opportunities` list stops at 10.
      deal_count: response.opportunity_count ?? (response.opportunities ?? []).length,
      ticket_count: (response.cases ?? []).length
    }
  };
}

/**
 * Turn form values into a request body.
 *
 * Absent stays absent. A field the form did not send is a field the save
 * leaves alone, which is the whole reason for PATCH below.
 *
 * @param {Record<string, any>} values
 */
function toBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    const value = values[field];
    body[field] = value === '' ? null : value;
  }
  if ('do_not_call' in body) body.do_not_call = Boolean(values.do_not_call);
  if ('is_active' in body) body.is_active = Boolean(values.is_active);
  // Single-select owner, so the list is empty or one long. Only present when
  // the form decided it changed. See the action for why that matters.
  if ('assigned_to' in values) {
    body.assigned_to = values.assigned_to ? [values.assigned_to] : [];
  }
  return body;
}

/**
 * Save the edit form.
 *
 * PATCH, not PUT. `ContactDetailView.put` runs `teams.clear()`,
 * `assigned_to.clear()` and `tags.clear()` unconditionally before re-adding
 * whatever the body carried, and this form carries one owner, so a single PUT
 * would drop every co-assignee, every team and every tag on the record.
 * `patch` guards each relation with `if "<field>" in data`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateContact({ cookies }, id, values) {
  return await apiRequest(
    `/contacts/${id}/`,
    { method: 'PATCH', body: toBody(values) },
    { cookies }
  );
}

/**
 * Create a contact.
 *
 * No `org` and no `created_by` in the body: `ContactsListView.post` sets both
 * from `request.profile`, and `CreateContactSerializer` lists neither as a
 * field. `account` must be one the caller can open, or it is refused.
 *
 * The response now carries the new `id`, which it did not before this change.
 * Without it a client cannot open what it just made.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export async function createContact({ cookies }, values) {
  return await apiRequest('/contacts/', { method: 'POST', body: toBody(values) }, { cookies });
}

/**
 * `ContactDetailView.get` answers 404 for another org's contact. Deliberately
 * not 403, which would confirm the id exists, and 403 for a contact inside the
 * org that this profile neither created, nor is assigned to, nor reaches
 * through the account they own.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} id
 */
async function fetchDetail(cookies, id) {
  try {
    return await apiRequest(`/contacts/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    // On the status, not on the wording. Django answers a missing record with
    // "No Contact matches the given query.", no "404" in it anywhere.
    if (err?.status === 404) {
      error(404, 'That contact does not exist, or they belong to another team.');
    }
    if (err?.status === 403) {
      error(403, 'This contact belongs to somebody else. Ask an admin if you need them.');
    }
    throw err;
  }
}
