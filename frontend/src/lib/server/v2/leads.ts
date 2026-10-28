/**
 * Leads: the first v2 module wired to the real API.
 *
 * WHY THIS LIVES IN `#lib/server`
 * SvelteKit refuses to bundle anything under `src/lib/server` into client
 * code, and that guarantee is the point: the access token is read from an
 * httpOnly cookie in here. A module importable from a `+page.js`, and
 * therefore from the browser, cannot be where authenticated requests are
 * made. Leads was the first module to move here off the old mock facade; every
 * other module has since followed and the facade (`#lib/v2/api.js` and
 * `#lib/v2/mock/`) is gone.
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
import { error, type Cookies } from '@sveltejs/kit';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import { apiStatus } from '#lib/api-errors.js';
import { rethrowModuleOff } from '#lib/server/v2/modules.js';
import { attachmentHref } from '#lib/server/v2/files.js';
import { recordDuplicates } from '#lib/server/v2/duplicates.js';
import { fieldsForDetail, fieldsForEdit } from '#lib/server/v2/record-custom-fields.js';
import type { components } from '#lib/api-schema.js';

type Schemas = components['schemas'];
type ApiLead = Schemas['Lead'];
type CustomFieldValues = Record<string, string | boolean | null>;
/**
 * What the create and edit actions collect: the submitted fields as strings,
 * plus the typed custom values.
 */
export type LeadFormValues = {
  [field: string]: string | CustomFieldValues | undefined;
  custom_fields?: CustomFieldValues;
};

/** See the note on FILTER_FIELDS in tickets.js. */
export const FILTER_FIELDS = ['assigned_to', 'status', 'source', 'tags'];

/**
 * Django serialises `assigned_to` as a list of Profiles. The v2 list and rail
 * show a single owner, so pick the first and fall back to the creator, which
 * is what the row means by "Owner": whoever is answerable for this lead.
 *
 * @returns A display name, or '' when the lead is unassigned.
 */
function ownerName(lead: ApiLead): string {
  const email = lead.assigned_to?.[0]?.user_details?.email;
  if (email) return email;
  return lead.created_by?.email || '';
}

