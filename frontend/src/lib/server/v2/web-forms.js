/**
 * Web forms: the wiring behind `/settings/web-forms`.
 *
 * Server-only, like every module in this directory: `$lib/server` is the one
 * path SvelteKit refuses to bundle into client code, and a form's captcha
 * secret passes through here on its way to the API.
 *
 * WHO MAY DO WHAT
 * Reading is open to any member of the org; every write is admin-only. The two
 * halves are not the same risk. A web form is an anonymous endpoint that writes
 * leads into the org, so creating one is closer to minting a credential than to
 * editing a record, and `webforms/views.py` refuses a non-admin's POST, PUT,
 * DELETE, publish and unpublish. `canManage` below is a display hint read off
 * the JWT so the page can hide buttons that would 403; it is not the
 * authorization, which is re-derived from `request.profile` server-side.
 *
 * `is_published` IS NEVER SENT THROUGH `updateWebForm`
 * Publishing has its own endpoint because it validates the source state and
 * the form's shape: already-published is a 400 rather than a no-op, a form
 * with no email field cannot be published (that field is what lets a repeat
 * submission update the existing lead instead of failing), and a form set to
 * redirect on success must have a redirect URL. `WebFormDetailSerializer`
 * lists `is_published` in `read_only_fields`, so putting it in an update body
 * is silently ignored rather than rejected. That silence is the trap: a page
 * that sent it would appear to work and would never publish anything. The
 * allow-list in `buildBody` is what keeps it from being sent at all.
 *
 * TOTALS COME FROM THE SERVER, NOT FROM COUNTING ROWS
 * The list is paginated at ten. The `totals` block on the response is computed
 * over every form in the org, so "3 published" stays true past the tenth form.
 * Nothing here recounts the page.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerRole } from './organization.js';

/** Enough forms that no real org is truncated, small enough to stay one page. */
const LIST_LIMIT = 100;

const EMPTY_TOTALS = { count: 0, published: 0, submissions_30d: 0, spam_30d: 0 };

/**
 * The org's forms, newest first, plus the org-wide totals.
 *
 * `truncated` is surfaced rather than swallowed. An org with more than
 * `LIST_LIMIT` forms gets a page that says so; a list that silently stops at a
 * cap reads as "that is all of them".
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getWebForms({ cookies }) {
  const resp = await apiRequest(`/webforms/?limit=${LIST_LIMIT}`, {}, { cookies });
  const forms = resp?.results ?? [];
  const totals = resp?.totals ?? EMPTY_TOTALS;
  return {
    forms,
    totals,
    truncated: (resp?.count ?? forms.length) > forms.length,
    // A display hint. `is_org_admin(request.profile)` is what actually decides.
    canManage: viewerRole(cookies) === 'ADMIN'
  };
}

/**
 * Profiles in this org, for the owner and notify selects.
 *
 * Same endpoint and same swallow-and-return-empty shape `listOwners` in
 * `leads.js` uses. The endpoint scopes to `request.profile.org` itself, and
 * `WebFormDetailSerializer` resolves the ids it is sent through the same
 * org-scoped queryset, so a forged id names nobody rather than reaching into
 * another tenant. Failing to an empty list leaves the page readable with an
 * empty picker, which is the right outcome for a member who cannot edit
 * anyway.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listProfiles(cookies) {
  try {
    const resp = await apiRequest('/users/get-teams-and-users/', {}, { cookies });
    return (resp.profiles ?? []).map((/** @type {any} */ p) => ({
      id: p.id,
      name: p.user_details?.email || p.user?.email || 'Unknown'
    }));
  } catch {
    return [];
  }
}

/**
 * Active custom field definitions that live on Lead.
 *
 * Filtered here rather than server-side because `/custom-fields/` has no
 * target filter and returns every definition in the org, turned-off ones
 * included. A form pointing at an inactive definition would write into a
 * column nothing reads, so those are dropped from the picker; the serializer
 * separately refuses a definition belonging to another org or another model.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listLeadCustomFields(cookies) {
  try {
    const resp = await apiRequest('/custom-fields/', {}, { cookies });
    return (resp.definitions ?? [])
      .filter((/** @type {any} */ d) => d.target_model === 'Lead' && d.is_active)
      .map((/** @type {any} */ d) => ({ id: d.id, label: d.label, key: d.key }));
  } catch {
    return [];
  }
}

/** @param {import('@sveltejs/kit').Cookies} cookies */
async function listTags(cookies) {
  try {
    const resp = await apiRequest('/tags/', {}, { cookies });
    return (resp.tags ?? []).map((/** @type {any} */ t) => ({ id: t.id, name: t.name }));
  } catch {
    return [];
  }
}

