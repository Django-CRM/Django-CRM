/**
 * The record CSV exports: the query each list page sends is the query its
 * export sends, and the proxy streams the file through with the caller's own
 * token and nothing else.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('$env/dynamic/public', () => ({
  env: { PUBLIC_DJANGO_API_URL: 'http://api.test' }
}));

const { forwardCsvExport } = await import('./csv-export.js');
const queries = await import('./list-queries.js');
const leadsExport = await import('../../../routes/api/leads/export/+server.js');
const accountsExport = await import('../../../routes/api/accounts/export/+server.js');

const UUID = '11111111-2222-3333-4444-555555555555';
const page = (/** @type {string} */ path) => new URL(`http://app.test${path}`);

function event(/** @type {string} */ path, /** @type {Record<string, string>} */ jar = {}) {
  return /** @type {any} */ ({
    url: page(path),
    cookies: { get: (/** @type {string} */ k) => jar[k] },
    request: new Request(page(path))
  });
}

/** @type {import('vitest').Mock} */
let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      new Response('\ufeffID\r\n', {
        status: 200,
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': 'attachment; filename="leads-2026-09-26.csv"'
        }
      })
  );
  vi.stubGlobal('fetch', fetchMock);
});

/** The URL the proxy asked Django for. */
const asked = () => new URL(fetchMock.mock.calls[0][0]);

describe('forwardCsvExport', () => {
  it('refuses without a session and never calls the API', async () => {
    const res = await forwardCsvExport(event('/x'), '/leads/export/', new URLSearchParams());
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends the caller token, asks for CSV and drops paging', async () => {
    const res = await forwardCsvExport(
      event('/x', { jwt_access: 'tok' }),
      '/leads/export/',
      new URLSearchParams({ search: 'ada', limit: '25', offset: '50' })
    );
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).toEqual({ Authorization: 'Bearer tok', Accept: 'text/csv' });
    expect(asked().pathname).toBe('/api/leads/export/');
    expect([...asked().searchParams]).toEqual([['search', 'ada']]);
    expect(res.status).toBe(200);
    expect(res.headers.get('Content-Disposition')).toBe(
      'attachment; filename="leads-2026-09-26.csv"'
    );
    // Bytes, not `text()`: decoding strips the BOM Excel needs, so this
    // proves it arrives intact.
    expect([...new Uint8Array(await res.arrayBuffer())].slice(0, 3)).toEqual([0xef, 0xbb, 0xbf]);
  });

  it('passes an upstream refusal through as a refusal', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 400 }));
    const res = await forwardCsvExport(
      event('/x', { jwt_access: 'tok' }),
      '/leads/export/',
      new URLSearchParams()
    );
    expect(res.status).toBe(400);
  });
});

describe('list queries', () => {
  it('forward only what the page forwards', () => {
    const q = queries.leadListQuery(
      page(`/leads?status=assigned&include_deleted=true&assigned_to=${UUID}&search=a`)
    );
    expect(q.get('status')).toBe('assigned');
    expect(q.get('assigned_to')).toBe(UUID);
    expect(q.get('search')).toBe('a');
    expect(q.has('include_deleted')).toBe(false);
  });

  it('default the ticket queue to open statuses, and drop that on all=1', () => {
    expect(queries.ticketListQuery(page('/tickets')).getAll('status')).toEqual([
      'New',
      'Assigned',
      'Pending'
    ]);
    expect(queries.ticketListQuery(page('/tickets?all=1')).has('status')).toBe(false);
    expect(queries.ticketListQuery(page('/tickets?status=Closed')).getAll('status')).toEqual([
      'Closed'
    ]);
  });

  it('ask for active contacts unless inactive=1', () => {
    expect(queries.contactListQuery(page('/contacts')).get('is_active')).toBe('true');
    expect(queries.contactListQuery(page('/contacts?inactive=1')).has('is_active')).toBe(false);
  });

  it('resolve the board to one pipeline and open stages', () => {
    const pipelines = [
      { id: 'p1', is_default: false },
      { id: 'p2', is_default: true }
    ];
    const board = queries.dealListQuery(page('/pipeline?view=board&open=false'), pipelines);
    expect(board.params.get('pipeline')).toBe('p2');
    expect(board.params.get('open')).toBe('true');

    const list = queries.dealListQuery(page('/pipeline?pipeline=nope&rotten=true'), pipelines);
    expect(list.params.has('pipeline')).toBe(false);
    expect(list.params.get('rotten')).toBe('true');
  });
});

describe('export proxies', () => {
  it('ask for the open leads the page lists', async () => {
    await leadsExport.GET(event('/api/leads/export?search=a&limit=5', { jwt_access: 't' }));
    expect(asked().pathname).toBe('/api/leads/export/');
    expect(asked().searchParams.get('open')).toBe('true');
    expect(asked().searchParams.get('search')).toBe('a');
    expect(asked().searchParams.has('limit')).toBe(false);
  });

  it('ask for the active accounts the page lists', async () => {
    await accountsExport.GET(event('/api/accounts/export', { jwt_access: 't' }));
    expect(asked().pathname).toBe('/api/accounts/export/');
    expect(asked().searchParams.get('is_active')).toBe('true');
  });
});
