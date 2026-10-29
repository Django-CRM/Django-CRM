/**
 * Projects (`/api/projects/`): the list, the record, its time, and billing it.
 *
 * Server-only, for the same reason as every module here: the access token is
 * an httpOnly cookie. The org is never a parameter. Who may read, edit, bill
 * or delete is the API's call (deal rules for the first three, admins for
 * billing); the `can_*` flags it sends only decide what the page offers.
 *
 * Decimals arrive as strings and become numbers at this boundary, null kept
 * as null: a project without a budget is not a project with a budget of 0.
 */
import { authorText } from '#lib/v2/author.js';
import { error, type Cookies } from '@sveltejs/kit';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import { apiStatus } from '#lib/api-errors.js';
import { rethrowModuleOff } from '#lib/server/v2/modules.js';
import { attachmentHref } from '#lib/server/v2/files.js';
import { invoiceFromTimeEntries } from '#lib/server/v2/invoices.js';
import { loggedEntryBody } from '#lib/server/v2/timesheet.js';
import { listAccountsPicker } from '#lib/server/v2/accounts.js';
import { listDeals } from '#lib/server/v2/deals.js';
import { getOrgPeopleAndTeams } from '#lib/server/v2/org-people.js';
import { billGrouping, isoDateOrNull, isUuid, type RetainerUsage } from '#lib/v2/projects.js';
import type { components } from '#lib/api-schema.js';

type Schemas = components['schemas'];

/** Every list filter `listProjects` forwards; see the note in tickets.js. */
export const FILTER_FIELDS = ['status', 'account', 'assigned_to'];

function num(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function intOrNull(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null;
}

/**
 * A profile as the pages show it: the deal endpoints' shape, read the way
 * `tasks.js` reads it.
 */
export function toPerson(profile: Schemas['Profile'] | null | undefined) {
  const user = profile?.user_details;
  return { id: profile?.id ?? '', name: user?.name || user?.email || 'Someone' };
}

function toBurn(burn: Partial<Schemas['ProjectBurn']> | null | undefined) {
  return {
    logged_hours: num(burn?.logged_hours) ?? 0,
    billable_hours: num(burn?.billable_hours) ?? 0,
    billable_value: num(burn?.billable_value) ?? 0,
    unbilled_value: num(burn?.unbilled_value) ?? 0,
    // Null for a member, or while Invoices is off: not known, rather than 0.
    invoiced_amount: num(burn?.invoiced_amount),
    hours_percent: intOrNull(burn?.hours_percent),
    amount_percent: intOrNull(burn?.amount_percent)
  };
}

/** The month a retainer bill would take, or null off a retainer. */
function toRetainerUsage(usage: Schemas['ProjectDetail']['retainer_usage']): RetainerUsage | null {
  if (!usage || !usage.month) return null;
  return {
    month: usage.month,
    used_hours: num(usage.used_hours) ?? 0,
    available_hours: num(usage.available_hours),
    overage_hours: num(usage.overage_hours) ?? 0,
    billable_now: usage.billable_now === true
  };
}

/** @param p a list item */
function toRow(p: Schemas['ProjectList']) {
  return {
    id: p.id,
    name: p.name ?? '',
    account: { id: p.account?.id ?? null, name: p.account?.name ?? 'No account' },
    deal: p.deal?.id ? { id: p.deal.id, name: p.deal.name ?? '' } : null,
    status: p.status ?? 'active',
    billing_type: p.billing_type ?? 'time_and_materials',
    currency: p.currency || 'USD',
    budget_hours: num(p.budget_hours),
    budget_amount: num(p.budget_amount),
    hourly_rate: num(p.hourly_rate),
    retainer_fee: num(p.retainer_fee),
    retainer_hours: num(p.retainer_hours),
    retainer_rollover: p.retainer_rollover === true,
    retainer_overage_rate: num(p.retainer_overage_rate),
    retainer_carried_hours: num(p.retainer_carried_hours) ?? 0,
    retainer_next_month: p.retainer_next_month ?? null,
    retainer_last_month: p.retainer_last_month ?? null,
    start_date: p.start_date ?? null,
    due_date: p.due_date ?? null,
    assigned_to: (p.assigned_to ?? []).map(toPerson),
    tags: (p.tags ?? []).map((t) => t?.name).filter((t) => typeof t === 'string' && t),
    burn: toBurn(p.burn),
    created_at: p.created_at ?? null,
    updated_at: p.updated_at ?? null
  };
}

/** Comments, newest first, in the shape `Timeline` renders. */
export function toComments(comments: Schemas['Comment'][] | undefined) {
  return (comments ?? [])
    .map((c) => ({
      id: String(c.id),
      type: 'note',
      at: c.commented_on,
      by: authorText(c.commented_by?.user_details) || null,
      body: c.comment ?? ''
    }))
    .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

/** Files, newest first, each linked through the download proxy. */
export function toFiles(attachments: Schemas['Attachments'][] | undefined) {
  return (attachments ?? [])
    .map((a) => ({
      id: String(a.id),
      name: a.file_name || 'Attachment',
      at: a.created_at ?? null,
      href: attachmentHref(a.id)
    }))
    .sort((a, b) => new Date(b.at ?? 0).getTime() - new Date(a.at ?? 0).getTime());
}

/** The project list, newest first as the API orders it. */
export async function listProjects({ cookies }: { cookies: Cookies }, params?: URLSearchParams) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '50');
  let response: Schemas['ProjectListResponse'];
  try {
    response = await apiRequest(`/projects/?${query}`, {}, { cookies });
  } catch (err) {
    rethrowModuleOff(err);
    throw err;
  }
  const results: ReturnType<typeof toRow>[] = (response?.projects ?? []).map(toRow);
  return { results, totals: { count: response?.count ?? results.length } };
}