/**
 * One form with its whole field list, both embed snippets, and everything the
 * editor needs in order to offer a choice.
 *
 * The four requests run concurrently: the page is a single screen with four
 * pickers on it, and four sequential round trips is three too many.
 *
 * Another org's id answers 404, not 403, so the id space cannot be used to
 * discover which forms exist elsewhere. Callers carry that through to their
 * error copy rather than "fixing" it into a uniform 403. The form fetch is
 * deliberately NOT wrapped in a try: a 404 there is the whole answer, and
 * swallowing it would render an editor for a form that does not exist.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getWebForm({ cookies }, id) {
  if (!id) throw new Error('Which form? No form id was given.');
  const [form, profiles, customFields, tags] = await Promise.all([
    apiRequest(`/webforms/${id}/`, {}, { cookies }),
    listProfiles(cookies),
    listLeadCustomFields(cookies),
    listTags(cookies)
  ]);
  return {
    form,
    profiles,
    customFields,
    tags,
    canManage: viewerRole(cookies) === 'ADMIN'
  };
}

/**
 * The fields `WebFormDetailSerializer` accepts on create or update.
 *
 * An allow-list rather than a pass-through. `org`, `created_by` and
 * `is_published` are all derived server-side and would be ignored, but sending
 * one is how a reader comes to believe this page sets it. `fields` is nested
 * and handled separately below because its shape needs its own pass.
 */
const WRITABLE_FIELDS = [
  'name',
  'allowed_origins',
  'submit_button_label',
  'success_mode',
  'success_message',
  'redirect_url',
  'assign_to',
  'notify_profiles',
  'lead_source',
  'tags',
  'captcha_provider',
  'captcha_site_key',
  'captcha_secret',
  'reject_disposable_email'
];

/** The per-row keys the field serializer accepts. `order` is not among them:
 *  the server assigns it from list position, so a client cannot reorder a form
 *  by lying about it. `id` is read-only; the list is replaced wholesale. */
const WRITABLE_FIELD_KEYS = [
  'source',
  'lead_field',
  'custom_field',
  'label',
  'placeholder',
  'is_required'
];

/**
 * Shape the request body from the allow-lists.
 *
 * The name check is a fast fail for an obviously empty form, not the
 * authority: the model requires a name and the serializer re-validates
 * everything here regardless of what this lets through.
 *
 * @param {{ [key: string]: any }} values
 */
function buildBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of WRITABLE_FIELDS) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }

  if (body.name !== undefined) {
    body.name = String(body.name).trim();
    if (!body.name) throw new Error('A form needs a name.');
  }

  if (Array.isArray(values.fields)) {
    body.fields = values.fields.map((/** @type {any} */ row) => {
      /** @type {Record<string, any>} */
      const out = {};
      for (const key of WRITABLE_FIELD_KEYS) {
        if (row[key] === undefined) continue;
        out[key] = row[key];
      }
      return out;
    });
  }

  return body;
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ [key: string]: any }} values
 */
export async function createWebForm({ cookies }, values) {
  const body = buildBody(values);
  if (!body.name) throw new Error('A form needs a name.');
  return await apiRequest('/webforms/', { method: 'POST', body }, { cookies });
}

/**
 * PUT, which the view runs with `partial=True`, so this is a partial update
 * despite the verb: only the keys sent are touched. `fields`, when present,
 * replaces the whole list in one request rather than one request per row.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ [key: string]: any }} values
 */
export async function updateWebForm({ cookies }, id, values) {
  if (!id) throw new Error('Which form? No form id was given.');
  const body = buildBody(values);
  return await apiRequest(`/webforms/${id}/`, { method: 'PUT', body }, { cookies });
}

/**
 * A hard delete, and it takes the submissions with it (the FK cascades).
 * There is no soft-delete here: an unpublished form already accepts nothing,
 * which is what "switch it off but keep the history" means.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function deleteWebForm({ cookies }, id) {
  if (!id) throw new Error('Which form? No form id was given.');
  return await apiRequest(`/webforms/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * Start accepting submissions.
 *
 * Its own endpoint, not a field on update: it validates the source state and
 * the form's shape, and answers 400 with the reason when either is wrong.
 * Callers should show that reason rather than a generic failure, because
 * "add an email field first" is the whole content of the response.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function publishWebForm({ cookies }, id) {
  if (!id) throw new Error('Which form? No form id was given.');
  return await apiRequest(`/webforms/${id}/publish/`, { method: 'POST' }, { cookies });
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function unpublishWebForm({ cookies }, id) {
  if (!id) throw new Error('Which form? No form id was given.');
  return await apiRequest(`/webforms/${id}/unpublish/`, { method: 'POST' }, { cookies });
}

/**
 * Submissions for one form, newest first, accepted and rejected alike.
 *
 * `reject_reason` is deliberately absent from the API's response: naming the
 * control that caught a bot is how the next bot gets past it. The status is
 * what a person acts on, and that is what comes back.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {number} limit
 */
export async function getSubmissions({ cookies }, id, limit = 25) {
  if (!id) throw new Error('Which form? No form id was given.');
  const resp = await apiRequest(`/webforms/${id}/submissions/?limit=${limit}`, {}, { cookies });
  return { submissions: resp?.results ?? [], count: resp?.count ?? 0 };
}

/**
 * Views, submissions and conversion over a fixed trailing 30 days.
 *
 * The window is not a parameter here because it is not one on the API either.
 * The series is zero-filled server-side, so a day with no activity is a real
 * zero rather than a gap that would make a chart lie about its shape.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getAnalytics({ cookies }, id) {
  if (!id) throw new Error('Which form? No form id was given.');
  return await apiRequest(`/webforms/${id}/analytics/`, {}, { cookies });
}
