/**
 * Time entries: the wiring behind /v2/timesheet and the ticket's time panel.
 *
 * Server-only. `GET /time-entries/timesheet/` returns the caller's own week
 * grouped into day buckets (every day present, empty or not) with week
 * totals, the billable split, and a running-timer count. The page reads it
 * verbatim as `data.week`.
 *
 * The v2 timesheet page is always "your timesheet", it has no profile
 * switcher, so this layer never passes a `profile`, which also keeps it clear
 * of the endpoint's admin-only "another profile" 403. `TimesheetView` already
 * expands `case` and `invoice` to `{id, name}` / `{id, invoice_number}` and
 * names the profile, so there is no reshaping here.
 *
 * The rest is the ticket panel's surface, ticket-scoped on the way in
 * (`/cases/<id>/time-entries/`) and entry-scoped on the way back out
 * (`/time-entries/<id>/`), which is how the API splits it. Every write lands
 * here rather than in `tickets.js` so one file holds what the app knows about
 * time entries; `stopTimer` is shared by both callers already.
 *
 * None of these check who owns an entry. The API does: it narrows a list to
 * the caller's own rows for a non-admin, and answers 403 on someone else's
 * entry. Nothing below should be read as having re-checked that.
 */
import { apiRequest } from '$lib/api-helpers.js';

/**
 * Mon..Sun ISO-week range for `date` (UTC), as YYYY-MM-DD strings.
 * @param {Date} date
 */
function isoWeekRange(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dow = (d.getUTCDay() + 6) % 7; // Mon=0
  d.setUTCDate(d.getUTCDate() - dow);
  const start = d.toISOString().slice(0, 10);
  d.setUTCDate(d.getUTCDate() + 6);
  const end = d.toISOString().slice(0, 10);
  return { start, end };
}

/**
 * The caller's timesheet for a Mon..Sun week, shaped for the page.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ start?: string, end?: string }} [range] explicit week; defaults to this ISO week
 * @returns {Promise<{ week: any }>}
 */
export async function getTimesheet({ cookies }, { start, end } = {}) {
  if (!start || !end) {
    const range = isoWeekRange(new Date());
    start = start || range.start;
    end = end || range.end;
  }
  const qs = new URLSearchParams({ start, end });
  const week = await apiRequest(`/time-entries/timesheet/?${qs.toString()}`, {}, { cookies });
  return { week };
}

/**
 * Stop a running timer.
 *
 * The id guard is not defensive noise: an empty id would POST to
 * `/time-entries//stop/`, which is a different path, and the failure would
 * read as a routing bug rather than a missing argument.
 *
 * Ownership is the backend's call. The endpoint is org-scoped and checks the
 * entry belongs to the caller, so this does not re-check it here and must not
 * be read as having done so.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} entryId
 * @returns {Promise<any>} the stopped entry
 */
export async function stopTimer({ cookies }, entryId) {
  if (!entryId) throw new Error('Which time entry? No entry id was given.');

  return await apiRequest(`/time-entries/${entryId}/stop/`, { method: 'POST' }, { cookies });
}

/** Longest manual entry the form will send, in minutes. A day of work logged
 *  in one go is already unusual; more than that is a typo, and the API has no
 *  ceiling of its own to catch it. */
const MAX_ENTRY_MINUTES = 1440;

/** The groupings `/time-entries/report/` accepts. */
export const REPORT_GROUPINGS = ['agent', 'ticket', 'account'];

/**
 * Where the time went over a window: totals by agent, ticket or account.
 *
 * `group_by` and `billable` are checked against what the API accepts rather
 * than forwarded blind. Both arrive from the query string, so a hand-edited
 * URL would otherwise turn a 400 into a 500 page; an unknown value falls back
 * to the default instead, which is what the filter bar shows anyway.
 *
 * The window is passed through as given (YYYY-MM-DD, inclusive). Left off, the
 * API answers for the last 30 days and says which days those were, so the page
 * never has to guess a range it did not choose.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ start?: string, end?: string, group_by?: string, billable?: string, account?: string, profile?: string }} [filters]
 * @returns {Promise<{ report: any }>}
 */
export async function getTimeReport({ cookies }, filters = {}) {
  const qs = new URLSearchParams();
  if (filters.start) qs.set('start', filters.start);
  if (filters.end) qs.set('end', filters.end);
  qs.set(
    'group_by',
    REPORT_GROUPINGS.includes(filters.group_by ?? '') ? String(filters.group_by) : 'agent'
  );
  if (filters.billable === 'true' || filters.billable === 'false') {
    qs.set('billable', filters.billable);
  }
  if (filters.account) qs.set('account', filters.account);
  if (filters.profile) qs.set('profile', filters.profile);

  const report = await apiRequest(`/time-entries/report/?${qs.toString()}`, {}, { cookies });
  return { report };
}

