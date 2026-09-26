import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listEstimates } = await import('$lib/server/v2/estimates.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/** @param {string} status @param {string} total @param {string} currency */
const estimate = (status, total, currency) => ({
  id: `${status}-${currency}-${total}`,
  status,
  total_amount: total,
  currency,
  converted_to_invoice: null
});

/**
 * Each estimate carries its own currency and there are no exchange rates, so
 * the header figures are per currency, never one sum printed in the org's.
 */
describe('estimate header figures', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('adds up each currency on its own', async () => {
    apiRequest.mockResolvedValue({
      count: 4,
      results: [
        estimate('Accepted', '100.00', 'USD'),
        estimate('Accepted', '40.00', 'EUR'),
        estimate('Sent', '70.00', 'EUR'),
        estimate('Sent', '30.00', 'EUR')
      ]
    });

    const { totals } = await listEstimates(event);

    expect(totals.accepted_unconverted).toEqual([
      { currency: 'EUR', amount: 40 },
      { currency: 'USD', amount: 100 }
    ]);
    expect(totals.awaiting_reply).toEqual([{ currency: 'EUR', amount: 100 }]);
  });
});
