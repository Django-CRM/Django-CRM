/**
 * What each list page can be filtered by, and how those filters are drawn.
 *
 * Client-safe on purpose: `.svelte` files import this, and SvelteKit hard-blocks
 * importing `$lib/server/` into a component. The server-side counterpart is
 * `$lib/server/v2/filter-params.js`.
 *
 * Filter state lives in the URL and nowhere else, so a filtered list is
 * shareable and the back button works without any state to synchronise.
 *
 * A descriptor has two parts:
 *
 * - `presets`: named views. Each writes the params the page's `load` ALREADY
 *   reads. Never invent a new spelling: tickets and tasks use `all=1`, not
 *   `all=true`, and a preset writing the wrong one changes the URL and shows
 *   the same rows, which is the failure this whole phase exists to remove.
 * - `fields`: what `+ Filter` offers. Each becomes one removable chip.
 *
 * A `date-range` field names BOTH emitted params explicitly, because the
 * convention is not uniform: invoices uses `due_date_gte` (one underscore)
 * while every other endpoint uses `due_date__gte` (two). Never build a date key
 * by concatenation. A `number-range` field (`amount` on pipeline) is the same
 * shape, two named params and no derived key, only the value it validates is
 * numeric rather than an ISO date.
 */
import {
  CASE_PRIORITIES,
  CASE_TYPES,
  DOCUMENT_STATUSES,
  ESTIMATE_STATUSES,
  INDUSTRIES,
  INVOICE_STATUSES,
  LEAD_LIST_STATUSES,
  LEAD_STATUS_LABEL,
  LEAD_SOURCES,
  LEAD_SOURCE_LABEL,
  SOLUTION_STATUS,
  SOLUTION_STATUS_LABEL,
  STAGES,
  STAGE_LABEL,
  TASK_PRIORITY,
  TASK_STATUS,
  industryLabel,
  invoiceStatusLabel
} from './enums.js';

/** @typedef {{ key: string, label: string, params: Record<string, string> }} Preset */
/** @typedef {{ key: string, label: string, type: string, options?: string[], labelFor?: (v: string) => string, gteKey?: string, lteKey?: string }} Field */
/** @typedef {{ presets: Preset[], fields: Field[] }} Descriptor */

