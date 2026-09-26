/**
 * Saved views, the server half: every key a page saves is one its list really
 * forwards, and the three actions call the API with only what they should.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('$env/dynamic/public', () => ({ env: { PUBLIC_DJANGO_API_URL: 'http://api.test' } }));
const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { SAVED_VIEW_KEYS, loadSavedViews, savedViewActions, savedViewSpec } =
  await import('./saved-views.js');
const queries = await import('./list-queries.js');
const { FILTERS } = await import('$lib/v2/filters.js');

const UUID = '11111111-2222-3333-4444-555555555555';
const VIEW = '99999999-8888-7777-6666-555555555555';
const cookies = /** @type {any} */ ({ get: () => 'token' });

/** The filter-bar page each API list is drawn on. */
const PAGE = {
  leads: 'leads',
  contacts: 'contacts',
  accounts: 'accounts',
  opportunities: 'pipeline',
  cases: 'tickets',
  invoices: 'invoices'
};

/** @param {string} module */
function listQuery(module, /** @type {URL} */ url) {
  if (module === 'leads') return queries.leadListQuery(url);
  if (module === 'contacts') return queries.contactListQuery(url);
  if (module === 'accounts') return queries.accountListQuery(url);
  if (module === 'cases') return queries.ticketListQuery(url);
  if (module === 'invoices') return queries.invoiceListQuery(url);
  return queries.dealListQuery(url, [{ id: UUID }]).params;
}

/** A value the page accepts for `key`, from its filter descriptor. */
function sample(/** @type {string} */ module, /** @type {string} */ key) {
  const field = FILTERS[PAGE[module]].fields.find(
    (/** @type {any} */ f) => f.key === key || f.gteKey === key || f.lteKey === key
  );
  if (field?.type === 'select') return field.options[0];
  if (['person', 'tag', 'account'].includes(field?.type)) return UUID;
  if (field?.type === 'stage') return 'QUALIFIED';
  if (field?.type === 'date-range') return '2026-01-02';
  if (field?.type === 'number-range') return '10';
  if (field?.type === 'boolean' || key === 'open' || key === 'rotten') return 'true';
  if (key === 'pipeline') return UUID;
  if (key === 'status') return 'New';
  return 'x';
}

describe('SAVED_VIEW_KEYS', () => {
  for (const [module, keys] of Object.entries(SAVED_VIEW_KEYS)) {
    it(`${module}: every saved key is forwarded to the list under its own name`, () => {
      for (const key of keys) {
        const value = sample(module, key);
        const url = new URL(`http://app.test/x?${new URLSearchParams({ [key]: value })}`);
        expect(listQuery(module, url).getAll(key), key).toContain(value);
      }
    });
  }

  it('every multi key is forwarded with all its values', () => {
    const second = { status: { leads: 'in process', cases: 'Pending' } };
    for (const module of Object.keys(SAVED_VIEW_KEYS)) {
      const spec = savedViewSpec(/** @type {any} */ (module), new URLSearchParams());
      for (const key of spec.multi ?? []) {
        const values =
          key === 'status'
            ? [sample(module, key), second.status[/** @type {'leads'|'cases'} */ (module)]]
            : [UUID, UUID.replace('1', '9')];
        const query = new URLSearchParams();
        for (const v of values) query.append(key, v);
        expect(
          listQuery(module, new URL(`http://app.test/x?${query}`)).getAll(key),
          `${module} ${key}`
        ).toEqual(values);
      }
    }
  });

  it('never saves paging or the page presets', () => {
    for (const keys of Object.values(SAVED_VIEW_KEYS)) {
      for (const key of ['limit', 'offset', 'inactive', 'all', 'view']) {
        expect(keys).not.toContain(key);
      }
    }
  });
});

describe('loadSavedViews', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  const url = new URL('http://app.test/tickets');

  it("asks for one list's views and hands back the page's spec", async () => {
    apiRequest.mockResolvedValue({ saved_views: [{ id: VIEW }], limit: 25 });
    const saved = await loadSavedViews({ cookies, url }, 'cases');
    expect(apiRequest.mock.calls[0][0]).toBe('/saved-views/?module=cases');
    expect(saved).toEqual({
      spec: savedViewSpec('cases', url.searchParams),
      views: [{ id: VIEW }],
      limit: 25
    });
  });

  it('costs the menu, not the page, when the fetch fails', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('down'), { status: 500 }));
    expect((await loadSavedViews({ cookies, url }, 'leads')).views).toEqual([]);
  });
});

