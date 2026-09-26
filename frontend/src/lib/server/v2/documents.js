/**
 * Documents: the thirteenth v2 module wired to the real API.
 *
 * Lives under `$lib/server` like the twelve before it: SvelteKit keeps this
 * directory out of the client bundle, so the httpOnly access token never
 * reaches the browser. Org is a JWT claim the backend re-derives; it is never a
 * parameter here.
 *
 * WHY THIS MODULE
 * `/documents` is a top-of-sidebar destination that still read a fixture:
 * its list rendered mock rows and its "Upload" button wrote nowhere. `Document`
 * has had a full API (`common/views/document_views.py`) since the first
 * migration and neither v1 nor Flutter ever drew it, so its access rules had
 * only ever been exercised by tests. Wiring it drove those rules for real.
 * The org-scoped, share-aware list read, plus the three writes (upload, edit,
 * delete).
 *
 * THE FINDING THE WIRING EXPOSED
 * `DocumentDetailView.put`/`patch` authorised on `_may_read`: creator, anyone
 * in `shared_to`, any member of a shared `teams`, or an admin. So anyone a
 * document was merely *shared with* could overwrite its file, rename it, flip
 * its status to `inactive` (hiding it), and, through PUT, which clears the M2M
 * before re-adding, wipe the whole share list and lock everyone else out. The
 * same view already gated `delete` narrowly with a written rationale ("a share
 * hands someone a copy to work with, not the right to remove it from everyone
 * else"); rewriting the bytes behind a title is at least as destructive as
 * deleting the row, yet mutation was left at read level. The backend fix adds
 * `_may_write` = `_may_delete` (creator or admin) and uses it on PUT and PATCH.
 * This file only offers an Edit affordance where the viewer can write, but that
 * is a UX hint. The serializer/view is the trust boundary. Uploading is a
 * separate, unrestricted operation: any org member may add a document (the
 * server checks only auth + org), so the page does not gate the Upload button.
 *
 * WHAT THE MOCK ASSUMED THAT THE API SHAPES DIFFERENTLY
 * - `GET /api/documents/` returns two separately paginated envelopes
 *   (`documents_active` / `documents_inactive`) plus a `users` list, a v1
 *   artefact. `listDocuments` flattens both into the one list the page reads
 *   and recomputes the totals here (there is no summary endpoint), never a
 *   second query.
 * - `shared_to` is a nested `ProfileSerializer`; `toDoc` collapses each to
 *   `{ id, name }`. `created_by` is a `UserSerializer` (its `id` is the *user*
 *   id, which is what `can_write` compares the JWT `user_id` against).
 * - `file_kind` is derived from the filename here; `size_bytes` and each team's
 *   `member_count` are now returned by `DocumentSerializer` because they cannot
 *   be derived from the filename alone.
 * - `document_file` is a stored path and is only ever displayed. Constructing a
 *   media URL in the client is how a private file becomes a public one; the
 *   server decides who gets the bytes. `download_href` is that decision made
 *   properly: a route on this origin that streams from an authenticated
 *   endpoint gated by the same `_may_read` the detail view uses.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { env } from '$env/dynamic/public';
import { documentHref } from './files.js';
import { getOrgPeopleAndTeams } from './org-people.js';

const API_BASE_URL = `${env.PUBLIC_DJANGO_API_URL}/api`;

/** The two document states the backend accepts (Document.DOCUMENT_STATUS_CHOICE). */
export const STATUS_CHOICES = ['active', 'inactive'];

/**
 * Every filter param `listDocuments` will forward. The descriptor in
 * `$lib/v2/filters.js` must not name a key absent from this list; a test in
 * `filters.test.js` enforces that. `status` is the only one: `tags` has no
 * backing relation on `Document` at all, and `shared_to` is passed straight to
 * `json.loads` by `DocumentListView.get`, so a plain `?shared_to=<uuid>` would
 * 500 rather than filter.
 */
export const FILTER_FIELDS = ['status'];

/**
 * The signed-in role and user id, decoded from the JWT. Display hints only:
 * `role` decides whether the page offers Upload/Edit, and `user_id` is compared
 * to a document's `created_by` to decide the per-row Edit affordance. The
 * backend re-derives both and is the thing that actually refuses a write, so
 * neither is trusted for authorization.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @returns {{ role: string | null, userId: string | null }}
 */
function viewerClaims(cookies) {
  const token = cookies.get('jwt_access');
  if (!token) return { role: null, userId: null };
  try {
    const payload = token.split('.')[1];
    const json = Buffer.from(payload, 'base64url').toString('utf-8');
    const claims = JSON.parse(json);
    return { role: claims.role ?? null, userId: claims.user_id ?? null };
  } catch {
    return { role: null, userId: null };
  }
}

/** A person's display name from a nested ProfileSerializer row. */
function personName(profile) {
  const d = profile?.user_details ?? {};
  return d.name || d.email || 'Unnamed';
}