/**
 * Every time entry on one ticket, newest first.
 *
 * Returns the array as given. An agent sees only their own rows here and an
 * admin sees the team's, which is the API's decision, not this layer's, so
 * the panel must not present the total it can add up from this list as the
 * ticket's total. `time_summary` on the ticket is that number.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} ticketId
 * @returns {Promise<any[]>}
 */
export async function listTicketTime({ cookies }, ticketId) {
  if (!ticketId) throw new Error('Which ticket? No ticket id was given.');

  const entries = await apiRequest(`/cases/${ticketId}/time-entries/`, {}, { cookies });
  return Array.isArray(entries) ? entries : [];
}

/**
 * Start a running timer on a ticket.
 *
 * The API answers 409 when this person already has one running, anywhere, and
 * names the ticket it is on. That id is carried through on the thrown error so
 * the page can offer a way to it: "you have a timer running" with no way to
 * reach it is a dead end on the one screen that could fix it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} ticketId
 * @returns {Promise<any>} the started entry
 */
export async function startTicketTimer({ cookies }, ticketId) {
  if (!ticketId) throw new Error('Which ticket? No ticket id was given.');

  return await apiRequest(
    `/cases/${ticketId}/time-entries/start/`,
    { method: 'POST' },
    { cookies }
  );
}

/**
 * Log a stopped entry after the fact.
 *
 * The API wants two timestamps; the form asks for one duration, so the window
 * is built here as "the last `minutes` minutes", ending now. That is what the
 * phone does too (`mobile/lib/widgets/tickets/ticket_time_panel.dart`), and it
 * keeps the clock out of the browser's hands: a UTC instant computed on the
 * server cannot be a day out because somebody's laptop is set wrong.
 *
 * It also means an entry cannot be backdated to last Tuesday from here. That
 * is the same limit the phone has, and adding a date field would need a
 * timezone to resolve it against, which a `<input type="date">` does not send.
 *
 * `description` is required by `TimeEntryCreateSerializer` and checked here
 * too, so an empty box is answered without a round trip. `currency` is the
 * org's, passed in by the caller: left off, every entry would be USD and an
 * org that invoices in euros could not bill a single one of them, since the
 * invoice builder refuses a mixed-currency set.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} ticketId
 * @param {{ minutes: number|string, description: string, billable?: boolean, hourlyRate?: string|number|null, currency?: string|null }} entry
 * @returns {Promise<any>} the created entry
 */
export async function logTicketTime({ cookies }, ticketId, entry) {
  if (!ticketId) throw new Error('Which ticket? No ticket id was given.');

  const minutes = Number(entry.minutes);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > MAX_ENTRY_MINUTES) {
    throw new Error(`Minutes must be a whole number between 1 and ${MAX_ENTRY_MINUTES}.`);
  }
  const description = (entry.description ?? '').trim();
  if (!description) throw new Error('Say what the time was spent on.');

  const ended = new Date();
  const started = new Date(ended.getTime() - minutes * 60000);

  /** @type {Record<string, unknown>} */
  const body = {
    started_at: started.toISOString(),
    ended_at: ended.toISOString(),
    description,
    billable: Boolean(entry.billable)
  };

  // Optional, and only when it is a number the API will take. A rate is the
  // difference between an invoice line worth something and one worth 0.00.
  if (entry.hourlyRate !== null && entry.hourlyRate !== undefined && entry.hourlyRate !== '') {
    const rate = Number(entry.hourlyRate);
    if (!Number.isFinite(rate) || rate < 0) throw new Error('The rate must be a positive number.');
    body.hourly_rate = rate.toFixed(2);
  }
  if (entry.currency) body.currency = entry.currency;

  return await apiRequest(
    `/cases/${ticketId}/time-entries/`,
    { method: 'POST', body },
    { cookies }
  );
}

/**
 * Flip one entry between billable and not.
 *
 * A PUT carrying the single field. The endpoint updates partially, so the
 * description, the window, and the rate are left where they are; sending the
 * whole entry back would race anyone editing it elsewhere.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} entryId
 * @param {boolean} billable
 * @returns {Promise<any>} the updated entry
 */
export async function setEntryBillable({ cookies }, entryId, billable) {
  if (!entryId) throw new Error('Which time entry? No entry id was given.');

  return await apiRequest(
    `/time-entries/${entryId}/`,
    { method: 'PUT', body: { billable: Boolean(billable) } },
    { cookies }
  );
}

/**
 * Delete an entry.
 *
 * The API refuses one that has been invoiced, which is the check that matters
 * and is not repeated here: an entry can be invoiced between this page
 * rendering and the button being pressed, so the answer has to come from the
 * server either way.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} entryId
 * @returns {Promise<null>}
 */
export async function deleteEntry({ cookies }, entryId) {
  if (!entryId) throw new Error('Which time entry? No entry id was given.');

  return await apiRequest(`/time-entries/${entryId}/`, { method: 'DELETE' }, { cookies });
}
