/**
 * Leads: the first v2 module wired to the real API.
 *
 * WHY THIS LIVES IN `$lib/server`
 * SvelteKit refuses to bundle anything under `src/lib/server` into client
 * code, and that guarantee is the point: the access token is read from an
 * httpOnly cookie in here. A module importable from a `+page.js`, and
 * therefore from the browser, cannot be where authenticated requests are
 * made. Leads was the first module to move here off the old mock facade; every
 * other module has since followed and the facade (`$lib/v2/api.js` and
 * `$lib/v2/mock/`) is gone.
 *
 * SHAPES
 * The functions below return the shapes the v2 pages already consume, so the
 * pages did not have to change to stop being mocks. Where the mock invented a
 * field the backend does not have, the field is gone rather than faked;
 * `last_activity_at` is the notable one; see `listLeads`.
 *
 * SECURITY
 * The org is never a parameter here. It is a claim inside the JWT, read
 * server-side by `RLSContextMiddleware`, and `LeadListView` filters on
 * `request.profile.org` besides. Adding an `org` argument to any of these
 * would be inventing a way for a caller to ask for another tenant's rows.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';
import { attachmentHref } from '$lib/server/v2/files.js';
import {
  leadFieldDefinitions,
  pairForDisplay,
  pairForEdit
} from '$lib/server/v2/lead-custom-fields.js';

/** See the note on FILTER_FIELDS in tickets.js. */
export const FILTER_FIELDS = ['assigned_to', 'status', 'source', 'tags'];

/**
 * Django serialises `assigned_to` as a list of Profiles. The v2 list and rail
 * show a single owner, so pick the first and fall back to the creator, which
 * is what the row means by "Owner": whoever is answerable for this lead.
 *
 * @param {any} lead
 * @returns {string} A display name, or '' when the lead is unassigned.
 */
function ownerName(lead) {
  const assigned = lead.assigned_to?.[0];
  const email = assigned?.user_details?.email || assigned?.user?.email;
  if (email) return email;
  return lead.created_by?.email || '';
}

/**
 * @param {any} lead
 * @returns {any} The subset of a Django lead the v2 pages read.
 */
function toRow(lead) {
  return {
    id: lead.id,
    first_name: lead.first_name ?? '',
    last_name: lead.last_name ?? '',
    job_title: lead.job_title ?? '',
    company_name: lead.company_name ?? '',
    industry: lead.industry ?? '',
    email: lead.email ?? '',
    phone: lead.phone ?? '',
    website: lead.website ?? '',
    status: lead.status ?? 'assigned',
    source: lead.source ?? '',
    opportunity_amount: lead.opportunity_amount ? Number(lead.opportunity_amount) : null,
    // A lead's amount is stored in the lead's own currency, which the
    // serializer stamps from the org default at create time and the user can
    // change afterwards. Dropping it here left every lead amount printing as
    // dollars, the same defect the deal list had.
    currency: lead.currency || 'USD',
    assigned_to: ownerName(lead),
    // Null means never contacted, and the column says so rather than
    // substituting `updated_at`. An edit is not a conversation, and a list
    // whose "Last touch" quietly counts edits stops being worth reading.
    last_contacted: lead.last_contacted ?? null,
    created_at: lead.created_at
  };
}

/**
 * The open leads list, least recently touched first.
 *
 * The mock sorted on a `last_activity_at` field that Lead does not have: the
 * file that defined it said so in a header comment, because aging (rotting
 * days on a deal's `DealStage`, `get_aging_status()`) is deal-only. The real
 * ordering is `last_contacted` where it is set and the creation date where it
 * is not, which is the same intent expressed in fields that exist.
 *
 * `totals` comes from the API and is computed there over the whole filtered
 * queryset. Do not recompute it from `results`; `results` is one page, and a
 * page total presented as a list total is the specific bug this redesign
 * exists to fix.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listLeads({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response = await apiRequest(`/leads/?${query}`, {}, { cookies });
  const rows = (response.open_leads?.open_leads ?? []).map(toRow);

  rows.sort((a, b) => lastTouch(a) - lastTouch(b));

  return {
    results: rows,
    totals: response.totals ?? { count: rows.length, unworked_over_a_week: 0 }
  };
}

/** @param {{ last_contacted: string | null, created_at: string }} row */
function lastTouch(row) {
  return new Date(row.last_contacted ?? row.created_at).getTime();
}

