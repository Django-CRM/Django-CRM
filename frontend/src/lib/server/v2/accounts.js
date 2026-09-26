/**
 * Accounts: the third v2 module wired to the real API.
 *
 * Same rules as `leads.js` and `deals.js`: this file lives under `$lib/server`
 * because SvelteKit refuses to bundle that directory into client code and the
 * access token is an httpOnly cookie. The org is never a parameter; it is a
 * claim inside the JWT and the backend reads it from there.
 *
 * WHY THIS MODULE, AND WHY NOW
 * `/pipeline` went live linking each deal to `/accounts/{uuid}`, and
 * `/accounts` was still fixtures keyed by slugs like `acc-northwind`. So
 * every account link on a live page answered 404. A module is not finished
 * when its own pages work; it is finished when the pages it points at do.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * - `renewal_note` ("Renews every August", "Prospect, no contract yet") is
 *   gone. There is no contract, subscription or renewal model anywhere in the
 *   backend, and nothing to derive one from. A renewal date is the kind of
 *   invention somebody plans a quarter around.
 * - `customer_since` is gone as a stored fact and comes back as `first_won_on`
 *: the close date of the first deal won against this company, which the CRM
 *   does know. The page still says "customer since"; the field name says where
 *   the date came from.
 * - `billing_city` / `billing_country` are `city` / `country_display`. Account
 *   has one flat address, not a billing and a shipping one.
 * - `employees` is `number_of_employees`.
 * - A contact's `relationship` ("Champion", "Blocker") is gone for the third
 *   time; Contact has `title` and `department`.
 *
 * WHAT THE FIXTURE GOT RIGHT
 * The mock refused to store `lifetime_value` / `open_pipeline` /
 * `overdue_amount` and derived them from the records instead, with a comment
 * saying the real API had to aggregate them. It does now: the counts in
 * `accounts.views.annotate_rollups`, the money in `attach_money_rollups`.
 *
 * MONEY IS PER CURRENCY
 * Deals and invoices each carry a currency and there are no exchange rates, so
 * the API never adds two currencies together: the money arrives as
 * `rollups.by_currency`. It becomes `won_by_currency`, `pipeline_by_currency`
 * and `overdue_by_currency` here, each a list of `{currency, amount}` holding
 * only the currencies with something in them, so an empty list means nothing.
 *
 * NUMBERS ARRIVE AS STRINGS
 * DRF renders `DecimalField` as a string. The rollups come through the JSON
 * encoder as numbers, the model fields as strings, and the pages add them
 * together, so everything numeric is coerced here, at the boundary.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';

/** DRF decimals are strings. `null` stays `null`. It means "not recorded". */
function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Invoice statuses where the money is still owed.
 *
 * Mirrors `invoices.models.UNPAID_STATUSES`, which the account rollup uses on
 * the server. It is repeated here so the per-row "past due" marker on the
 * invoice rail is decided by the same rule as the total printed above it.
 * The API's own `is_overdue` counts a *draft* past its date, which the header
 * deliberately does not, and a rail that disagrees with its own header is how
 * people stop believing either number.
 */
const UNPAID_STATUSES = ['Sent', 'Viewed', 'Partially_Paid', 'Overdue'];

/**
 * Whether this invoice is part of the figure in the header.
 *
 * @param {any} invoice
 */
function isPastDue(invoice) {
  if (!UNPAID_STATUSES.includes(invoice.status)) return false;
  if (!invoice.due_date) return false;
  if ((num(invoice.amount_due) ?? 0) <= 0) return false;
  return new Date(invoice.due_date) < new Date(new Date().toDateString());
}

/**
 * `INDCHOICES` stores the label in the value, "FOOD & BEVERAGE", shouting.
 * Only the presentation changes; the value that round-trips to the API is
 * untouched.
 *
 * @param {string | null} industry
 */