/** The subset of a Django lead the v2 pages read. */
function toRow(lead: ApiLead) {
  return {
    id: lead.id,
    first_name: lead.first_name ?? '',
    last_name: lead.last_name ?? '',
    job_title: lead.job_title ?? '',
    company_name: lead.company_name ?? '',
    industry: lead.industry ?? '',
    email: lead.email ?? '',
    phone: lead.phone ?? '',
    phone_e164: lead.phone_e164 ?? '',
    website: lead.website ?? '',
    status: lead.status ?? 'assigned',
    source: lead.source ?? '',
    source_detail: lead.source_detail ?? '',
    source_ref: lead.source_ref ?? '',
    last_enquiry_at: lead.last_enquiry_at ?? null,
    first_response_at: lead.first_response_at ?? null,
    first_response_by: lead.first_response_by_details?.name || null,
    waiting_since: lead.waiting_since ?? null,
    is_unanswered: lead.is_unanswered === true,
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
 */
export async function listLeads({ cookies }: { cookies: Cookies }, params?: URLSearchParams) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response: Schemas['LeadListResponse'] = await apiRequest(
    `/leads/?${query}`,
    {},
    { cookies }
  );
  const raw = response.open_leads?.open_leads ?? [];
  const rows = raw.map(toRow);

  if (query.get('unanswered') === 'true') rows.sort((a, b) => waited(a) - waited(b));
  else rows.sort((a, b) => lastTouch(a) - lastTouch(b));

  return {
    results: rows,
    totals: response.totals ?? { count: rows.length, unworked_over_a_week: 0 },
    // An API without response tracking ignores `?unanswered=true` rather than
    // answering 404, so its rows are the only tell: they lack the field. Null
    // when there is no row to read it from.
    responseLive: raw.length ? raw.some((l) => 'is_unanswered' in l) : null
  };
}

function lastTouch(row: { last_contacted: string | null; created_at: string }) {
  return new Date(row.last_contacted ?? row.created_at).getTime();
}

/** Oldest wait first, the API's own order for the unanswered list. */
function waited(row: { waiting_since: string | null }) {
  return row.waiting_since ? new Date(row.waiting_since).getTime() : Infinity;
}

/** The periods the Response time view offers; the API takes 7 to 90 days. */
export const RESPONSE_DAYS = [7, 30, 90];

/**
 * @param raw a `?days=` value
 * @returns one of RESPONSE_DAYS, 30 for anything else
 */
export function responseDays(raw: string | null): number {
  const n = Number(raw);
  return RESPONSE_DAYS.includes(n) ? n : 30;
}

/**
 * Time to first response per person. The API answers an admin with every
 * active member and a member with their own row; this passes on what it got.
 * An API without response tracking answers 404, which is the page's answer.
 */
export async function getResponseTimes({ cookies }: { cookies: Cookies }, days: number) {
  let resp: Schemas['LeadResponseTimesResponse'];
  try {
    resp = await apiRequest(
      `/leads/response-times/?days=${responseDays(String(days))}`,
      { expectedStatuses: [404] },
      { cookies }
    );
  } catch (err) {
    rethrowModuleOff(err);
    if (apiStatus(err) === 404) error(404, 'Response times are not available here.');
    throw err;
  }
  const int = (n: number | null | undefined) =>
    typeof n === 'number' && Number.isFinite(n) ? n : 0;
  return {
    since: typeof resp?.since === 'string' ? resp.since : null,
    rows: (Array.isArray(resp?.rows) ? resp.rows : []).map((r) => ({
      profile: typeof r?.profile === 'string' ? r.profile : '',
      name: typeof r?.name === 'string' && r.name ? r.name : 'Unknown',
      answered: int(r?.answered),
      median_seconds:
        typeof r?.median_seconds === 'number' && Number.isFinite(r.median_seconds)
          ? r.median_seconds
          : null,
      within_5_minutes: int(r?.within_5_minutes),
      within_1_hour: int(r?.within_1_hour),
      unanswered_now: int(r?.unanswered_now)
    }))
  };
}

/**
 * Won, lost and time to win per person. The API answers an admin with every
 * active member plus a totals row, and a member with their own row and no
 * totals; this passes on what it got. An API without outcomes answers 404.
 */
export async function getOutcomes({ cookies }: { cookies: Cookies }, days: number) {
  let resp: Schemas['LeadOutcomesResponse'];
  try {
    resp = await apiRequest(
      `/leads/outcomes/?days=${responseDays(String(days))}`,
      { expectedStatuses: [404] },
      { cookies }
    );
  } catch (err) {
    rethrowModuleOff(err);
    if (apiStatus(err) === 404) error(404, 'Outcomes are not available here.');
    throw err;
  }
  const int = (n: number | null | undefined) =>
    typeof n === 'number' && Number.isFinite(n) ? n : 0;
  const orNull = (n: number | null | undefined) =>
    typeof n === 'number' && Number.isFinite(n) ? n : null;
  const figures = (r: Schemas['LeadOutcomesTotals'] | undefined) => ({
    won: int(r?.won),
    lost: int(r?.lost),
    win_rate: orNull(r?.win_rate),
    median_seconds_to_win: orNull(r?.median_seconds_to_win),
    new_leads: int(r?.new_leads)
  });
  return {
    since: typeof resp?.since === 'string' ? resp.since : null,
    measured_since: typeof resp?.measured_since === 'string' ? resp.measured_since : null,
    rows: (Array.isArray(resp?.rows) ? resp.rows : []).map((r) => ({
      profile: typeof r?.profile === 'string' ? r.profile : '',
      name: typeof r?.name === 'string' && r.name ? r.name : 'Unknown',
      ...figures(r)
    })),
    totals: resp?.totals ? figures(resp.totals) : null
  };
}

/**
 * One lead, with its activity and anything that ought to give the reader
 * pause before they act on it.
 */
export async function getLead({ cookies }: { cookies: Cookies }, id: string) {
  const response = await fetchDetail(cookies, id);
  // `description` is a real field the edit form owns, but `toRow` leaves it out
  // because the list has no use for it. The detail page does. It is the one
  // place a lead's free text is worth reading, so it is attached here rather
  // than widening every list row to carry a paragraph nobody scans.
  const lead = { ...toRow(response.lead_obj), description: response.lead_obj.description ?? '' };

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
    customFields: fieldsForDetail(response, response.lead_obj.custom_fields),
    activity: buildActivity(response),
    // The API's delete rule for this caller, so Delete is offered only when it would work.
    canDelete: Boolean(response.can_delete),
    duplicates: await recordDuplicates({ cookies }, 'leads', id)
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
 */
export async function addLeadNote(
  { cookies }: { cookies: Cookies },
  id: string,
  comment: string,
  file: File | null = null
): Promise<Schemas['LeadCommentAttachmentResponse']> {
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
 */
function buildActivity(response: Schemas['LeadDetailResponse']) {
  const events: Array<{
    id: string;
    type: 'note' | 'file' | 'status';
    at: string;
    by: string | null;
    body: string;
    href?: string | null;
  }> = (response.comments ?? []).map((c) => ({
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

  events.push({
    id: `created-${response.lead_obj.id}`,
    type: 'status',
    at: response.lead_obj.created_at,
    by: response.lead_obj.created_by?.email ?? null,
    body: 'Lead created'
  });

  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

/** Scalar fields the edit form owns. Everything else about a lead is derived. */
export const EDITABLE_FIELDS: Exclude<
  keyof Schemas['LeadCreateSwaggerRequest'],
  'custom_fields'
>[] = [
  'first_name',
  'last_name',
  'job_title',
  'company_name',
  'email',
  'phone',
  'website',
  'status',
  'source',
  'source_detail',
  'source_ref',
  'industry',
  'opportunity_amount',
  'description'
];

/**
 * Fields whose "no value" is '' rather than null: the source columns are
 * blank-only strings, and a null there is refused.
 */
const BLANK_NOT_NULL = ['source', 'source_detail', 'source_ref'];

function emptyAs(field: string, value: unknown) {
  if (value !== '' && value !== undefined && value !== null) return value;
  return BLANK_NOT_NULL.includes(field) ? '' : null;
}

/**
 * Profiles in this org, for the owner select.
 *
 * The endpoint scopes to `request.profile.org` itself, and the API filters
 * `assigned_to` ids through `Profile.objects.filter(id__in=..., org=...)` on
 * the way back in, so a forged id cannot assign a lead to somebody in
 * another tenant. It would simply not match, and the lead would end up
 * unassigned.
 */
async function listOwners(cookies: Cookies) {
  try {
    const response: Schemas['TeamsAndUsersResponse'] = await apiRequest(
      '/users/get-teams-and-users/',
      {},
      { cookies }
    );
    return (response.profiles ?? []).map((p) => ({
      id: p.id,
      name: p.user_details?.email || 'Unknown'
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
 */
export async function getLeadForEdit({ cookies }: { cookies: Cookies }, id: string) {
  const response = await fetchDetail(cookies, id);
  const lead = toRow(response.lead_obj);

  // Every key of EDITABLE_FIELDS. `description` is the one the row leaves out.
  const form = {
    first_name: lead.first_name,
    last_name: lead.last_name,
    job_title: lead.job_title,
    company_name: lead.company_name,
    email: lead.email,
    phone: lead.phone,
    website: lead.website,
    status: lead.status,
    source: lead.source,
    source_detail: lead.source_detail,
    source_ref: lead.source_ref,
    industry: lead.industry,
    opportunity_amount: lead.opportunity_amount ?? '',
    description: response.lead_obj.description ?? '',
    // The select binds to a Profile id, not a display name. The mock bound it
    // to a name, which reads the same on screen and cannot be saved.
    assigned_to: response.lead_obj.assigned_to?.[0]?.id ?? ''
  };

  return {
    lead,
    form,
    // The definitions drive both the inputs rendered here and the keys read
    // back off the submitted form, so a field the org has not defined has
    // nowhere to enter the request in the first place.
    customFields: fieldsForEdit(response, response.lead_obj.custom_fields),
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

async function takenEmails(cookies: Cookies, excludeId: string) {
  try {
    const response: Schemas['LeadListResponse'] = await apiRequest(
      '/leads/?limit=200',
      {},
      { cookies }
    );
    return (response.open_leads?.open_leads ?? []).flatMap((l) =>
      l.id !== excludeId && l.email ? [l.email.toLowerCase()] : []
    );
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
 */
export async function updateLead(
  { cookies }: { cookies: Cookies },
  id: string,
  values: Record<string, unknown>
): Promise<Schemas['LeadPatchResponse']> {
  const body: FormBody<Schemas['PatchedLeadPatchSwaggerRequest']> = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    body[field] = emptyAs(field, values[field]);
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
 * `LeadDetailView.get` answers 404, with one body, both for a lead that does
 * not exist and for one this profile may not open (another org's, or one in
 * this org they neither created nor are assigned to). Deliberately not 403,
 * which would confirm the id exists. It is the caller's answer, not a server
 * fault, so it never becomes a 500, and the copy covers both cases.
 */
async function fetchDetail(cookies: Cookies, id: string): Promise<Schemas['LeadDetailResponse']> {
  try {
    return await apiRequest(`/leads/${id}/`, {}, { cookies });
  } catch (err) {
    rethrowModuleOff(err);
    // On the status, not on the wording. This read `message.includes('404')`,
    // and Django's message for a missing lead is "No Lead matches the given
    // query.", so opening a deleted lead answered 500. See `api-helpers.js`.
    if (apiStatus(err) === 404) {
      error(404, 'That lead does not exist, or you do not have access to it.');
    }
    throw err;
  }
}

/**
 * Delete this lead for good. The API applies the delete rule (an admin or
 * the lead's creator) and answers 404 for one the caller cannot open; the
 * page offers the action only when the detail response said `can_delete`.
 */
export async function deleteLead(
  { cookies }: { cookies: Cookies },
  id: string
): Promise<Schemas['LeadDeleteResponse']> {
  return await apiRequest(`/leads/${id}/`, { method: 'DELETE' }, { cookies });
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
 * @returns the `LeadListView.post` response body,
 *   carrying the new lead's `id`.
 */
export async function createLead(
  { cookies }: { cookies: Cookies },
  values: Record<string, unknown>
): Promise<Schemas['LeadCreateResponse']> {
  const body: FormBody<Schemas['LeadCreateSwaggerRequest']> = {};
  for (const field of EDITABLE_FIELDS) {
    body[field] = emptyAs(field, values[field]);
  }

  if (values.opportunity_amount !== undefined) {
    const amount = values.opportunity_amount;
    body.opportunity_amount = amount === '' || amount == null ? null : Number(amount);
  }

  // Single-select owner, sent as the list the API expects. Empty means
  // unassigned, which is a real state, so it is sent rather than skipped.
  body.assigned_to = values.assigned_to ? [values.assigned_to] : [];
  if (values.custom_fields) body.custom_fields = values.custom_fields;

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
 */
// Despite the name `user_obj`, `ProfileView.get` serializes `request.profile`
// (a Profile, not a User) through `ProfileSerializer`, so `user_obj.id` below
// is the Profile's own id. The payload key is a holdover from the API's
// naming, not a description of what it returns.
export async function myProfileId(cookies: Cookies) {
  try {
    const response: Schemas['ProfileViewResponse'] = await apiRequest('/profile/', {}, { cookies });
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
 * @returns the raw API response, which carries
 *   `account_id`, and `contact_id` / `opportunity_id` that may each be null.
 */
export async function convertLead(
  { cookies }: { cookies: Cookies },
  id: string
): Promise<Schemas['LeadPatchResponse']> {
  if (!id) throw new Error('A lead id is required to convert.');
  return await apiRequest(
    `/leads/${id}/`,
    {
      method: 'PATCH',
      body: { status: 'converted' } satisfies Schemas['PatchedLeadPatchSwaggerRequest']
    },
    { cookies }
  );
}

/** Everything the create form needs to render its selects, in one call. */
export async function getLeadFormOptions({ cookies }: { cookies: Cookies }) {
  const [owners, mine] = await Promise.all([listOwners(cookies), myProfileId(cookies)]);
  return {
    owners,
    defaults: { assigned_to: mine, status: 'assigned' }
  };
}