/**
 * One lead, with its activity and anything that ought to give the reader
 * pause before they act on it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getLead({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  // `description` is a real field the edit form owns, but `toRow` leaves it out
  // because the list has no use for it. The detail page does. It is the one
  // place a lead's free text is worth reading, so it is attached here rather
  // than widening every list row to carry a paragraph nobody scans.
  const lead = { ...toRow(response.lead_obj), description: response.lead_obj.description ?? '' };

  // Per-org custom fields. A vertical pack's whole promise is the fields it
  // sets up for the industry, and they are stored on the lead, so the detail
  // page reads them here rather than leaving them visible only under
  // Settings → Custom fields.
  const definitions = await leadFieldDefinitions(cookies);

  return {
    lead,
    // The pipeline stage the lead sits in, with its pipeline's name, or null.
    // `LeadDetailView` sends it beside `lead_obj`, which carries only the id.
    stage: response.pipeline_stage
      ? {
          name: response.pipeline_stage.name,
          pipeline: {
            id: response.pipeline_stage.pipeline.id,
            name: response.pipeline_stage.pipeline.name
          }
        }
      : null,
    customFields: pairForDisplay(definitions, response.lead_obj.custom_fields),
    activity: buildActivity(response),
    duplicates: await findDuplicates(cookies, lead)
  };
}

/**
 * Log a note against a lead: a call made, a reply, what they said.
 *
 * `LeadDetailView.post` creates a Comment scoped to the lead's org and stamps
 * `commented_by` from `request.profile`; the only field it reads from the body
 * is `comment`. That view answers 403 for a profile that is neither the lead's
 * creator, an assignee, nor an admin, the same gate that governs *reading* the
 * lead, so the composer this feeds appears only to someone who could already
 * open the page, and a note is attributed server-side rather than by the client.
 *
 * A file rides with the note, never alone: `LeadDetailView.post` only saves an
 * attachment inside its `if params.get("comment")` branch, so an upload with no
 * note text is silently dropped by the API. The composer enforces the note, and
 * the form action refuses a file without one, so the two rules agree.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {string} comment
 * @param {File|null} [file]
 */
export async function addLeadNote({ cookies }, id, comment, file = null) {
  if (file) {
    const form = new FormData();
    form.set('comment', comment);
    form.set('lead_attachment', file);
    return await apiRequest(`/leads/${id}/`, { method: 'POST', body: form }, { cookies });
  }
  return await apiRequest(`/leads/${id}/`, { method: 'POST', body: { comment } }, { cookies });
}

/**
 * The record's real events, newest first: notes, files, and creation.
 *
 * Three event KINDS, all backed by rows that exist. A note is a `Comment`, a
 * file is an `Attachments`, and the record always has its own creation. The
 * mock timeline also had `status` entries ("moved from Assigned to In process");
 * nothing records those, Lead has no history table and no aging chain, so they
 * are not reconstructed here. Meetings, calls and emails as distinct types would
 * be the same fiction: there are no such records, so there are no such events.
 *
 * @param {any} response
 */