/**
 * Projects for a picker (the task form's parent): id and name, the first 200
 * the caller may see. A failure answers [] so the form still renders.
 */
export async function projectChoices(event: {
  cookies: Cookies;
}): Promise<{ id: string; name: string }[]> {
  try {
    const { results } = await listProjects(event, new URLSearchParams({ limit: '200' }));
    return results.map((p) => ({ id: p.id, name: p.name }));
  } catch {
    return [];
  }
}

/**
 * One project as a picker option, for a task form that arrives with it chosen
 * when the picker's first 200 do not hold it. Null when the caller cannot see
 * it, so the form does not offer a project the API would refuse.
 */
export async function projectChoice(
  { cookies }: { cookies: Cookies },
  id: string
): Promise<{ id: string; name: string } | null> {
  if (!isUuid(id)) return null;
  try {
    const raw: Schemas['ProjectDetail'] | null = await apiRequest(
      `/projects/${id}/`,
      { quietErrors: true, expectedStatuses: [404] },
      { cookies }
    );
    return raw?.id ? { id: raw.id, name: raw.name ?? '' } : null;
  } catch {
    return null;
  }
}

/**
 * What the project form's pickers offer: the accounts and active members the
 * caller may pick, and the deals while Pipeline is on (null otherwise). Each
 * list fails to empty; `peopleFailed` says the team did not load, so the form
 * leaves the people and rates alone rather than posting an empty list.
 */
export async function getProjectFormOptions(
  event: { cookies: Cookies },
  { dealsOn }: { dealsOn: boolean }
) {
  const [accounts, team, deals] = await Promise.all([
    listAccountsPicker(event)
      .then((r) => r.accounts)
      .catch(() => []),
    getOrgPeopleAndTeams(event.cookies),
    dealsOn
      ? listDeals(event, new URLSearchParams({ limit: '100' }))
          .then((r) => r.results.map((d) => ({ id: d.id, name: d.name })))
          .catch(() => [])
      : null
  ]);
  return {
    accounts,
    people: team.people.map((p) => ({ id: p.id, name: p.name })),
    peopleFailed: team.options_failed,
    deals
  };
}

/**
 * One project. A project the caller may not see answers 404, like one that
 * does not exist, so the copy covers both.
 */