/**
 * The page's icon buckets, from the filename extension. The list maps `pdf`,
 * `sheet` and `text` to specific icons and everything else to a generic file,
 * so anything outside those three collapses to `'file'`.
 *
 * @param {string} path
 */
function fileKind(path = '') {
  const ext = String(path).split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'pdf') return 'pdf';
  if (['xls', 'xlsx', 'csv', 'ods', 'numbers'].includes(ext)) return 'sheet';
  if (['txt', 'md', 'markdown', 'doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'text';
  return 'file';
}

/**
 * One document as the page reads it, from a `DocumentSerializer` row.
 *
 * `can_write` mirrors the server's `_may_write`: an admin, or the person whose
 * user id matches `created_by`. It gates the Edit link so the row never offers
 * an action the server would refuse, but the view enforces it regardless.
 *
 * @param {any} d
 * @param {{ role: string | null, userId: string | null }} viewer
 */
function toDoc(d, viewer) {
  const createdById = d.created_by?.id != null ? String(d.created_by.id) : null;
  return {
    id: d.id,
    title: d.title ?? '',
    document_file: d.document_file ?? '',
    // Null when there is no file, so the row keeps rendering the name as text
    // instead of offering a download that 404s.
    download_href: d.document_file ? documentHref(d.id) : null,
    file_kind: fileKind(d.document_file),
    size_bytes: typeof d.size_bytes === 'number' ? d.size_bytes : null,
    status: d.status,
    shared_to: (d.shared_to ?? []).map((/** @type {any} */ p) => ({
      id: p.id,
      name: personName(p)
    })),
    teams: (d.teams ?? []).map((/** @type {any} */ t) => ({
      id: t.id,
      name: t.name,
      member_count: t.member_count ?? 0
    })),
    created_by: {
      id: createdById,
      name: d.created_by?.name || d.created_by?.email || 'Unknown'
    },
    created_at: d.created_at,
    can_write: viewer.role === 'ADMIN' || (createdById != null && createdById === viewer.userId)
  };
}

/**
 * Totals for the header and stat cards, over every document (not one page).
 * `unshared` counts documents given to nobody and no team, reachable only by
 * their uploader and admins. Not an error, so the page states it without alarm.
 *
 * @param {ReturnType<typeof toDoc>[]} docs
 */
function computeTotals(docs) {
  return {
    count: docs.length,
    active: docs.filter((d) => d.status === 'active').length,
    inactive: docs.filter((d) => d.status === 'inactive').length,
    unshared: docs.filter((d) => d.shared_to.length === 0 && d.teams.length === 0).length
  };
}

/**
 * The documents list. `limit=1000` fetches the whole (small) set through both
 * paginated envelopes so the totals cover every row. The backend has already
 * scoped the rows to the org and, for a non-admin, to what `_visible_to`
 * allows, so every row returned here is one the viewer may open.
 *
 * A `status` filter narrows both envelopes at once: `DocumentListView.get`
 * applies it to the base queryset before splitting into
 * `documents_active`/`documents_inactive`, so `?status=active` leaves the
 * inactive envelope empty and the merge below still returns the right rows.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listDocuments({ cookies }, params) {
  const viewer = viewerClaims(cookies);
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '1000');
  const resp = await apiRequest(`/documents/?${query}`, {}, { cookies });

  const active = resp?.documents_active?.documents_active ?? [];
  const inactive = resp?.documents_inactive?.documents_inactive ?? [];
  const results = [...active, ...inactive]
    .map((d) => toDoc(d, viewer))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  return { results, totals: computeTotals(results) };
}

/**
 * The share pickers for the upload form.
 *
 * Uploading is NOT admin-only: `DocumentListView.post` gates on
 * `(IsAuthenticated, HasOrgContext)` and nothing more, so any org member may
 * add a document (a rep attaching a discovery note, not just an admin), and a
 * UI that claimed "admins only" would be inventing a rule the server does not
 * enforce. Editing and deleting are the narrow writes (creator or admin,
 * enforced by `_may_write` / `_may_delete`); creating is open to the org.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getUploadOptions({ cookies }) {
  const { people, teams } = await getOrgPeopleAndTeams(cookies);
  return { people, teams };
}

/**
 * The document plus the picker options and current shares, for the edit form.
 * Refuses a non-writer up front: a non-admin who is not the creator gets
 * `can_edit: false` and an "only the owner or an admin" state, so the form is
 * never drawn for someone the PUT would refuse. The detail fetch is org-scoped,
 * so a bad or foreign id 404s.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getDocumentForEdit({ cookies }, id) {
  const viewer = viewerClaims(cookies);
  const raw = await apiRequest(`/documents/${id}/`, {}, { cookies });
  const doc = toDoc(raw.doc_obj, viewer);

  if (!doc.can_write) return { can_edit: false };

  const { people, teams, options_failed } = await getOrgPeopleAndTeams(cookies);
  const offered = new Set(people.map((p) => p.id));
  const shared = doc.shared_to.map((p) => p.id);
  return {
    can_edit: true,
    people,
    teams,
    options_failed,
    document: {
      id: doc.id,
      title: doc.title,
      status: doc.status,
      file_kind: doc.file_kind,
      document_file: doc.document_file,
      shared_to: shared,
      // Shares the picker has no checkbox for: people deactivated since, or
      // every share when the list failed to load. The page posts them back so
      // a PUT keeps them; the server re-accepts an inactive person only when
      // the document is already shared with them.
      kept_shares: shared.filter((id) => !offered.has(id)),
      teams: doc.teams.map((t) => t.id)
    }
  };
}

/**
 * `POST /api/documents/` as multipart. The shared `apiRequest` sends JSON only,
 * and an upload is a file, so this forwards a `FormData` by hand: the file
 * Blob under `document_file`, plus `shared_to`/`teams` as JSON-string arrays of
 * ids (the view `json.loads()`es them). `Content-Type` is left unset so fetch
 * writes the multipart boundary itself. Admin-only server-side.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {{ title: string, status: string, file: File, shared_to: string[], teams: string[] }} values
 */
export function uploadDocument(cookies, values) {
  return sendMultipart(cookies, '/documents/', 'POST', values);
}

/**
 * One multipart send, used by create and by the edit that replaces a file.
 *
 * `shared_to` and `teams` go as JSON-string arrays of ids because the view
 * `payload_id_list`es them, and `Content-Type` is left unset so fetch writes
 * the multipart boundary itself. Errors are re-thrown carrying the upstream
 * status and body, which is what the actions render.
 *
 * The two lists are left out when absent, which only a PATCH does: PATCH
 * never reads them, and a PUT always clears both.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} path
 * @param {'POST'|'PUT'|'PATCH'} method
 * @param {{ title: string, status: string, file: File, shared_to?: string[], teams?: string[] }} values
 */
async function sendMultipart(cookies, path, method, values) {
  const body = new FormData();
  body.append('title', values.title);
  body.append('status', values.status);
  body.append('document_file', values.file);
  if (values.shared_to) body.append('shared_to', JSON.stringify(values.shared_to));
  if (values.teams) body.append('teams', JSON.stringify(values.teams));

  /** @type {Record<string, string>} */
  const headers = {};
  const token = cookies.get('jwt_access');
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const failure = /** @type {Error & { status: number, body: any }} */ (
      new Error(JSON.stringify(data))
    );
    failure.status = response.status;
    failure.body = data;
    throw failure;
  }
  return response.json().catch(() => ({}));
}