function buildActivity(response) {
  /** @type {Array<{id:string,type:'note'|'file'|'status',at:string,by:string|null,body:string,href?:string|null}>} */
  const events = (response.comments ?? []).map((/** @type {any} */ c) => ({
    id: `note-${c.id}`,
    type: 'note',
    at: c.commented_on,
    by: c.commented_by_user?.email || c.commented_by?.user_details?.email || null,
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

  events.push({
    id: `created-${response.lead_obj.id}`,
    type: 'status',
    at: response.lead_obj.created_at,
    by: response.lead_obj.created_by?.email ?? null,
    body: 'Lead created'
  });

  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

/**
 * Other open leads in this org that share an email or a website.
 *
 * The mock rail asserted "Another lead shares this website" on every lead it
 * ever rendered. A duplicate warning that is always on is one people learn to
 * scroll past, so this asks the API and says nothing when the answer is none.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {any} lead
 */
async function findDuplicates(cookies, lead) {
  if (!lead.email && !lead.website) return [];

  // `search` covers first name, last name, company and email. Website is not
  // searchable server-side, so an exact email match is the reliable half and
  // the website comparison happens over that result set.
  const query = new URLSearchParams({ limit: '10' });
  query.set('search', lead.email || lead.company_name);

  /** @type {any} */
  let response;
  try {
    response = await apiRequest(`/leads/?${query}`, {}, { cookies });
  } catch {
    // A failed duplicate check is not a reason to fail the page.
    return [];
  }

  return (response.open_leads?.open_leads ?? [])
    .filter((/** @type {any} */ other) => {
      if (other.id === lead.id) return false;
      const sameEmail = lead.email && other.email?.toLowerCase() === lead.email.toLowerCase();
      const sameSite = lead.website && other.website?.toLowerCase() === lead.website.toLowerCase();
      return Boolean(sameEmail || sameSite);
    })
    .map((/** @type {any} */ other) => ({
      id: other.id,
      name: `${other.first_name ?? ''} ${other.last_name ?? ''}`.trim() || other.email,
      matched_on:
        lead.email && other.email?.toLowerCase() === lead.email.toLowerCase()
          ? 'the same email address'
          : 'the same website'
    }));
}

/** Scalar fields the edit form owns. Everything else about a lead is derived. */
export const EDITABLE_FIELDS = [
  'first_name',
  'last_name',
  'job_title',
  'company_name',
  'email',
  'phone',
  'website',
  'status',
  'source',
  'industry',
  'opportunity_amount',
  'description'
];

/**
 * Profiles in this org, for the owner select.
 *
 * The endpoint scopes to `request.profile.org` itself, and the API filters
 * `assigned_to` ids through `Profile.objects.filter(id__in=..., org=...)` on
 * the way back in, so a forged id cannot assign a lead to somebody in
 * another tenant. It would simply not match, and the lead would end up
 * unassigned.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listOwners(cookies) {
  try {
    const response = await apiRequest('/users/get-teams-and-users/', {}, { cookies });
    return (response.profiles ?? []).map((/** @type {any} */ p) => ({
      id: p.id,
      name: p.user_details?.email || p.user?.email || 'Unknown'
    }));
  } catch {
    return [];
  }
}

/**
 * The lead plus what the edit form needs in order to explain itself.
 *
 * `form` holds only `EDITABLE_FIELDS`, so no input can bind to a
 * server-derived field by accident. The read-only context the page needs in
 * order to *describe* those fields travels separately in `server`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getLeadForEdit({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  const lead = toRow(response.lead_obj);

  /** @type {Record<string, any>} */
  const form = {};
  for (const field of EDITABLE_FIELDS) {
    form[field] = lead[field] ?? response.lead_obj[field] ?? '';
  }
  form.opportunity_amount = lead.opportunity_amount ?? '';
  // The select binds to a Profile id, not a display name. The mock bound it
  // to a name, which reads the same on screen and cannot be saved.
  form.assigned_to = response.lead_obj.assigned_to?.[0]?.id ?? '';

  const definitions = await leadFieldDefinitions(cookies);

  return {
    lead,
    form,
    // The definitions drive both the inputs rendered here and the keys read
    // back off the submitted form, so a field the org has not defined has
    // nowhere to enter the request in the first place.
    customFields: pairForEdit(definitions, response.lead_obj.custom_fields),
    owners: await listOwners(cookies),
    server: {
      // The DB constraint `unique_lead_email_per_org` is case-insensitive and
      // conditional on a non-empty email. This list only lets the form show
      // the collision before submit: the 400 from the API is what decides.
      taken_emails: await takenEmails(cookies, id),
      converted_at: lead.status === 'converted' ? response.lead_obj.updated_at : null
    }
  };
}