export async function getProject({ cookies }: { cookies: Cookies }, id: string) {
  let raw: Schemas['ProjectDetail'];
  try {
    raw = await apiRequest(`/projects/${id}/`, {}, { cookies });
  } catch (err) {
    rethrowModuleOff(err);
    if (apiStatus(err) === 404) {
      error(404, 'That project does not exist, or you do not have access to it.');
    }
    throw err;
  }
  return {
    project: {
      ...toRow(raw),
      description: raw.description ?? '',
      custom_fields: raw.custom_fields ?? {},
      estimate: raw.estimate
        ? { id: raw.estimate.id, estimate_number: raw.estimate.estimate_number ?? '' }
        : null,
      rates: (raw.rates ?? []).map((r) => ({
        profile: toPerson(r.profile),
        hourly_rate: num(r.hourly_rate)
      })),
      invoices: (raw.invoices ?? []).map((inv) => ({
        id: inv.id,
        invoice_number: inv.invoice_number ?? '',
        status: inv.status ?? '',
        total_amount: num(inv.total_amount) ?? 0,
        currency: inv.currency || raw.currency || 'USD',
        issue_date: inv.issue_date ?? null
      }))
    },
    retainerUsage: toRetainerUsage(raw.retainer_usage),
    comments: toComments(raw.comments),
    files: toFiles(raw.attachments),
    canEdit: raw.can_edit === true,
    canDelete: raw.can_delete === true,
    canBill: raw.can_bill === true
  };
}

/** @param body from `projectBody` */
export async function createProject(
  { cookies }: { cookies: Cookies },
  body: FormBody<Schemas['ProjectBodyRequest']>
): Promise<{ id: string | null }> {
  const response: Schemas['ProjectSaved'] = await apiRequest(
    '/projects/',
    { method: 'POST', body },
    { cookies }
  );
  return { id: response?.id ?? response?.project?.id ?? null };
}

/**
 * PATCH: only the keys in `body` change, and `rates`, when sent, replaces the
 * whole list.
 *
 * @param body from `projectBody`
 */
export async function updateProject(
  { cookies }: { cookies: Cookies },
  id: string,
  body: FormBody<Schemas['ProjectBodyRequest']>
): Promise<Schemas['ProjectSaved']> {
  return await apiRequest(`/projects/${id}/`, { method: 'PATCH', body }, { cookies });
}

/**
 * Admins and the creator. The project's time, invoices and tasks stay, with
 * their link to it cleared.
 *
 * @returns the API answers 204
 */
