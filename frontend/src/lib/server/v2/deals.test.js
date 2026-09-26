import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listDeals, listBoard, getDealFormOptions } = await import('$lib/server/v2/deals.js');

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

describe('pipelines', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('boards only open lanes, by kind rather than by code, and names them by label', async () => {
    apiRequest.mockResolvedValue({
      pipeline: { id: 'p-2', name: 'Renewals', is_default: false },
      columns: [
        { id: 'TALKING', name: 'Talking', kind: 'open', item_count: 1, items: [{ id: 'd-1' }] },
        // A won stage that is not called CLOSED_WON is still not a lane.
        { id: 'SIGNED', name: 'Signed', kind: 'won', item_count: 4, items: [] },
        { id: 'CHURNED', name: 'Churned', kind: 'lost', item_count: 0, items: [] }
      ]
    });

    const { lanes, pipelineId } = await listBoard(event, new URLSearchParams('pipeline=p-2'));

    expect(apiRequest.mock.calls[0][0]).toBe('/opportunities/kanban/?pipeline=p-2');
    expect(pipelineId).toBe('p-2');
    expect(lanes.map((/** @type {any} */ l) => [l.stage, l.label, l.count])).toEqual([
      ['TALKING', 'Talking', 1]
    ]);
  });

  it("carries each deal's pipeline, stage label and kind through to the rows", async () => {
    apiRequest.mockResolvedValue({
      opportunities: [
        { id: 'd-1', pipeline: 'p-2', stage: 'SIGNED', stage_label: 'Signed', stage_kind: 'won' }
      ],
      totals: null
    });

    const { results } = await listDeals(event);

    expect(results[0]).toMatchObject({
      pipeline: 'p-2',
      stage: 'SIGNED',
      stage_label: 'Signed',
      stage_kind: 'won'
    });
  });

  it("starts a new deal in the default pipeline's first open stage", async () => {
    apiRequest.mockImplementation(async (/** @type {string} */ path) => {
      if (path === '/opportunities/pipelines/') {
        return {
          pipelines: [
            {
              id: 'p-1',
              name: 'Sales',
              is_default: true,
              stages: [
                { id: 's-0', code: 'LOST', label: 'Lost', kind: 'lost' },
                { id: 's-1', code: 'FIRST', label: 'First', kind: 'open' }
              ]
            },
            { id: 'p-2', name: 'Other', is_default: false, stages: [] }
          ]
        };
      }
      return {};
    });

    const options = await getDealFormOptions(event);

    expect(options.defaults).toMatchObject({ pipeline: 'p-1', stage: 'FIRST' });
    expect(options.pipelines).toHaveLength(2);
  });
});