function industryLabel(industry) {
  if (!industry) return '';
  return industry
    .toLowerCase()
    .split(' ')
    .map((word) => (word === '&' ? '&' : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(' ');
}

/**
 * The aggregates, or nulls.
 *
 * `rollups` is null when the serializer was used somewhere that did not
 * annotate. Nulls travel through rather than becoming zeroes: "we did not
 * count" and "we counted nothing" are different sentences and the page prints
 * them differently.
 *
 * @param {any} rollups
 */
function toRollups(rollups) {
  if (!rollups) {
    return {
      won_by_currency: null,
      won_count: null,
      pipeline_by_currency: null,
      open_deal_count: null,
      overdue_by_currency: null,
      open_tickets: null,
      first_won_on: null
    };
  }
  /** @param {string} field */
  const perCurrency = (field) =>
    (rollups.by_currency ?? [])
      .map((/** @type {any} */ r) => ({ currency: r.currency, amount: num(r[field]) ?? 0 }))
      .filter((/** @type {any} */ m) => m.amount !== 0);
  return {
    won_by_currency: perCurrency('won_amount'),
    won_count: rollups.won_count ?? 0,
    pipeline_by_currency: perCurrency('open_pipeline'),
    open_deal_count: rollups.open_deal_count ?? 0,
    overdue_by_currency: perCurrency('overdue_amount'),
    open_tickets: rollups.open_tickets ?? 0,
    first_won_on: rollups.first_won_on ?? null
  };
}

/**
 * The subset of a Django account the v2 pages read.
 *
 * @param {any} account
 */
function toRow(account) {
  return {
    id: account.id,
    name: account.name ?? '',
    industry: industryLabel(account.industry),
    industry_value: account.industry ?? '',
    website: account.website ?? '',
    email: account.email ?? '',
    phone: account.phone ?? '',
    number_of_employees: account.number_of_employees ?? null,
    annual_revenue: num(account.annual_revenue),
    currency: account.currency || 'USD',
    city: account.city ?? '',
    country: account.country ?? '',
    country_display: account.country_display ?? '',
    description: account.description ?? '',
    is_active: account.is_active !== false,
    created_at: account.created_at,
    ...toRollups(account.rollups)
  };
}

/**
 * Every filter param `listAccounts` will forward. See the note on
 * FILTER_FIELDS in tickets.js: the descriptor in `$lib/v2/filters.js` must not
 * name a key absent from this list, and `filters.test.js` enforces it.
 *
 * `industry` and `city` are `icontains` matches server-side, and `tags` and
 * `assigned_to` are `getlist` (`AccountsListView.get_context_data`,
 * `backend/accounts/views.py:213-226`), so more than one id can be sent for
 * either.
 */
export const FILTER_FIELDS = ['assigned_to', 'tags', 'industry', 'city'];

/**
 * Accounts in this org, largest won first (within each currency; see below).
 *
 * `active_accounts.open_accounts_count` is the size of the whole filtered
 * queryset; `open_accounts` is one page of it. `count` below reads the
 * former, so it does not under-report the way a header that counted only the
 * loaded page would.
 *
 * There is no `totals` envelope to prefer over this derivation here, unlike
 * `invoices.js` and `tasks.js`: `AccountsListView.get_context_data`
 * (`backend/accounts/views.py`) never puts a `totals` key on the response, so
 * a `response.totals ?? {...}` fallback would always fall through to the
 * `{...}` side, dead code pretending at a capability the API does not have.
 * `customers` is still computed from `rows`, the one page actually loaded
 * (`limit` defaults to 25), so it DOES under-report whenever the filtered set
 * is bigger than that page. Fixing that needs a real aggregate from the API,
 * which is not there to read yet.
 *
 * The API narrows this list for non-admins to accounts they created or are
 * assigned to, so what comes back is already what this person may see.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listAccounts({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response = await apiRequest(`/accounts/?${query}`, {}, { cookies });
  const active = response.active_accounts ?? {};
  const rows = (active.open_accounts ?? []).map(toRow);
  // Largest won first within a currency, as amounts only compare there; the
  // API orders `by_currency` by code, so an account is placed by its first.
  rows.sort((a, b) => {
    const x = a.won_by_currency?.[0];
    const y = b.won_by_currency?.[0];
    if (!x || !y) return (y ? 1 : 0) - (x ? 1 : 0);
    return x.currency === y.currency ? y.amount - x.amount : x.currency.localeCompare(y.currency);
  });

  const inactive = response.closed_accounts ?? {};

  return {
    results: rows,
    totals: {
      count: active.open_accounts_count ?? rows.length,
      // Every account with a deal won against it. This is the only definition
      // of "customer" the data supports; there is no customer flag.
      customers: rows.filter((row) => (row.won_count ?? 0) > 0).length,
      inactive: inactive.close_accounts_count ?? 0,
      shown: rows.length
    }
  };
}

/**
 * Accounts as a flat `{id, name}` picker list, open accounts only.
 *
 * Same `/accounts/?limit=200` shape `contacts.js:342` already fetches for its
 * own account picker, reused here rather than respelled a third time for the
 * invoices and estimates filter bars, which both need an Account field.
 *
 * The 200 ceiling is real: an org with more open accounts than that gets a
 * truncated picker, the same limit `contacts.js` already lives with.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ accounts: {id: string, name: string}[] }>}
 */
export async function listAccountsPicker({ cookies }) {
  const response = await apiRequest('/accounts/?limit=200', {}, { cookies });
  const active = response.active_accounts ?? {};
  return {
    accounts: (active.open_accounts ?? []).map((/** @type {any} */ account) => ({
      id: account.id,
      name: account.name ?? ''
    }))
  };
}

/**
 * One account and everything hanging off it.
 *
 * All of it arrives in a single request. `AccountDetailView.get` already
 * returns the deals, people, tickets and invoices for the account, so unlike
 * the pipeline's "attached" rail this panel costs nothing extra. It was the
 * response all along and the fixture page was reimplementing it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getAccount({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  const account = toRow(response.account_obj);

  return {
    account,
    owners: (response.account_obj.assigned_to ?? []).map(profileName),
    tags: (response.account_obj.tags ?? []).map((/** @type {any} */ tag) => tag.name),
    deals: (response.opportunity_list ?? []).map((/** @type {any} */ deal) => ({
      id: deal.id,
      name: deal.name ?? '',
      stage: deal.stage,
      // Named and classified by the deal's own pipeline, never by its code.
      stage_label: deal.stage_label ?? deal.stage,
      stage_kind: deal.stage_kind ?? null,
      amount: num(deal.amount) ?? 0,
      // Each deal is priced in its own currency, as the account's rollups
      // above are. Dropping this printed every deal on the page as dollars.
      currency: deal.currency || 'USD',
      closed_on: deal.closed_on ?? null,
      // Real fields, from the stage-aging chain the deal module already owns.
      days_in_current_stage: deal.days_in_stage ?? 0,
      aging_status: deal.aging_status ?? 'green'
    })),
    contacts: (response.contacts ?? []).map((/** @type {any} */ contact) => ({
      id: contact.id,
      first_name: contact.first_name ?? '',
      last_name: contact.last_name ?? '',
      title: contact.title ?? '',
      department: contact.department ?? '',
      email: contact.email ?? '',
      phone: contact.phone ?? '',
      do_not_call: Boolean(contact.do_not_call)
    })),
    tickets: (response.cases ?? []).map((/** @type {any} */ ticket) => ({
      id: ticket.id,
      name: ticket.name ?? '',
      status: ticket.status,
      priority: ticket.priority
    })),
    invoices: (response.invoices ?? []).map((/** @type {any} */ invoice) => ({
      id: invoice.id,
      invoice_number: invoice.invoice_number ?? '',
      status: invoice.status,
      total_amount: num(invoice.total_amount) ?? 0,
      amount_due: num(invoice.amount_due) ?? 0,
      currency: invoice.currency || 'USD',
      due_date: invoice.due_date ?? null,
      past_due: isPastDue(invoice)
    })),
    activity: (response.comments ?? []).map((/** @type {any} */ comment) => ({
      id: comment.id,
      at: comment.commented_on,
      by: comment.commented_by_user?.email || comment.commented_by?.user_details?.email || null,
      body: comment.comment
    }))
  };
}

