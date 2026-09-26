import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { auditFilters, listAuditLog, AUDIT_PAGE } = await import('$lib/server/v2/audit-log.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

describe('auditFilters', () => {
  it('forwards only the known filters, blank ones dropped', () => {
    const params = new URLSearchParams(
      'event_type=ORG_SWITCH&actor=&from=2026-09-01&org=attacker&to=%20'
    );
    expect(auditFilters(params)).toEqual({ event_type: 'ORG_SWITCH', from: '2026-09-01' });
  });

  it('forwards the token refresh toggle when it is on', () => {
    const params = new URLSearchParams('include_token_refresh=true');
    expect(auditFilters(params)).toEqual({ include_token_refresh: 'true' });
  });
});

describe('listAuditLog', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks for one page with the filters', async () => {
    apiRequest.mockResolvedValue({ count: 1, results: [{ id: 'a' }], event_types: [] });
    const out = await listAuditLog(event, { actor: 'u1' }, 25);
    const url = apiRequest.mock.calls[0][0];
    expect(url.startsWith('/org/audit-log/?')).toBe(true);
    const q = new URLSearchParams(url.split('?')[1]);
    expect(q.get('actor')).toBe('u1');
    expect(q.has('include_token_refresh')).toBe(false);
    expect(q.get('limit')).toBe(String(AUDIT_PAGE));
    expect(q.get('offset')).toBe('25');
    expect(out).toMatchObject({ forbidden: false, count: 1, entries: [{ id: 'a' }], error: null });
  });

  it('passes the token refresh toggle on to the API', async () => {
    apiRequest.mockResolvedValue({ count: 0, results: [], event_types: [] });
    await listAuditLog(event, { include_token_refresh: 'true' }, 0);
    const q = new URLSearchParams(apiRequest.mock.calls[0][0].split('?')[1]);
    expect(q.get('include_token_refresh')).toBe('true');
  });

  it('folds a 403 into forbidden', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('no'), { status: 403 }));
    expect(await listAuditLog(event, {}, 0)).toEqual({ forbidden: true });
  });

  it('shows a refused filter beside an empty list', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('event_type: Unknown event type.'), { status: 400 })
    );
    const out = /** @type {any} */ (await listAuditLog(event, { event_type: 'X' }, 0));
    expect(out).toMatchObject({ forbidden: false, entries: [], count: 0 });
    expect(out.error).toMatch(/Unknown event type/);
  });

  it('lets any other failure through', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }));
    await expect(listAuditLog(event, {}, 0)).rejects.toThrow('boom');
  });
});
