import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listReports } = await import('./reports.js');

const cookies = /** @type {any} */ ({ get: () => undefined });

/** A money block as the backend sends it: plain figure plus `by_currency`. */
const block = (/** @type {any[]} */ rows, /** @type {any} */ plain = {}) => ({
  ...plain,
  by_currency: rows
});

function respond({ dashboard, revenue, aging }) {
  apiRequest.mockImplementation(async (/** @type {string} */ url) => {
    if (url.includes('dashboard')) return dashboard;
    if (url.includes('revenue')) return revenue;
    return aging;
  });
}

const EMPTY_BUCKET = block([], { count: 0, amount: '0', invoices: [] });

describe('listReports', () => {
  // Braces matter: a function returned from beforeEach is run as a cleanup,
  // and mockReset() returns the mock, which would then be called with no URL.
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('builds one view per currency and never adds them together', async () => {
    respond({
      dashboard: {
        invoice_count: 4,
        average_days_to_pay: 9,
        summary: block([
          { currency: 'EUR', total_invoiced: '10', total_paid: '7', total_due: '3' },
          { currency: 'USD', total_invoiced: '140', total_paid: '100', total_due: '40' }
        ])
      },
      revenue: {
        data: [
          {
            period: '2026-09-01',
            by_currency: [
              { currency: 'EUR', invoiced: '10', revenue: '7', count: 1 },
              { currency: 'USD', invoiced: '140', revenue: '100', count: 1 }
            ]
          }
        ]
      },
      aging: {
        current: EMPTY_BUCKET,
        '1_30_days': block([
          { currency: 'EUR', count: 1, amount: '3' },
          { currency: 'USD', count: 1, amount: '40' }
        ]),
        '31_60_days': EMPTY_BUCKET,
        '61_90_days': EMPTY_BUCKET,
        over_90_days: EMPTY_BUCKET,
        overdue: block([
          { currency: 'EUR', count: 1, amount: '3' },
          { currency: 'USD', count: 1, amount: '40' }
        ]),
        by_account: [
          { id: 'a1', currency: 'EUR', name: 'Acme', count: 1, oldest_days: 10, amount: '3' },
          { id: 'a1', currency: 'USD', name: 'Acme', count: 1, oldest_days: 10, amount: '40' }
        ]
      }
    });

    const r = await listReports({ cookies });

    expect(r.can_view).toBe(true);
    expect(r.currencies).toEqual(['EUR', 'USD']);
    expect(r.invoice_count).toBe(4);
    expect(r.byCurrency.USD.dashboard).toEqual({
      total_invoiced: 140,
      total_paid: 100,
      overdue_amount: 40
    });
    expect(r.byCurrency.EUR.dashboard.total_invoiced).toBe(10);
    expect(r.byCurrency.EUR.revenue).toEqual([{ period: '2026-09-01', invoiced: 10, paid: 7 }]);
    expect(r.byCurrency.USD.aging.buckets[0]).toMatchObject({ amount: 40, count: 1 });
    expect(r.byCurrency.EUR.overdueByAccount).toEqual([
      { id: 'a1', name: 'Acme', count: 1, oldest_days: 10, amount: 3 }
    ]);
  });

  it('gives a non-admin an empty but renderable shape', async () => {
    apiRequest.mockRejectedValue({ status: 403 });

    const r = await listReports({ cookies });

    expect(r.can_view).toBe(false);
    expect(r.currencies).toEqual([]);
    expect(r.blank.revenue).toEqual([]);
    expect(r.blank.aging.buckets).toHaveLength(4);
  });
});