export async function deleteProject({ cookies }: { cookies: Cookies }, id: string): Promise<null> {
  return await apiRequest(`/projects/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * A comment, a file, or both, posted the way a deal takes them. The author is
 * the caller, set by the API.
 */
export async function addProjectNote(
  { cookies }: { cookies: Cookies },
  id: string,
  comment: string,
  file: File | null = null
): Promise<Schemas['ProjectDetail']> {
  if (file) {
    const form = new FormData();
    if (comment) form.set('comment', comment);
    form.set('project_attachment', file);
    return await apiRequest(`/projects/${id}/`, { method: 'POST', body: form }, { cookies });
  }
  return await apiRequest(`/projects/${id}/`, { method: 'POST', body: { comment } }, { cookies });
}

/**
 * The new-project form's draft from a deal, nothing saved. Null when the
 * caller cannot see the deal (404) or the API has no such route, so the form
 * opens empty rather than failing.
 */
export async function getProjectDraft(
  { cookies }: { cookies: Cookies },
  dealId: string
): Promise<Schemas['ProjectDraft'] | null> {
  try {
    return await apiRequest(
      `/projects/from-deal/${dealId}/`,
      { quietErrors: true, expectedStatuses: [404] },
      { cookies }
    );
  } catch (err) {
    rethrowModuleOff(err);
    const status = apiStatus(err);
    if (status === 404 || status === 400) return null;
    throw err;
  }
}

/**
 * The project's time entries: everyone's for an admin, the caller's own
 * otherwise, as on a ticket. Null when Timesheet refuses or the read fails,
 * which the panel says.
 */
export async function listProjectTime(
  { cookies }: { cookies: Cookies },
  id: string
): Promise<Schemas['TimeEntry'][] | null> {
  try {
    const entries: Schemas['TimeEntry'][] = await apiRequest(
      `/projects/${id}/time-entries/`,
      {},
      { cookies }
    );
    return Array.isArray(entries) ? entries : [];
  } catch {
    return null;
  }
}

/**
 * Start the caller's timer on the project, or on one of its tasks. 409 when
 * one is already running anywhere. `billable` is the project's default, false
 * on a non-billable project, whose entries the API keeps non-billable anyway.
 *
 * @returns the started entry
 */
export async function startProjectTimer(
  { cookies }: { cookies: Cookies },
  id: string,
  { task = '', billable }: { task?: string; billable: boolean }
): Promise<Schemas['TimeEntry']> {
  const body: { billable: boolean; task?: string } = { billable };
  if (task) body.task = task;
  return await apiRequest(
    `/projects/${id}/time-entries/start/`,
    { method: 'POST', body },
    { cookies }
  );
}

/**
 * Log a stopped entry on the project. With no rate typed, the API fills in
 * the person's rate on the project, else the project's; the currency is the
 * project's, so none is sent.
 *
 * @returns the logged entry
 */
export async function logProjectTime(
  { cookies }: { cookies: Cookies },
  id: string,
  entry: {
    minutes: number | string;
    description: string;
    billable?: boolean;
    hourlyRate?: string | number | null;
    task?: string;
  }
): Promise<Schemas['TimeEntry']> {
  const body: FormBody<Schemas['ProjectTimeEntryBodyRequest']> = loggedEntryBody({
    ...entry,
    currency: null
  });
  if (entry.task) body.task = entry.task;
  return await apiRequest(`/projects/${id}/time-entries/`, { method: 'POST', body }, { cookies });
}

/**
 * What a bill up to `upTo` would take, for the Bill time panel: admins only.
 * Null on any refusal, and the panel then offers the bill without a preview.
 *
 * @param upTo YYYY-MM-DD
 */
export async function getUnbilled({ cookies }: { cookies: Cookies }, id: string, upTo: string) {
  const date = isoDateOrNull(upTo);
  const qs = date ? `?${new URLSearchParams({ up_to: date })}` : '';
  try {
    const r: Schemas['ProjectUnbilled'] = await apiRequest(
      `/projects/${id}/unbilled/${qs}`,
      {},
      { cookies }
    );
    return {
      entry_count: Number(r?.entry_count) || 0,
      hours: num(r?.hours) ?? 0,
      value: num(r?.value) ?? 0,
      // Billable entries with no rate, which bill at 0; 0 from an older API.
      unrated_count: Number(r?.unrated_count) || 0,
      currency: r?.currency || null
    };
  } catch {
    return null;
  }
}

/**
 * Bill every stopped, billable, unbilled entry on the project started on or
 * before `upTo` into one Draft invoice.
 */
export async function billProjectTime(
  event: { cookies: Cookies },
  id: string,
  { upTo, groupBy }: { upTo?: string; groupBy?: string }
) {
  const body: Schemas['InvoiceFromTimeEntriesRequestRequest'] = {
    project_id: id,
    group_by: billGrouping(groupBy)
  };
  const date = isoDateOrNull(upTo);
  if (date) body.up_to = date;
  return await invoiceFromTimeEntries(event, body);
}

/**
 * A one-line Draft invoice for part of a fixed fee.
 *
 * @param body from `invoiceAmountBody`
 */
export async function invoiceProjectAmount(
  { cookies }: { cookies: Cookies },
  id: string,
  body: Schemas['InvoiceAmountRequest']
): Promise<Schemas['ProjectInvoiceAmount']> {
  return await apiRequest(`/projects/${id}/invoice-amount/`, { method: 'POST', body }, { cookies });
}

/**
 * Bill the retainer's month the page showed (`{ month }`) into a Draft invoice: admins only.
 * A 400 says why not.
 */
export async function billProjectRetainer(
  { cookies }: { cookies: Cookies },
  id: string,
  month: string
): Promise<Schemas['ProjectRetainerBill']> {
  const body: Schemas['ProjectRetainerBillBodyRequest'] = { month };
  return await apiRequest(`/projects/${id}/retainer/bill/`, { method: 'POST', body }, { cookies });
}
