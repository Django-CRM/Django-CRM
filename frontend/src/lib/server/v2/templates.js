/**
 * Invoice templates: the eighteenth v2 module and the highest-risk of the
 * invoices sub-pages. Server-only, like every v2 module: the access token is an
 * httpOnly cookie and the org is a JWT claim the backend reads, never a param.
 *
 * WHAT A TEMPLATE IS, AND WHY THIS PAGE IS CAREFUL
 * A template is org-wide shared config: how every invoice looks when it reaches
 * a customer. `template_html` / `template_css` are org-authored HTML/CSS that
 * WeasyPrint renders into a PDF on the SERVER. They must never reach this app's
 * DOM: the moment either lands in a `{@html}` a PDF setting becomes stored XSS.
 * The backend list/detail serializer now strips both fields (a `has_custom_html`
 * boolean and a byte count stand in for them), so, unlike the old mock, which
 * omitted the blobs at the client boundary. The markup never leaves the DB in
 * the first place. `toRow` whitelists fields anyway, as a second wall.
 *
 * THE FINDING THIS MODULE CARRIED
 * Both template endpoints were `(IsAuthenticated, HasOrgContext)` only: any
 * member could create, edit, delete, or re-point the org default, and could
 * write raw `template_html`/`template_css` that feeds the server-side PDF
 * render. That was fixed on the backend (admin-only writes + a `url_fetcher`
 * that stops the WeasyPrint SSRF/local-file-read); this page is the client that
 * draws the one write a worklist should own.
 *
 * THE WRITES: SET DEFAULT, CREATE, EDIT
 * `is_default` is a singleton, the model clears it on every other row inside a
 * transaction, so making a template the default is a swap, not a toggle. It is
 * owned by the list page's "Use instead of" control and deliberately absent
 * from the edit form, so that saving an unrelated change can never demote the
 * org's default by submitting an unchecked box.
 *
 * Editing used to be impossible rather than merely unwired: the detail GET
 * answers with the same stripping serializer as the list, so a form could not
 * pre-fill the two fields it exists to change, and saving would have blanked
 * them. `GET /invoices/templates/<id>/editor/` now serves the raw markup on its
 * own admin-only route, which is what `getInvoiceTemplateForEdit` reads. The
 * detail and list responses are unchanged and still strip both fields.
 *
 * The markup is bound to `<textarea>` values, never to `{@html}`. That rule is
 * what makes serving it safe, and it is the one thing a future edit to these
 * pages must not break.
 *
 * TOTALS ARE COMPUTED HERE
 * The list endpoint has no `totals` envelope. `count` comes from the response;
 * `unused` (templates no invoice uses and that are not the default. Safe to
 * delete) is derived from the rows. `limit=1000`; an org has a handful of
 * templates, and that ceiling is called out where it is set.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from '$lib/server/v2/organization.js';

const num = (/** @type {any} */ value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/**
 * A list row from `InvoiceTemplateListSerializer`. Whitelisted on purpose: the
 * raw `template_html`/`template_css` are not on the wire, and this makes sure
 * nothing downstream can start depending on them if that ever regresses.
 *
 * @param {any} row
 */
function toRow(row) {
  return {
    id: row.id,
    name: row.name || '(untitled template)',
    is_default: Boolean(row.is_default),
    primary_color: row.primary_color || '#000000',
    secondary_color: row.secondary_color || '#000000',
    has_logo: Boolean(row.has_logo),
    has_custom_html: Boolean(row.has_custom_html),
    custom_html_bytes: num(row.custom_html_bytes),
    default_notes: row.default_notes || '',
    default_terms: row.default_terms || '',
    footer_text: row.footer_text || '',
    used_on_invoices: num(row.used_on_invoices),
    updated_at: row.updated_at || null,
    updated_by: row.updated_by || '—'
  };
}

/**
 * The two header figures, derived from the visible rows.
 *
 * @param {any[]} rows
 * @param {number | undefined} count
 */
function computeTotals(rows, count) {
  let unused = 0;
  for (const t of rows) {
    if (!t.is_default && t.used_on_invoices === 0) unused += 1;
  }
  return { count: count ?? rows.length, unused };
}

