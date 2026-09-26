import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listAccounts } = await import('$lib/server/v2/accounts.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * An account row as the API sends it, rollups included.
 *
 * @param {string} id
 * @param {any[]} byCurrency
 */
const account = (id, byCurrency) => ({
  id,
  name: id,
  rollups: {
    won_count: 1,
    open_deal_count: 0,
    open_tickets: 0,
    first_won_on: null,
    won_amount: byCurrency.length === 1 ? byCurrency[0].won_amount : null,
    open_pipeline: byCurrency.length === 1 ? byCurrency[0].open_pipeline : null,
    overdue_amount: byCurrency.length === 1 ? byCurrency[0].overdue_amount : null,
    by_currency: byCurrency
  }
});

/** @param {any[]} rows */
const respond = (rows) =>
  apiRequest.mockResolvedValue({
    active_accounts: { open_accounts: rows, open_accounts_count: rows.length },
    closed_accounts: {}
  });

describe('account rollups', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('keeps each currency on its own and drops the empty ones', async () => {
    respond([
      account('a', [
        { currency: 'EUR', won_amount: '300.00', open_pipeline: '0', overdue_amount: '70.00' },
        { currency: 'USD', won_amount: '1000.00', open_pipeline: '50.00', overdue_amount: '0' }
      ])
    ]);
    const { results } = await listAccounts(event);
    const [row] = results;
    expect(row.won_by_currency).toEqual([
      { currency: 'EUR', amount: 300 },
      { currency: 'USD', amount: 1000 }
    ]);
    expect(row.pipeline_by_currency).toEqual([{ currency: 'USD', amount: 50 }]);
    expect(row.overdue_by_currency).toEqual([{ currency: 'EUR', amount: 70 }]);
    expect(row).not.toHaveProperty('won_amount');
  });

  it('an account with nothing has empty lists, not zeros', async () => {
    respond([account('a', [])]);
    const [row] = (await listAccounts(event)).results;
    expect(row.won_by_currency).toEqual([]);
    expect(row.pipeline_by_currency).toEqual([]);
    expect(row.overdue_by_currency).toEqual([]);
  });

  it('sorts largest won first within a currency, never across two', async () => {
    const won = (/** @type {string} */ currency, /** @type {string} */ amount) => [
      { currency, won_amount: amount, open_pipeline: '0', overdue_amount: '0' }
    ];
    respond([
      account('none', []),
      account('usd-small', won('USD', '10')),
      account('eur-small', won('EUR', '5')),
      account('usd-big', won('USD', '500'))
    ]);
    const { results } = await listAccounts(event);
    expect(results.map((r) => r.id)).toEqual(['eur-small', 'usd-big', 'usd-small', 'none']);
  });
});