/**
 * Save an edit. Gated by `_may_write` (creator or admin) on both verbs.
 *
 * The verb follows whether sharing changed, because the two verbs disagree
 * about it:
 *
 * - `PUT` replaces `shared_to` and `teams` with the body's lists, scoped to
 *   the caller's org. A person must be active, unless the document is already
 *   shared with them: a share to somebody deactivated since survives only if
 *   the body names it again (the edit page posts those as `kept_shares`). A
 *   missing list empties that relation, so a PUT built from a picker list that
 *   failed to load would drop every share the document has.
 * - `PATCH` writes `title`, `status` and `document_file` through
 *   `DocumentCreateSerializer` and never touches the two lists.
 *
 * So `shared_to`/`teams` are passed only when the person changed who can
 * open the document, and only as a pair (PUT empties whichever is missing,
 * so one list on its own is treated as no change at all). A
 * save that leaves sharing alone is a PATCH and keeps every share, inactive
 * ones included.
 *
 * Multipart when the form carried a replacement file, JSON otherwise, because
 * a rename should not have to resend the bytes. Deleting and re-uploading used
 * to be the only way to correct a wrong file, and it lost every share.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ title: string, status: string, shared_to?: string[], teams?: string[], file?: File | null }} values
 */
export function updateDocument({ cookies }, id, values) {
  const shares =
    values.shared_to && values.teams ? { shared_to: values.shared_to, teams: values.teams } : null;
  const method = shares ? 'PUT' : 'PATCH';
  if (values.file) {
    return sendMultipart(cookies, `/documents/${id}/`, method, {
      title: values.title,
      status: values.status,
      file: values.file,
      ...shares
    });
  }
  return apiRequest(
    `/documents/${id}/`,
    { method, body: { title: values.title, status: values.status, ...shares } },
    { cookies }
  );
}

/**
 * `DELETE /api/documents/<id>/`. Gated by `_may_delete` (creator or admin).
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export function deleteDocument({ cookies }, id) {
  return apiRequest(`/documents/${id}/`, { method: 'DELETE' }, { cookies });
}