/**
 * The template catalogue. Reading is open to any member (they see how their
 * invoices print); the writes below are the admin-gated part.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function listInvoiceTemplates({ cookies }) {
  const query = new URLSearchParams();
  // One page covers every real org; the derived totals need the whole set.
  query.set('limit', '1000');

  const response = await apiRequest(`/invoices/templates/?${query.toString()}`, {}, { cookies });
  const rows = (response.results ?? []).map(toRow);

  return { templates: rows, totals: computeTotals(rows, response.count) };
}

/**
 * Make one template the org default. A partial update flipping `is_default`;
 * the model swaps the flag off whatever held it. The API admin-gates this.
 * This layer does not decide, it just asks, and a non-admin gets a 403.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function setDefaultTemplate({ cookies }, id) {
  return apiRequest(
    `/invoices/templates/${id}/`,
    { method: 'PUT', body: { is_default: true } },
    { cookies }
  );
}

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/**
 * Every field the create form is allowed to write.
 *
 * `template_html` and `template_css` are absent because a new template starts
 * from the built-in layout: replacing the whole document, line-item table and
 * totals included, is a deliberate follow-up on an existing template, not part
 * of naming a new one. The edit form owns those two fields (`UPDATE_FIELDS`),
 * where the saved markup can be shown alongside the change.
 *
 * That reasoning used to be different and is worth not re-deriving: they were
 * excluded because every read serializer stripped them, so a value written
 * here could never be read back. The editor route fixed that.
 */
export const CREATE_FIELDS = [
  'name',
  'primary_color',
  'secondary_color',
  'default_notes',
  'default_terms',
  'footer_text',
  'is_default'
];

/**
 * Every field the edit form is allowed to write.
 *
 * `is_default` is deliberately not here. It is a singleton the model swaps
 * inside a transaction, and it has its own control on the list page. Reading it
 * from an edit form means an unchecked box rides along with every unrelated
 * save, which on the org's current default would quietly demote it and leave
 * the org with none.
 */
export const UPDATE_FIELDS = [
  'name',
  'primary_color',
  'secondary_color',
  'default_notes',
  'default_terms',
  'footer_text',
  'template_html',
  'template_css'
];

/**
 * Create an invoice template.
 *
 * Admin-only server-side: `_forbid_non_admin_template` 403s anyone whose
 * `profile.role` is not ADMIN and who is not a superuser. The page hides the
 * control using `can_manage`, but that is a display hint decoded from the JWT;
 * the backend re-derives the role and is the check that matters.
 *
 * The colour checks below now mirror `_validate_hex_color` in
 * `InvoiceTemplateCreateSerializer`, which is the check that counts. They used
 * to mirror nothing: the model is `max_length=7` and had no format validator,
 * so `"purple"` was stored happily and then rendered as an invalid CSS colour
 * in the PDF. The serializer closed that when the phone became the third client
 * constraining the field. These stay because a client-side message beats a raw
 * 400 on a field the user picked from a swatch.
 *
 * `is_default` is not a plain field write: the model's `save()` clears the flag
 * on every other template in the org inside a transaction, so setting it here
 * demotes whichever template is currently default.
 *
 * When a logo is supplied the body must be multipart, because `logo` is an
 * ImageField. `apiRequest` already detects a `FormData` body and leaves
 * `Content-Type` unset so the browser adds the boundary.
 *
 * `InvoiceTemplateListView.post` (`backend/invoices/api_views.py`) wraps its
 * result in an envelope, `{ error, message, template }`, exactly the trap
 * `createTag` documents in `tags.js`: a `create*` function that resolves to
 * the envelope instead of the record is the one the next caller reaches for
 * `.id` on and gets `undefined`. This unwraps to `.template` deliberately,
 * with the same `?? resp` fallback as `createTag`, so a change in the
 * backend's response shape degrades to the raw envelope rather than throwing.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 * @param {File | null} [logoFile]
 * @returns {Promise<any>}
 */