/**
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} excludeId
 */
async function takenEmails(cookies, excludeId) {
  try {
    const response = await apiRequest('/leads/?limit=200', {}, { cookies });
    return (response.open_leads?.open_leads ?? [])
      .filter((/** @type {any} */ l) => l.id !== excludeId && l.email)
      .map((/** @type {any} */ l) => l.email.toLowerCase());
  } catch {
    return [];
  }
}

/**
 * Save the edit form.
 *
 * PATCH, not PUT, for one concrete reason: `LeadDetailView.put` runs
 * `assigned_to.clear()`, `tags.clear()`, `contacts.clear()` and
 * `teams.clear()` unconditionally, then re-adds only what the body carried.
 * This form does not edit tags, contacts or teams, so every save through a
 * PUT would silently strip all three. `patch` guards each of those with
 * `if "<field>" in params`, which is the semantics a form owning a subset of
 * the record actually needs.
 *
 * Only the keys `values` actually contains are sent, and only if they are in
 * the allow-list. Absent stays absent: on a converted lead the status select
 * is disabled and submits nothing, and sending its current value would trip
 * `validate_status`, which treats `value == current` on an irreversible status
 * as a repeat conversion and rejects the whole save.
 *
 * The API ignores `org` and `created_by`; `LeadCreateSerializer` does not
 * list them, but a client should not be sending fields whose absence is what
 * makes them safe. To write a new field, add it to `EDITABLE_FIELDS`
 * deliberately.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateLead({ cookies }, id, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    const value = values[field];
    body[field] = value === '' ? null : value;
  }

  if ('opportunity_amount' in values) {
    const amount = values.opportunity_amount;
    body.opportunity_amount = amount === '' || amount == null ? null : Number(amount);
  }
  // A single-select owner, so the list is empty or one long. Empty is
  // meaningful, it is how a lead is unassigned, so it is sent, not skipped.
  if ('assigned_to' in values) {
    body.assigned_to = values.assigned_to ? [values.assigned_to] : [];
  }

  // `validate_payload` merges rather than replaces: it carries forward every
  // recognized key the body does not mention, and treats an explicit null as
  // "clear this one" (`cleaned.pop`). So omitting a field preserves it and
  // submitting it empty removes it, which is exactly what a form whose empty
  // input means "no value" needs, and why `collectFromForm` sends null instead
  // of skipping the key. Absent stays absent: a caller that does not edit
  // custom fields never sends the key at all.
  if ('custom_fields' in values && values.custom_fields) {
    body.custom_fields = values.custom_fields;
  }

  return await apiRequest(`/leads/${id}/`, { method: 'PATCH', body }, { cookies });
}

/**
 * `LeadDetailView.get` answers 404 for another org's lead and 403 for a lead
 * inside the org that this profile is neither assigned to nor created. Both
 * are the caller's answer, not a server fault, so neither becomes a 500.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} id
 */
