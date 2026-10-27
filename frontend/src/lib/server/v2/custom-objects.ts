/**
 * Server-side calls to the custom objects API (`/api/objects/`).
 *
 * A self-hosted install has no such routes and answers 404 to all of them;
 * `getCustomObjects` and `getRelated` turn that into `null` so callers can
 * hide the feature. Every key and id is checked before it reaches a path.
 */

import { error, type Cookies } from '@sveltejs/kit';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';
import type { components as CommunityComponents } from '#lib/api-schema.js';
import { apiErrorBody, apiStatus } from '#lib/api-errors.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { listLeads } from '#lib/server/v2/leads.js';
import { listContacts } from '#lib/server/v2/contacts.js';
import { listAccounts } from '#lib/server/v2/accounts.js';
import { listDeals } from '#lib/server/v2/deals.js';
import { listTickets } from '#lib/server/v2/tickets.js';
import { listTasks } from '#lib/server/v2/tasks.js';
import { attachmentHref } from '#lib/server/v2/files.js';
import { isPhoto, OBJECT_KEY, UUID } from '#lib/v2/custom-objects.js';

export const FIELD_KEY = /^[a-z][a-z0-9_]{0,63}$/;

const PAGE_SIZE = 20;
const ABSENT_FOR_MS = 5 * 60 * 1000;
const NOT_AVAILABLE = 'Custom objects are not available here.';
export const OBJECT_GONE = 'That object does not exist, or you do not have access to it.';
const RECORD_GONE = 'That record does not exist, or you do not have access to it.';

type Event = { cookies: Cookies };

type Enterprise = components['schemas'];
type ObjectSummary = Enterprise['CustomObjectSummary'];
export type ObjectRef = Enterprise['CustomObjectRef'];
export type FieldRef = Enterprise['CustomFieldRef'];
export type ObjectField = Enterprise['CustomObjectField'];
export type ObjectList = Enterprise['CustomObjectList'];
type ObjectDetail = Enterprise['CustomObjectDetail'];
type RecordAttachment = Enterprise['CustomRecordAttachment'];

type RecordList = Enterprise['CustomRecordList'];
export type RecordDetail = Enterprise['CustomRecordDetail'];
type RecordWrite = Enterprise['RecordWriteRequest'];
type RecordPatch = Enterprise['PatchedRecordWriteRequest'];
export type RelatedRecords = Enterprise['RelatedRecords'];

export function checkKey(key: string): string {
  if (typeof key !== 'string' || !OBJECT_KEY.test(key)) error(404, OBJECT_GONE);
  return key;
}

export function checkFieldKey(key: string): string {
  if (typeof key !== 'string' || !FIELD_KEY.test(key)) error(404, OBJECT_GONE);
  return key;
}

export function checkId(id: unknown): string {
  if (typeof id !== 'string' || !UUID.test(id)) error(404, RECORD_GONE);
  return id;
}

/** A query string with %20 for spaces, as the API paths are written elsewhere. */
function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(name, String(value));
  }
  return params.toString().replaceAll('+', '%20');
}

/** Run a call, turning a 404 into a page 404 and rethrowing anything else. */
async function orNotFound<T>(call: () => Promise<T>, message: string): Promise<T> {
  try {
    return await call();
  } catch (err) {
    if (apiStatus(err) === 404) error(404, message);
    throw err;
  }
}

/**
 * Set when `GET /objects/` answers 404 (a backend without custom objects), so
 * that backend's request log gains one line per 5 minutes, not one per page.
 */
let absentUntil = 0;

const absent = () => Date.now() < absentUntil;

/** Forget the absent memo; for tests. */
export function resetObjectsAbsentMemo() {
  absentUntil = 0;
}

/**
 * The objects answer, or null where there are no custom objects or the call
 * failed for any reason.
 */
export async function getCustomObjects({ cookies }: Event): Promise<ObjectList | null> {
  if (absent()) return null;
  try {
    const body: ObjectList = await apiRequest(
      '/objects/',
      { quietErrors: true, expectedStatuses: [404] },
      { cookies }
    );
    absentUntil = 0;
    return Array.isArray(body?.objects) && Array.isArray(body?.link_targets) ? body : null;
  } catch (err) {
    if (apiStatus(err) === 404) absentUntil = Date.now() + ABSENT_FOR_MS;
    return null;
  }
}

