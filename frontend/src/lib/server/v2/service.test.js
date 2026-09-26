import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getServiceAnalytics } = await import('./service.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const SERVICE = {
  totals: { opened: 3, closed: 1, open_now: 2, window_days: 7 },
  volume: [
    { date: '2026-09-20', opened: 1, closed: 0 },
    { date: '2026-09-26', opened: 2, closed: 1 }
  ],
  first_response: [{ priority: 'Urgent', target_minutes: 60, met: 1, missed: 0 }],
  by_type: [{ case_type: 'Question', count: 3 }],
  by_agent: [{ id: 'p1', name: 'Asha' }]
};
const CSAT = { average: 4.25, count: 4, distribution: { 1: 0, 2: 0, 3: 1, 4: 1, 5: 2 } };
const NRT = {
  median_hours: 1.5,
  count: 2,
  by_priority: [
    { priority: 'Urgent', target_hours: 1, median_hours: 0.5, met: 1, missed: 0 },
    { priority: 'High', target_hours: 4, median_hours: null, met: 0, missed: 1 }
  ]
};

/** Answer each endpoint with its own fixture, so call order cannot mask a mix-up. */
function answerByPath() {
  apiRequest.mockImplementation(async (/** @type {string} */ path) => {
    if (path.startsWith('/cases/analytics/service/')) return SERVICE;
    if (path.startsWith('/cases/csat/aggregate/')) return CSAT;
    if (path.startsWith('/cases/analytics/nrt/')) return NRT;
    throw new Error(`unexpected path ${path}`);
  });
}

describe('getServiceAnalytics, admin', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    answerByPath();
  });

  it('makes three calls, the window ones from the first volume day with no `to`', async () => {
    await getServiceAnalytics(event, '7');
    const paths = apiRequest.mock.calls.map((c) => c[0]);
    expect(paths).toEqual([
      '/cases/analytics/service/?days=7',
      '/cases/csat/aggregate/?from=2026-09-20',
      '/cases/analytics/nrt/?from=2026-09-20'
    ]);
    expect(paths.some((p) => p.includes('to='))).toBe(false);
  });

  it('re-keys every section into the camelCase the page reads', async () => {
    const out = await getServiceAnalytics(event);
    expect(out.can_view).toBe(true);
    expect(out.volume).toEqual(SERVICE.volume);
    expect(out.firstResponse).toEqual(SERVICE.first_response);
    expect(out.byType).toEqual(SERVICE.by_type);
    expect(out.byAgent).toEqual(SERVICE.by_agent);
    expect(out.csat).toEqual({ average: 4.25, count: 4, distribution: CSAT.distribution });
    expect(out.nextResponse).toEqual(NRT.by_priority);
  });

  it('keeps every rating bucket even when the backend omits one', async () => {
    apiRequest.mockImplementation(async (/** @type {string} */ path) => {
      if (path.startsWith('/cases/analytics/service/')) return SERVICE;
      if (path.startsWith('/cases/csat/aggregate/'))
        return { average: 5, count: 1, distribution: { 5: 1 } };
      return NRT;
    });
    const { csat } = await getServiceAnalytics(event);
    expect(csat.distribution).toEqual({ 1: 0, 2: 0, 3: 0, 4: 0, 5: 1 });
  });

  it('passes a null average through for a window with no ratings', async () => {
    apiRequest.mockImplementation(async (/** @type {string} */ path) => {
      if (path.startsWith('/cases/analytics/service/')) return SERVICE;
      if (path.startsWith('/cases/csat/aggregate/')) {
        return { average: null, count: 0, distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 } };
      }
      return NRT;
    });
    const { csat } = await getServiceAnalytics(event);
    expect(csat.average).toBeNull();
    expect(csat.count).toBe(0);
  });
});

describe('getServiceAnalytics, non-admin', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('stops at the 403 and returns the empty shape, with no satisfaction or next-response call', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }));
    const out = await getServiceAnalytics(event);
    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/analytics/service/');
    expect(out.can_view).toBe(false);
    expect(out.volume).toEqual([]);
    expect(out.nextResponse).toEqual([]);
    expect(out.csat).toEqual({
      average: null,
      count: 0,
      distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 }
    });
  });

  it('rethrows any failure that is not a 403', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Boom'), { status: 500 }));
    await expect(getServiceAnalytics(event)).rejects.toThrow('Boom');
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });
});