async function fetchDetail(cookies, id) {
  try {
    return await apiRequest(`/leads/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    // On the status, not on the wording. This read `message.includes('404')`,
    // and Django's message for a missing lead is "No Lead matches the given
    // query.", so opening a deleted lead answered 500. See `api-helpers.js`.
    if (err?.status === 404) {
      error(404, 'That lead does not exist, or it belongs to another team.');
    }
    if (err?.status === 403) {
      error(403, 'This lead is assigned to somebody else. Ask an admin if you need it.');
    }
    throw err;
  }
}

/**
 * Create a lead.
 *
 * Mirrors `updateLead`, with one deliberate difference: update sends only the
 * fields the form submitted, because the API treats absent as "leave alone".
 * Create has nothing to leave alone, so every editable field is sent and an
 * empty one is sent as null, which is how the serializer records "no value".
 *
 * `org` and `created_by` are never sent. The backend derives both from the
 * JWT, and accepting them from a form body is how a tenant boundary gets
 * crossed. The loop below only ever reads keys named in EDITABLE_FIELDS, so an
 * extra key in `values` cannot reach the request.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 * @returns {Promise<any>} the `LeadListView.post` response body. Confirmed against the
 *   running backend: on success this is `{ error: false, message: "Lead Created Successfully" }`,
 *   with no `id` field. Unlike `createDeal`'s response, which does carry one
 *   (`OpportunityListView.post` adds it deliberately), a caller here cannot read `.id` off the
 *   result to open the record it just created. `+page.server.js` accounts for this and falls
 *   back to the list rather than assuming an id that is not there.
 */
export async function createLead({ cookies }, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    const value = values[field];
    body[field] = value === '' || value === undefined ? null : value;
  }

  if (values.opportunity_amount !== undefined) {
    const amount = values.opportunity_amount;
    body.opportunity_amount = amount === '' || amount == null ? null : Number(amount);
  }

  // Single-select owner, sent as the list the API expects. Empty means
  // unassigned, which is a real state, so it is sent rather than skipped.
  body.assigned_to = values.assigned_to ? [values.assigned_to] : [];

  return await apiRequest('/leads/', { method: 'POST', body }, { cookies });
}

/**
 * Who the session actually is, rather than who a form claims to be. The owner
 * select defaults to this. Empty on failure: an unassigned lead is a better
 * outcome than a wrongly assigned one, and the select is right there.
 *
 * Exported for `macros.js`, which needs the same "who am I" resolution to
 * compare against a macro's `owner.id`; a second copy of this call would be
 * the same mistake `viewerRole` made before phase 1 consolidated it.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
// Despite the name `user_obj`, `ProfileView.get` serializes `request.profile`
// (a Profile, not a User) through `ProfileSerializer`, so `user_obj.id` below
// is the Profile's own id. The payload key is a holdover from the API's
// naming, not a description of what it returns.
export async function myProfileId(cookies) {
  try {
    const response = await apiRequest('/profile/', {}, { cookies });
    return response.user_obj?.id ?? '';
  } catch {
    return '';
  }
}

/**
 * Convert a lead.
 *
 * Conversion is not a dedicated endpoint. It is a side effect of PATCHing
 * `status: 'converted'` on the ordinary lead detail URL, which runs
 * `convert_lead_to_account` server-side and creates an Account, a Contact when
 * the lead has an email, and usually an Opportunity. The body carries nothing
 * else on purpose: the backend takes no overrides for the account name, the
 * opportunity amount, or the stage, so sending more would be silently dropped.
 *
 * The backend rejects a repeat conversion (`IRREVERSIBLE_STATUSES`) and, since
 * phase 4, a conversion of a lead with no email. Both surface as a 400.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @returns {Promise<Record<string, any>>} the raw API response, which carries
 *   `account_id`, and `contact_id` / `opportunity_id` that may each be null.
 */
export async function convertLead({ cookies }, id) {
  if (!id) throw new Error('A lead id is required to convert.');
  return await apiRequest(
    `/leads/${id}/`,
    { method: 'PATCH', body: { status: 'converted' } },
    { cookies }
  );
}

/**
 * Everything the create form needs to render its selects, in one call.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getLeadFormOptions({ cookies }) {
  const [owners, mine] = await Promise.all([listOwners(cookies), myProfileId(cookies)]);
  return {
    owners,
    defaults: { assigned_to: mine, status: 'assigned' }
  };
}