/**
 * @param {any} profile
 */
function profileName(profile) {
  return profile?.user_details?.email || profile?.user?.email || 'Unknown';
}

/** Scalar fields the account forms own. Everything else is server-derived. */
export const EDITABLE_FIELDS = [
  'name',
  'industry',
  'website',
  'email',
  'phone',
  'number_of_employees',
  'annual_revenue',
  'address_line',
  'city',
  'state',
  'postcode',
  'country',
  'description'
];

/**
 * The choice lists the forms need, taken from the API rather than hardcoded.
 *
 * `industries` and `countries` come back as `[value, label]` pairs on every
 * accounts list response, the same lists the model validates against, so a
 * select built from them cannot offer a value the serializer will reject.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listChoices(cookies) {
  const response = await apiRequest('/accounts/?limit=1', {}, { cookies });
  return {
    industries: (response.industries ?? []).map((/** @type {any} */ pair) => ({
      value: pair[0],
      label: industryLabel(pair[0])
    })),
    countries: (response.countries ?? []).map((/** @type {any} */ pair) => ({
      value: pair[0],
      label: pair[1]
    })),
    owners: (response.users ?? []).map((/** @type {any} */ user) => ({
      id: user.id,
      name: user.user__email
    }))
  };
}

/**
 * Everything the create form needs, in one call.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getAccountFormOptions({ cookies }) {
  const choices = await listChoices(cookies);
  return { ...choices, defaults: { country: '', industry: '' } };
}

/**
 * The account plus what the edit form needs in order to explain itself.
 *
 * `form` holds only `EDITABLE_FIELDS`, so no input can bind to a
 * server-derived field by accident.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getAccountForEdit({ cookies }, id) {
  const [response, choices] = await Promise.all([fetchDetail(cookies, id), listChoices(cookies)]);
  const raw = response.account_obj;
  const account = toRow(raw);

  return {
    account,
    ...choices,
    form: {
      name: account.name,
      industry: account.industry_value,
      website: account.website,
      email: account.email,
      phone: account.phone,
      number_of_employees: account.number_of_employees ?? '',
      annual_revenue: account.annual_revenue ?? '',
      address_line: raw.address_line ?? '',
      city: account.city,
      state: raw.state ?? '',
      postcode: raw.postcode ?? '',
      country: account.country,
      description: account.description,
      // Binds to a Profile id, not a display name.
      assigned_to: raw.assigned_to?.[0]?.id ?? ''
    },
    server: {
      // Read-only context so the form can explain what it is not editing.
      contact_count: (response.contacts ?? []).length,
      deal_count: (response.opportunity_list ?? []).length,
      invoice_count: (response.invoices ?? []).length,
      owner_count: (raw.assigned_to ?? []).length,
      team_count: (raw.teams ?? []).length,
      tag_count: (raw.tags ?? []).length
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
  if ('number_of_employees' in body && body.number_of_employees !== null) {
    body.number_of_employees = Number(body.number_of_employees);
  }
  if ('annual_revenue' in body && body.annual_revenue !== null) {
    body.annual_revenue = Number(body.annual_revenue);
  }
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
 * PATCH, not PUT, and here the reason is louder than it was for leads or
 * deals: `AccountDetailView.put` runs `contacts.clear()`, `tags.clear()`,
 * `teams.clear()` and `assigned_to.clear()` unconditionally before re-adding
 * whatever the body carried. This form owns thirteen scalar fields and one
 * owner, so a single PUT would strip every contact off the account, the
 * people, on the record whose entire purpose is the people. `patch` guards
 * each relation with `if "<field>" in data`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateAccount({ cookies }, id, values) {
  return await apiRequest(
    `/accounts/${id}/`,
    { method: 'PATCH', body: toBody(values) },
    { cookies }
  );
}

/**
 * Create an account.
 *
 * No `org` and no `created_by` in the body: `AccountsListView.post` sets both
 * from `request.profile`, and `AccountCreateSerializer` does not list either
 * as a field.
 *
 * The response now carries the new `id`, which it did not before this change.
 * Without it a client cannot open what it just made, and searching by name is
 * a race dressed up as a lookup.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export async function createAccount({ cookies }, values) {
  return await apiRequest('/accounts/', { method: 'POST', body: toBody(values) }, { cookies });
}

/**
 * `AccountDetailView.get` answers 404 for another org's account. Deliberately
 * not 403, which would confirm the id exists, and 403 for an account inside
 * the org that this profile neither created nor is assigned to.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} id
 */
async function fetchDetail(cookies, id) {
  try {
    return await apiRequest(`/accounts/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    // On the status, not on the wording. Django answers a missing record with
    // "No Account matches the given query.", no "404" in it anywhere.
    if (err?.status === 404) {
      error(404, 'That account does not exist, or it belongs to another team.');
    }
    if (err?.status === 403) {
      error(403, 'This account belongs to somebody else. Ask an admin if you need it.');
    }
    throw err;
  }
}
