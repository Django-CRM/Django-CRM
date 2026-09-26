import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getContact, getContactForEdit } = await import('$lib/server/v2/contacts.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/** Ten deal rows, as the API caps the list, plus the server's open summary. */
function detail(openDeals) {
  return {
    contact_obj: { id: 'c1', first_name: 'Ada', last_name: 'Lovelace', assigned_to: [], teams: [] },
    opportunities: Array.from({ length: 10 }, (_, i) => ({
      id: `d${i}`,
      name: `Deal ${i}`,
      stage: 'PROSPECTING',
      amount: '100',
      currency: 'USD'
    })),
    open_deals: openDeals,
    cases: [],
    tasks: [],
    colleagues: [],
    comments: [],
    attachments: []
  };
}

describe('getContact open deals', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("takes the server's count and totals, not the 10 rows it holds", async () => {
    apiRequest.mockResolvedValue(
      detail({
        count: 13,
        amount: null,
        by_currency: [
          { currency: 'EUR', count: 1, amount: '5.00' },
          { currency: 'USD', count: 12, amount: '1110.00' }
        ]
      })
    );

    const { openDeals, deals } = await getContact(event, 'c1');

    expect(deals).toHaveLength(10);
    expect(openDeals).toEqual({
      count: 13,
      by_currency: [
        { currency: 'EUR', amount: 5 },
        { currency: 'USD', amount: 1110 }
      ]
    });
  });

  it('reads an older server without the summary as no open deals', async () => {
    apiRequest.mockResolvedValue(detail(undefined));
    const { openDeals } = await getContact(event, 'c1');
    expect(openDeals).toEqual({ count: 0, by_currency: [] });
  });
});

describe('getContactForEdit deal count', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  /** @param {any} detailResponse */
  function respond(detailResponse) {
    apiRequest.mockImplementation(async (/** @type {string} */ url) =>
      url.startsWith('/contacts/c1/') ? detailResponse : {}
    );
  }

  it('says how many deals there are, not how many rows came back', async () => {
    respond({ ...detail({ count: 0, by_currency: [] }), opportunity_count: 23 });
    const { server } = await getContactForEdit(event, 'c1');
    expect(server.deal_count).toBe(23);
  });

  it('falls back to the rows from an older server without the count', async () => {
    respond(detail({ count: 0, by_currency: [] }));
    const { server } = await getContactForEdit(event, 'c1');
    expect(server.deal_count).toBe(10);
  });
});