/** Every object, archived ones included (admins only see those). */
export async function listObjectsForSettings({ cookies }: Event): Promise<ObjectList> {
  return await orNotFound(
    () => apiRequest('/objects/?include_archived=true', { expectedStatuses: [404] }, { cookies }),
    NOT_AVAILABLE
  );
}

/** @returns The object with its active `fields`. */
export async function getObject({ cookies }: Event, key: string): Promise<ObjectDetail> {
  checkKey(key);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/`, { expectedStatuses: [404] }, { cookies }),
    OBJECT_GONE
  );
}

/** @returns Admins also get turned-off fields. */
export async function listFields({ cookies }: Event, key: string): Promise<ObjectField[]> {
  checkKey(key);
  const response: { fields: ObjectField[] } = await orNotFound(
    () => apiRequest(`/objects/${key}/fields/`, { expectedStatuses: [404] }, { cookies }),
    OBJECT_GONE
  );
  return response?.fields ?? [];
}

export async function createObject(
  { cookies }: Event,
  body: FormBody<Enterprise['ObjectCreateRequest']>
): Promise<ObjectSummary> {
  return await apiRequest('/objects/', { method: 'POST', body }, { cookies });
}

export async function updateObject(
  { cookies }: Event,
  key: string,
  body: FormBody<Enterprise['PatchedObjectUpdateRequest']>
): Promise<ObjectSummary> {
  checkKey(key);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/`, { method: 'PATCH', body }, { cookies }),
    OBJECT_GONE
  );
}

/**
 * Archives an active object; deletes an archived one for good.
 *
 * @returns The archived object, or null on 204.
 */
export async function deleteObject({ cookies }: Event, key: string): Promise<ObjectSummary | null> {
  checkKey(key);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/`, { method: 'DELETE' }, { cookies }),
    OBJECT_GONE
  );
}

export async function createField(
  { cookies }: Event,
  key: string,
  body: FormBody<Enterprise['FieldCreateRequest']>
): Promise<ObjectField> {
  checkKey(key);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/fields/`, { method: 'POST', body }, { cookies }),
    OBJECT_GONE
  );
}

export async function updateField(
  { cookies }: Event,
  key: string,
  fieldKey: string,
  body: FormBody<Enterprise['PatchedFieldUpdateRequest']>
): Promise<ObjectField> {
  checkKey(key);
  checkFieldKey(fieldKey);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/fields/${fieldKey}/`, { method: 'PATCH', body }, { cookies }),
    OBJECT_GONE
  );
}

/** Turns a field off; its values and links are kept. */
export async function turnOffField(
  { cookies }: Event,
  key: string,
  fieldKey: string
): Promise<ObjectField> {
  checkKey(key);
  checkFieldKey(fieldKey);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/fields/${fieldKey}/`, { method: 'DELETE' }, { cookies }),
    OBJECT_GONE
  );
}

export async function listRecords(
  { cookies }: Event,
  key: string,
  { q = '', page = 1, filters }: { q?: string; page?: number; filters?: URLSearchParams } = {}
): Promise<RecordList> {
  checkKey(key);
  const offset = (Math.max(1, Math.floor(page) || 1) - 1) * PAGE_SIZE;
  const params = new URLSearchParams();
  if (q.trim()) params.set('q', q.trim());
  for (const [name, value] of filters ?? []) {
    if (name === 'limit' || name === 'offset') continue;
    if (name === 'q' && params.has('q')) continue;
    params.append(name, value);
  }
  params.set('limit', String(PAGE_SIZE));
  params.set('offset', String(offset));
  const qs = params.toString().replaceAll('+', '%20');
  return await orNotFound(
    () => apiRequest(`/objects/${key}/records/?${qs}`, { expectedStatuses: [404] }, { cookies }),
    OBJECT_GONE
  );
}

/**
 * Whether the caller may bulk-import: an admin, or a member with sales
 * access, as the API's `can_mass_import` decides. A display hint only; the
 * import endpoints refuse the rest.
 */
export async function mayMassImport({ cookies }: Event, isAdmin: boolean): Promise<boolean> {
  if (isAdmin) return true;
  try {
    const me: CommunityComponents['schemas']['ProfileViewResponse'] = await apiRequest(
      '/profile/',
      { quietErrors: true },
      { cookies }
    );
    return me?.user_obj?.has_sales_access === true;
  } catch {
    return false;
  }
}