export async function createInvoiceTemplate({ cookies }, values, logoFile = null) {
  const name = (values.name ?? '').toString().trim();
  if (!name) throw new Error('Give the template a name.');

  for (const key of ['primary_color', 'secondary_color']) {
    const colour = values[key];
    if (colour !== undefined && colour !== '' && !HEX_COLOR.test(String(colour))) {
      throw new Error('Colours must be a six digit hex value, for example #3B82F6.');
    }
  }

  /** @type {Record<string, any>} */
  const fields = {};
  for (const key of CREATE_FIELDS) {
    if (values[key] === undefined) continue;
    // An empty colour is omitted rather than sent as `''`. The model has no
    // `blank=True` on either colour, so DRF would answer a literal empty string
    // with "This field may not be blank", while omitting the key lets the model
    // default apply. Only reachable if `input type="color"` degrades to a text
    // field, but the failure would be a raw 400 with no useful message.
    if ((key === 'primary_color' || key === 'secondary_color') && values[key] === '') continue;
    fields[key] = key === 'name' ? name : values[key];
  }

  if (logoFile) {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) {
      body.append(key, typeof value === 'boolean' ? String(value) : value);
    }
    body.append('logo', logoFile);
    const resp = await apiRequest('/invoices/templates/', { method: 'POST', body }, { cookies });
    return resp.template ?? resp;
  }

  const resp = await apiRequest(
    '/invoices/templates/',
    { method: 'POST', body: fields },
    { cookies }
  );
  return resp.template ?? resp;
}

/**
 * Load one template for the edit form, including the raw markup.
 *
 * Refuses a non-admin up front with `can_edit: false`, the same shape
 * `getProductForEdit` uses, so the form is never drawn for someone the PUT
 * would refuse and the markup is not even requested. That is a display
 * decision; the editor route re-derives the role server-side and 403s anyway.
 *
 * This is the only call in the app that receives `template_html` /
 * `template_css`, and it reads the dedicated editor route rather than the
 * detail route, so nothing here widens what an ordinary member's page fetches.
 *
 * The two markup fields come back as plain strings for a `<textarea>` value.
 * Binding either into `{@html}` would turn a PDF setting into stored XSS, which
 * is the whole reason every other read strips them.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getInvoiceTemplateForEdit({ cookies }, id) {
  if (!viewerIsAdmin(cookies)) return { can_edit: false };

  const t = await apiRequest(`/invoices/templates/${id}/editor/`, {}, { cookies });
  return {
    can_edit: true,
    template: {
      id: t.id,
      name: t.name ?? '',
      primary_color: t.primary_color || '#3B82F6',
      secondary_color: t.secondary_color || '#1E40AF',
      default_notes: t.default_notes ?? '',
      default_terms: t.default_terms ?? '',
      footer_text: t.footer_text ?? '',
      template_html: t.template_html ?? '',
      template_css: t.template_css ?? '',
      is_default: Boolean(t.is_default),
      has_logo: Boolean(t.logo)
    }
  };
}

/**
 * Save an edit. `PUT /invoices/templates/<id>/` is already `partial=True` and
 * admin-gated on the server; this sends only the fields the form owns.
 *
 * Clearing the markup is a real edit, so an empty string is sent rather than
 * dropped: omitting it under `partial=True` would mean "leave it alone", and an
 * admin who selects the markup and deletes it expects it gone. The empty-colour
 * skip from `createInvoiceTemplate` is kept for the opposite reason: neither
 * colour field is `blank=True`, so `''` is a 400 rather than a clear.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 * @param {File | null} [logoFile]
 */
export async function updateInvoiceTemplate({ cookies }, id, values, logoFile = null) {
  const name = (values.name ?? '').toString().trim();
  if (!name) throw new Error('Give the template a name.');

  for (const key of ['primary_color', 'secondary_color']) {
    const colour = values[key];
    if (colour !== undefined && colour !== '' && !HEX_COLOR.test(String(colour))) {
      throw new Error('Colours must be a six digit hex value, for example #3B82F6.');
    }
  }

  /** @type {Record<string, any>} */
  const fields = {};
  for (const key of UPDATE_FIELDS) {
    if (values[key] === undefined) continue;
    if ((key === 'primary_color' || key === 'secondary_color') && values[key] === '') continue;
    fields[key] = key === 'name' ? name : values[key];
  }

  if (logoFile) {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) {
      body.append(key, typeof value === 'boolean' ? String(value) : value);
    }
    body.append('logo', logoFile);
    const resp = await apiRequest(
      `/invoices/templates/${id}/`,
      { method: 'PUT', body },
      { cookies }
    );
    return resp.template ?? resp;
  }

  const resp = await apiRequest(
    `/invoices/templates/${id}/`,
    { method: 'PUT', body: fields },
    { cookies }
  );
  return resp.template ?? resp;
}
