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

const { createEstimate, estimateFromDeal, sendEstimate } =
  await import('$lib/server/v2/estimates.js');

describe('creating and sending', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('posts the builder body and unwraps the created estimate', async () => {
    apiRequest.mockResolvedValue({ estimate: { id: 'e1' } });

    const created = await createEstimate(event, { title: 'T' });

    expect(apiRequest).toHaveBeenCalledWith(
      '/invoices/estimates/',
      { method: 'POST', body: { title: 'T' } },
      { cookies: event.cookies }
    );
    expect(created).toEqual({ id: 'e1' });
  });

  it('sends through the estimate send endpoint', async () => {
    apiRequest.mockResolvedValue({ error: false });

    await sendEstimate(event, 'e1');

    expect(apiRequest.mock.calls[0][0]).toBe('/invoices/estimates/e1/send/');
    expect(apiRequest.mock.calls[0][1].method).toBe('POST');
  });
});

describe('an estimate started from a deal', () => {
  const DEAL = '6f1c2b3a-1111-4222-8333-944455556666';

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks the API nothing for an id that is not a UUID', async () => {
    expect(await estimateFromDeal(event, '../invoices/x')).toBeNull();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('carries the deal, its account, currency, visible contacts and lines', async () => {
    apiRequest.mockResolvedValue({
      opportunity_obj: {
        id: DEAL,
        name: 'Renewal',
        currency: 'EUR',
        account: { id: 'a1', name: 'Acme' },
        line_items: [
          {
            name: 'Seats',
            description: 'Annual',
            quantity: '3.00',
            unit_price: '40.00',
            discount_type: 'PERCENTAGE',
            discount_value: '10.00',
            discount_amount: '12.00',
            product: { id: 'p1', name: 'Seat' }
          },
          { name: 'Setup', quantity: '1.00', unit_price: '50.00', discount_value: '0.00' }
        ]
      },
      contacts: [{ id: 'c1' }, { id: 'c2' }]
    });

    const prefill = await estimateFromDeal(event, DEAL);

    expect(apiRequest.mock.calls[0][0]).toBe(`/opportunities/${DEAL}/`);
    expect(prefill).toEqual({
      opportunity_id: DEAL,
      account_id: 'a1',
      currency: 'EUR',
      title: 'Estimate for Renewal',
      contact_ids: ['c1', 'c2'],
      // The deal line's discount comes along: the estimate's total counts it.
      items: [
        {
          name: 'Seats',
          description: 'Annual',
          quantity: 3,
          unit_price: 40,
          product: 'p1',
          discount_type: 'PERCENTAGE',
          discount_value: 10
        },
        {
          name: 'Setup',
          description: '',
          quantity: 1,
          unit_price: 50,
          product: null,
          discount_type: '',
          discount_value: 0
        }
      ]
    });
  });

  it('opens blank for a deal the caller cannot open', async () => {
    for (const status of [403, 404]) {
      apiRequest.mockRejectedValueOnce(Object.assign(new Error('no'), { status }));
      expect(await estimateFromDeal(event, DEAL)).toBeNull();
    }
  });

  it('lets any other failure through', async () => {
    apiRequest.mockRejectedValueOnce(Object.assign(new Error('down'), { status: 500 }));
    await expect(estimateFromDeal(event, DEAL)).rejects.toThrow('down');
  });
});