export async function getRecord(
  { cookies }: Event,
  key: string,
  id: string
): Promise<RecordDetail> {
  checkKey(key);
  checkId(id);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/records/${id}/`, { expectedStatuses: [404] }, { cookies }),
    RECORD_GONE
  );
}

/**
 * What the record detail page needs: the definition and the record, loaded
 * together, with attachments split into photos and files.
 */
export async function recordPage(event: Event, key: string, id: string) {
  checkKey(key);
  checkId(id);
  const [definition, record] = await Promise.all([
    getObject(event, key),
    getRecord(event, key, id)
  ]);
  if (definition.is_active === false) error(404, OBJECT_GONE);
  const attachments = (record.attachments ?? []).map((a) => ({
    ...a,
    href: attachmentHref(a.id)
  }));
  return {
    definition,
    record,
    photos: attachments.filter((a) => isPhoto(a)),
    files: attachments.filter((a) => !isPhoto(a))
  };
}

export async function createRecord(
  { cookies }: Event,
  key: string,
  body: RecordWrite
): Promise<RecordDetail> {
  checkKey(key);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/records/`, { method: 'POST', body }, { cookies }),
    RECORD_GONE
  );
}

/** Changes only what the body sends. */
export async function updateRecord(
  { cookies }: Event,
  key: string,
  id: string,
  body: RecordPatch
): Promise<RecordDetail> {
  checkKey(key);
  checkId(id);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/records/${id}/`, { method: 'PATCH', body }, { cookies }),
    RECORD_GONE
  );
}

export async function deleteRecord({ cookies }: Event, key: string, id: string): Promise<null> {
  checkKey(key);
  checkId(id);
  return await orNotFound(
    () => apiRequest(`/objects/${key}/records/${id}/`, { method: 'DELETE' }, { cookies }),
    RECORD_GONE
  );
}

/** Adds a note to a record the caller can read; the answer is the record's detail. */
export async function addRecordNote(
  { cookies }: Event,
  key: string,
  id: string,
  comment: string
): Promise<RecordDetail> {
  checkKey(key);
  checkId(id);
  return await apiRequest(
    `/objects/${key}/records/${id}/`,
    { method: 'POST', body: { comment } },
    { cookies }
  );
}

/** Only the author may; the API refuses anyone else with a 403. */
export async function editRecordNote(
  { cookies }: Event,
  key: string,
  commentId: string,
  comment: string
): Promise<Enterprise['Comment']> {
  checkKey(key);
  checkId(commentId);
  return await apiRequest(
    `/objects/${key}/comments/${commentId}/`,
    { method: 'PATCH', body: { comment } },
    { cookies }
  );
}

/** The author or an admin; the API refuses anyone else with a 403. */
export async function deleteRecordNote(
  { cookies }: Event,
  key: string,
  commentId: string
): Promise<null> {
  checkKey(key);
  checkId(commentId);
  return await apiRequest(
    `/objects/${key}/comments/${commentId}/`,
    { method: 'DELETE' },
    { cookies }
  );
}

export async function uploadAttachment(
  { cookies }: Event,
  key: string,
  id: string,
  file: File
): Promise<RecordAttachment> {
  checkKey(key);
  checkId(id);
  const body = new FormData();
  body.append('file', file);
  return await orNotFound(
    () =>
      apiRequest(
        `/objects/${key}/records/${id}/attachments/`,
        { method: 'POST', body },
        { cookies }
      ),
    RECORD_GONE
  );
}

export async function deleteAttachment(
  { cookies }: Event,
  key: string,
  id: string,
  attachmentId: string
): Promise<null> {
  checkKey(key);
  checkId(id);
  checkId(attachmentId);
  return await orNotFound(
    () =>
      apiRequest(
        `/objects/${key}/records/${id}/attachments/${attachmentId}/`,
        { method: 'DELETE' },
        { cookies }
      ),
    RECORD_GONE
  );
}

const BUILT_IN = ['lead', 'contact', 'account', 'deal', 'ticket', 'task'];

/**
 * Custom records linked to a built-in record, or null on any failure.
 *
 * @param to A built-in registry key.
 */
export async function getRelated(
  { cookies }: Event,
  to: string,
  id: string
): Promise<RelatedRecords | null> {
  if (!BUILT_IN.includes(to) || !UUID.test(id)) return null;
  // Once the absent memo lapses, re-ask `/objects/` rather than this route,
  // which 404s on every detail view of a backend without custom objects.
  if (absentUntil && !(await getCustomObjects({ cookies }))) return null;
  try {
    const body: RelatedRecords = await apiRequest(
      `/objects/related/?${query({ to, id })}`,
      { quietErrors: true, expectedStatuses: [404] },
      { cookies }
    );
    return Array.isArray(body?.groups) && Array.isArray(body?.link_fields) ? body : null;
  } catch {
    return null;
  }
}

function checkLink({
  to,
  id,
  record,
  field
}: {
  to: string;
  id: string;
  record: string;
  field: string;
}) {
  if (!BUILT_IN.includes(to)) error(404, RECORD_GONE);
  checkId(id);
  checkId(record);
  checkFieldKey(field);
}

export async function addRelated(
  { cookies }: Event,
  link: { to: string; id: string; record: string; field: string }
): Promise<{ added: boolean }> {
  checkLink(link);
  const { to, id, record, field } = link;
  return await apiRequest(
    '/objects/related/',
    { method: 'POST', body: { to, id, record, field } },
    { cookies }
  );
}

export async function removeRelated(
  { cookies }: Event,
  link: { to: string; id: string; record: string; field: string }
): Promise<null> {
  checkLink(link);
  const { to, id, record, field } = link;
  return await apiRequest(
    `/objects/related/?${query({ to, id, record, field })}`,
    { method: 'DELETE' },
    { cookies }
  );
}

const byName = ({ results }: { results: { id: string; name: string }[] }) =>
  results.map((row) => ({ id: row.id, name: row.name }));

/**
 * Each built-in kind's own list helper, so a name reads as it does on that
 * module's list page.
 */
const SEARCH: Record<
  string,
  (event: Event, params: URLSearchParams) => Promise<{ id: string; name: string }[]>
> = {
  lead: async (event, params) =>
    (await listLeads(event, params)).results.map((row) => ({
      id: row.id,
      name:
        [row.first_name, row.last_name].filter(Boolean).join(' ').trim() ||
        row.company_name ||
        row.email ||
        'Untitled'
    })),
  contact: async (event, params) => byName(await listContacts(event, params)),
  account: async (event, params) => byName(await listAccounts(event, params)),
  deal: async (event, params) => byName(await listDeals(event, params)),
  ticket: async (event, params) => byName(await listTickets(event, params)),
  task: async (event, params) =>
    (await listTasks(event, params)).results.map((row) => ({ id: row.id, name: row.title }))
};

/**
 * Names for a link picker, at most 20.
 *
 * @param target A built-in registry key, or `obj:<key>`.
 */
export async function searchLinkTargets(
  event: Event,
  target: string,
  q: string
): Promise<{ id: string; name: string }[]> {
  const search = String(q ?? '').trim();
  if (Object.hasOwn(SEARCH, target)) {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
    if (search) params.set('search', search);
    return (await SEARCH[target](event, params)).slice(0, PAGE_SIZE);
  }
  if (typeof target === 'string' && target.startsWith('obj:') && OBJECT_KEY.test(target.slice(4))) {
    const qs = query({ q: search, limit: PAGE_SIZE });
    const response: RecordList = await apiRequest(
      `/objects/${target.slice(4)}/records/?${qs}`,
      {},
      { cookies: event.cookies }
    );
    return (response?.results ?? [])
      .slice(0, PAGE_SIZE)
      .map((row) => ({ id: row.id, name: row.name }));
  }
  return [];
}

const first = (value: unknown) => String(Array.isArray(value) ? value[0] : value);

const asRecord = (value: unknown) =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {};

/**
 * A refused record save as one line per refused field.
 *
 * @param err The error thrown by a record write.
 * @param fields The object's field list.
 * @param nameLabel The object's `name_label`.
 */
export function recordWriteError(
  err: unknown,
  fields: { key: string; label: string }[],
  nameLabel = 'Name'
): string {
  const body = apiErrorBody(err);
  const label = (key: string) => fields.find((field) => field.key === key)?.label ?? key;
  const lines: string[] = [];
  if (body?.name) lines.push(`${nameLabel}: ${first(body.name)}`);
  for (const [key, message] of Object.entries(asRecord(body?.custom_fields))) {
    lines.push(key === '_size' ? first(message) : `${label(key)}: ${first(message)}`);
  }
  for (const [key, message] of Object.entries(asRecord(body?.links))) {
    lines.push(`${label(key)}: ${first(message)}`);
  }
  if (body?.assigned_to) lines.push(`Assignees: ${first(body.assigned_to)}`);
  if (body?.tags) lines.push(`Tags: ${first(body.tags)}`);
  if (lines.length) return lines.join('\n');
  if (typeof body?.detail === 'string') return body.detail;
  return readableError(err, 'Could not save this record.');
}