/** @type {Record<string, Descriptor>} */
export const FILTERS = {
  tickets: {
    presets: [
      { key: 'open', label: 'Open, newest first', params: {} },
      { key: 'mine', label: 'Mine', params: { assigned_to: '@me' } },
      { key: 'breaching', label: 'Breaching SLA', params: { sla_breached: 'true' } },
      { key: 'all', label: 'Everything', params: { all: '1' } }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      { key: 'priority', label: 'Priority', type: 'select', options: CASE_PRIORITIES },
      { key: 'case_type', label: 'Type', type: 'select', options: CASE_TYPES },
      { key: 'sla_breached', label: 'Breaching SLA', type: 'boolean' },
      { key: 'tags', label: 'Tag', type: 'tag' }
    ]
  },

  leads: {
    presets: [
      { key: 'open', label: 'Open leads', params: {} },
      { key: 'mine', label: 'Mine', params: { assigned_to: '@me' } }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      {
        key: 'status',
        label: 'Status',
        type: 'select',
        options: LEAD_LIST_STATUSES,
        labelFor: (v) => LEAD_STATUS_LABEL[v] ?? v
      },
      {
        key: 'source',
        label: 'Source',
        type: 'select',
        options: LEAD_SOURCES,
        labelFor: (v) => LEAD_SOURCE_LABEL[v] ?? v
      },
      { key: 'tags', label: 'Tag', type: 'tag' }
    ]
  },

  contacts: {
    presets: [
      { key: 'mine', label: 'Mine', params: { assigned_to: '@me' } },
      { key: 'inactive', label: 'Including inactive', params: { inactive: '1' } },
      { key: 'active', label: 'Active contacts', params: {} }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      { key: 'tags', label: 'Tag', type: 'tag' },
      { key: 'city', label: 'City', type: 'text' }
    ]
  },

  pipeline: {
    presets: [
      { key: 'open', label: 'Open deals', params: { open: 'true' } },
      { key: 'mine', label: 'Mine', params: { assigned_to: '@me' } },
      { key: 'stalled', label: 'Stalled', params: { rotten: 'true' } },
      { key: 'all', label: 'All deals', params: {} }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      {
        key: 'stage',
        label: 'Stage',
        type: 'select',
        options: STAGES,
        labelFor: (v) => STAGE_LABEL[v] ?? v
      },
      {
        key: 'lead_source',
        label: 'Source',
        type: 'select',
        options: LEAD_SOURCES,
        labelFor: (v) => LEAD_SOURCE_LABEL[v] ?? v
      },
      {
        key: 'amount',
        label: 'Value',
        type: 'number-range',
        gteKey: 'amount__gte',
        lteKey: 'amount__lte'
      },
      { key: 'tags', label: 'Tag', type: 'tag' }
    ]
  },

  tasks: {
    presets: [
      { key: 'open', label: 'Open tasks', params: {} },
      { key: 'mine', label: 'My tasks', params: { assigned_to: '@me' } },
      { key: 'all', label: 'All tasks', params: { all: '1' } }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      { key: 'priority', label: 'Priority', type: 'select', options: TASK_PRIORITY },
      { key: 'status', label: 'Status', type: 'select', options: TASK_STATUS },
      {
        key: 'due_date',
        label: 'Due date',
        type: 'date-range',
        gteKey: 'due_date__gte',
        lteKey: 'due_date__lte'
      }
    ]
  },

  accounts: {
    presets: [
      { key: 'mine', label: 'Mine', params: { assigned_to: '@me' } },
      { key: 'all', label: 'All accounts', params: {} }
    ],
    fields: [
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      { key: 'tags', label: 'Tag', type: 'tag' },
      {
        key: 'industry',
        label: 'Industry',
        type: 'select',
        options: INDUSTRIES,
        labelFor: industryLabel
      },
      { key: 'city', label: 'City', type: 'text' }
    ]
  },

  invoices: {
    presets: [
      { key: 'overdue', label: 'Overdue', params: { status: 'Overdue' } },
      { key: 'draft', label: 'Draft', params: { status: 'Draft' } },
      { key: 'all', label: 'All invoices', params: {} }
    ],
    fields: [
      {
        key: 'status',
        label: 'Status',
        type: 'select',
        options: INVOICE_STATUSES,
        labelFor: invoiceStatusLabel
      },
      { key: 'account', label: 'Account', type: 'account' },
      { key: 'assigned_to', label: 'Owner', type: 'person' },
      // Invoices uses a SINGLE underscore (`due_date_gte`/`due_date_lte`,
      // `backend/invoices/api_views.py:149-152`). Every other date-range field
      // in this file uses a DOUBLE underscore. Never build this key by
      // concatenation; a shared helper that did would silently return an
      // unfiltered list here while working everywhere else.
      {
        key: 'due_date',
        label: 'Due date',
        type: 'date-range',
        gteKey: 'due_date_gte',
        lteKey: 'due_date_lte'
      }
    ]
  },

  estimates: {
    presets: [
      { key: 'accepted', label: 'Accepted', params: { status: 'Accepted' } },
      { key: 'all', label: 'All estimates', params: {} }
    ],
    fields: [
      { key: 'status', label: 'Status', type: 'select', options: ESTIMATE_STATUSES },
      { key: 'account', label: 'Account', type: 'account' }
      // No Owner field: `EstimateListView.get` (backend/invoices/api_views.py
      // :1028-1036) reads only `status` and `account`, never `assigned_to`.
      // Offering one here would draw a chip that filters nothing underneath it.
    ]
  },

  solutions: {
    presets: [
      { key: 'published', label: 'Published', params: { visibility: 'published' } },
      { key: 'drafts', label: 'Drafts', params: { status: 'draft' } },
      { key: 'all', label: 'All articles, last edited first', params: {} }
    ],
    fields: [
      {
        key: 'status',
        label: 'Status',
        type: 'select',
        options: SOLUTION_STATUS,
        labelFor: (v) => SOLUTION_STATUS_LABEL[v] ?? v
      },
      { key: 'tags', label: 'Tag', type: 'tag' }
    ]
  },

  documents: {
    // `active` is the empty-params default, and `all` opts in with
    // `archived=1`, the same shape `/contacts` uses for `inactive`. The load
    // applies `status=active` unless that opt-in or an explicit Status choice
    // is present. Written the other way round, with `all` as the default,
    // archived documents come back alongside the live ones on a bare URL,
    // which undoes the only thing archiving does.
    presets: [
      { key: 'active', label: 'Active documents', params: {} },
      { key: 'all', label: 'Including archived', params: { archived: '1' } }
    ],
    // `tags` and `created_by` are deliberately absent: `DocumentListView.get`
    // (backend/common/views/document_views.py:71-79) reads only `title`,
    // `status` and `shared_to`, `Document` has no `tags` relation at all, and
    // `shared_to` is passed straight to `json.loads`, so a plain
    // `?shared_to=<uuid>` throws `JSONDecodeError` and answers 500. Status is
    // the only filter this endpoint actually honours.
    fields: [{ key: 'status', label: 'Status', type: 'select', options: DOCUMENT_STATUSES }]
  },

  recurring: {
    presets: [
      { key: 'active', label: 'Active schedules', params: { is_active: 'true' } },
      { key: 'all', label: 'All schedules', params: {} }
    ],
    fields: [{ key: 'is_active', label: 'Active', type: 'boolean' }]
  }
};