describe('savedViewSpec', () => {
  it('reads multi off the filter descriptor', () => {
    const spec = savedViewSpec('leads', new URLSearchParams());
    expect(spec.multi).toEqual(expect.arrayContaining(['assigned_to', 'status', 'tags']));
    expect(spec.multi).not.toContain('source');
    expect(savedViewSpec('invoices', new URLSearchParams()).multi).toEqual([]);
  });

  it('tickets: the open queue is explicit and All is its own flag', () => {
    expect(savedViewSpec('cases', new URLSearchParams()).implicit).toEqual({
      key: 'status',
      values: ['New', 'Assigned', 'Pending'],
      allParam: 'all'
    });
  });

  it('the board saves and applies only what the board runs, and stays the board', () => {
    const list = savedViewSpec('opportunities', new URLSearchParams());
    const board = savedViewSpec('opportunities', new URLSearchParams('view=board'));
    expect(list.keys).toContain('stage');
    expect(board.keys).not.toContain('stage');
    expect(board.keys).toEqual(expect.arrayContaining(['assigned_to', 'tags', 'search']));
    expect(board.keep).toEqual(['view']);
  });
});

/** A form action event posting `fields` from `/leads`. */
function post(/** @type {Record<string, string>} */ fields) {
  const body = new FormData();
  for (const [k, v] of Object.entries(fields)) body.set(k, v);
  return /** @type {any} */ ({
    request: new Request('http://app.test/leads?/saveView', { method: 'POST', body }),
    cookies,
    url: new URL('http://app.test/leads?/saveView')
  });
}

/** Run an action and return the redirect it throws, or its result. */
async function outcome(/** @type {Promise<any>} */ promise) {
  try {
    return await promise;
  } catch (thrown) {
    return thrown;
  }
}

describe('savedViewActions', () => {
  const actions = savedViewActions('leads');
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('saves only the page filters, server-set fields never sent, then returns to the page', async () => {
    apiRequest.mockResolvedValue({ id: VIEW });
    const result = await outcome(
      actions.saveView(
        post({
          name: ' Hot ',
          query: `status=assigned&tags=${UUID}&limit=25&org=evil&next=https://evil.test`
        })
      )
    );
    const [path, options] = apiRequest.mock.calls[0];
    expect(path).toBe('/saved-views/');
    expect(options).toEqual({
      method: 'POST',
      body: { module: 'leads', name: 'Hot', filters: { status: ['assigned'], tags: [UUID] } }
    });
    expect(result.status).toBe(303);
    // Back to this page's own path, whatever the hidden query said.
    expect(result.location.startsWith('/leads?')).toBe(true);
  });

  it("tickets: the queue's default is saved as its statuses, All as none", async () => {
    apiRequest.mockResolvedValue({ id: VIEW });
    const tickets = savedViewActions('cases');
    await outcome(tickets.saveView(post({ name: 'Open', query: 'priority=High' })));
    await outcome(tickets.saveView(post({ name: 'All', query: 'all=1&priority=High' })));
    expect(apiRequest.mock.calls[0][1].body.filters).toEqual({
      status: ['New', 'Assigned', 'Pending'],
      priority: ['High']
    });
    expect(apiRequest.mock.calls[1][1].body.filters).toEqual({ priority: ['High'] });
  });

  it('refuses a blank name without calling the API', async () => {
    const result = await actions.saveView(post({ name: '  ', query: '' }));
    expect(result.status).toBe(400);
    expect(result.data.savedViewError).toMatch(/name/);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("shows the API's own sentence when it refuses", async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('name: x'), {
        status: 400,
        body: { name: ['You already have a view with this name for this list.'] }
      })
    );
    const result = await actions.saveView(post({ name: 'Hot', query: '' }));
    expect(result.status).toBe(400);
    expect(result.data.savedViewError).toBe(
      'You already have a view with this name for this list.'
    );
  });

  it('renames and deletes by id, and never builds a path from a non-id', async () => {
    apiRequest.mockResolvedValue(null);
    await outcome(actions.renameView(post({ id: VIEW, name: 'Warm', query: '' })));
    expect(apiRequest.mock.calls[0]).toEqual([
      `/saved-views/${VIEW}/`,
      { method: 'PATCH', body: { name: 'Warm' } },
      { cookies }
    ]);
    await outcome(actions.deleteView(post({ id: VIEW, query: 'status=assigned' })));
    expect(apiRequest.mock.calls[1][0]).toBe(`/saved-views/${VIEW}/`);
    expect(apiRequest.mock.calls[1][1].method).toBe('DELETE');

    apiRequest.mockReset();
    const bad = await actions.deleteView(post({ id: '../leads/1', query: '' }));
    expect(bad.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("answers another person's view (a 404 upstream) as gone", async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('nf'), { status: 404, body: {} }));
    const result = await actions.deleteView(post({ id: VIEW, query: '' }));
    expect(result.status).toBe(404);
    expect(result.data.savedViewError).toBe('That view no longer exists.');
  });
});
