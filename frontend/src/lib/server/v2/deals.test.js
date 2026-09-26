import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listDeals } = await import('$lib/server/v2/deals.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * Deals carry their own currency and there are no exchange rates, so the API
 * sends `amount_sum` and `weighted_sum` as `null` once there are several
 * currencies and splits them in `by_currency`. The header has to print each
 * currency on its own rather than a sum labelled in the org's currency.
 */
describe('pipeline totals', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('keeps each currency on its own', async () => {
    apiRequest.mockResolvedValue({
      opportunities: [],
      totals: {
        count: 3,
        amount_sum: null,
        weighted_sum: null,
        by_currency: [
          { currency: 'EUR', amount_sum: '300.00', weighted_sum: '150.40' },
          { currency: 'USD', amount_sum: '1000.00', weighted_sum: '500.00' }
        ],
        stalled_count: 1
      }
    });

    const { totals } = await listDeals(event);

    expect(totals.count).toBe(3);
    expect(totals.stalled_count).toBe(1);
    expect(totals.amount_by_currency).toEqual([
      { currency: 'EUR', amount: 300 },
      { currency: 'USD', amount: 1000 }
    ]);
    expect(totals.weighted_by_currency).toEqual([
      { currency: 'EUR', amount: 150 },
      { currency: 'USD', amount: 500 }
    ]);
  });

  it('holds nothing for an unpriced pipeline, so the page prints its own zero', async () => {
    apiRequest.mockResolvedValue({
      opportunities: [],
      totals: { count: 1, amount_sum: '0', weighted_sum: '0', by_currency: [], stalled_count: 0 }
    });

    const { totals } = await listDeals(event);

    expect(totals.amount_by_currency).toEqual([]);
    expect(totals.weighted_by_currency).toEqual([]);
  });
});