/** Every param key a descriptor's fields can emit, date ranges expanded. */
export function fieldKeys(/** @type {Descriptor} */ descriptor) {
  const keys = [];
  for (const field of descriptor.fields) {
    if (field.type === 'date-range' || field.type === 'number-range') {
      keys.push(field.gteKey, field.lteKey);
    } else keys.push(field.key);
  }
  return /** @type {string[]} */ (keys.filter(Boolean));
}

/** The same URL with `key` removed. Returns a path plus query, never absolute. */
export function withoutParam(/** @type {URL} */ url, /** @type {string} */ key) {
  const next = new URLSearchParams(url.search);
  next.delete(key);
  const qs = next.toString();
  return qs ? `${url.pathname}?${qs}` : url.pathname;
}

/**
 * The same URL with `params` applied. A null or empty value removes the key, so
 * one helper covers both adding a filter and clearing one.
 */
export function withParams(
  /** @type {URL} */ url,
  /** @type {Record<string, string|null>} */ params
) {
  const next = new URLSearchParams(url.search);
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
  }
  const qs = next.toString();
  return qs ? `${url.pathname}?${qs}` : url.pathname;
}

/**
 * Which preset the current URL represents.
 *
 * A preset matches when every param it sets is present with that value. The
 * empty-params preset is the fallback, so it is checked last and only wins when
 * nothing else does. Presets are ordered most-specific-first in each descriptor
 * for this reason.
 *
 * `meId` is the resolved viewer's profile id. A preset param of `@me` is
 * substituted with `meId` before comparing, so `mine` only matches the
 * viewer's own id, never any other value of the same key. When `meId` is not
 * resolved (null), an `@me` param can never match: labelling an unowned or
 * someone-else's-owned view as "Mine" would assert something false about
 * what is on screen.
 */
export function activePresetKey(
  /** @type {string} */ pageKey,
  /** @type {URL} */ url,
  /** @type {string | null} */ meId = null
) {
  const descriptor = FILTERS[pageKey];
  if (!descriptor) return '';
  const withParamsSet = descriptor.presets.filter((p) => Object.keys(p.params).length > 0);
  for (const preset of withParamsSet) {
    const hit = Object.entries(preset.params).every(([k, v]) => {
      const wanted = v === '@me' ? meId : v;
      // An unresolved viewer cannot match a "@me" preset. Returning true here
      // would label any owner-filtered view as "Mine".
      return wanted !== null && url.searchParams.get(k) === wanted;
    });
    if (hit) return preset.key;
  }
  return descriptor.presets.find((p) => Object.keys(p.params).length === 0)?.key ?? '';
}

/**
 * One chip per explicitly-set filter param.
 *
 * A chip only ever represents a param that is actually in the URL, which is
 * what makes every chip removable. The old bar rendered chips describing
 * implicit defaults, and their X had nothing to remove, so it did nothing. An
 * implicit default belongs in the preset name, not in a chip.
 *
 * `lookups` resolves opaque ids to names: `{ people: [{id,name}], tags: [...],
 * accounts: [...] }`. A missing lookup falls back to the raw value rather than
 * rendering an empty chip.
 */
export function activeChips(
  /** @type {string} */ pageKey,
  /** @type {URL} */ url,
  /** @type {{people?: any[], tags?: any[], accounts?: any[]}} */ lookups = {}
) {
  const descriptor = FILTERS[pageKey];
  if (!descriptor) return [];
  const nameFrom = (/** @type {any[]} */ list, /** @type {string} */ id) =>
    list?.find((o) => o.id === id)?.name ?? id;

  const chips = [];
  for (const field of descriptor.fields) {
    if (field.type === 'date-range' || field.type === 'number-range') {
      const from = url.searchParams.get(/** @type {string} */ (field.gteKey));
      const to = url.searchParams.get(/** @type {string} */ (field.lteKey));
      if (!from && !to) continue;
      const value =
        field.type === 'number-range'
          ? from && to
            ? `${from} to ${to}`
            : from
              ? `over ${from}`
              : `under ${to}`
          : from && to
            ? `${from} to ${to}`
            : from
              ? `from ${from}`
              : `up to ${to}`;
      chips.push({
        key: field.key,
        label: field.label,
        value,
        href: withParams(url, {
          [/** @type {string} */ (field.gteKey)]: null,
          [/** @type {string} */ (field.lteKey)]: null
        })
      });
      continue;
    }

    const raw = url.searchParams.get(field.key);
    if (!raw) continue;
    let value = raw;
    if (field.type === 'person') value = nameFrom(lookups.people ?? [], raw);
    else if (field.type === 'tag') value = nameFrom(lookups.tags ?? [], raw);
    else if (field.type === 'account') value = nameFrom(lookups.accounts ?? [], raw);
    else if (field.type === 'boolean') value = raw === 'true' ? 'Yes' : 'No';
    else if (field.labelFor) value = field.labelFor(raw);

    chips.push({ key: field.key, label: field.label, value, href: withoutParam(url, field.key) });
  }
  return chips;
}
