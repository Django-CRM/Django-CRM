import { describe, it, expect } from 'vitest';
// Imported from `deals.js`, not from `+page.server.js`. SvelteKit permits only
// a fixed set of named exports on a `+page.server.js` and answers 500 for the
// whole route on any other one, so these constants cannot live there. This
// test passed while the pipeline page was returning 500 for every request,
// because vitest imports the module directly and never goes through the
// router. Do not "simplify" this back to a route import.
import { BOARD_FIELDS, BOARD_PRESETS } from '$lib/server/v2/deals.js';
import { FILTERS, fieldKeys } from '$lib/v2/filters.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';

/**
 * The board renders from `/opportunities/kanban/`, a different endpoint from
 * the list's `/opportunities/`, but it runs the list's own filters
 * (`deal_list_queryset` in `backend/opportunity/views/opportunity_views.py`),
 * so it reads every param below. `BOARD_FIELDS` names the subset of
 * pipeline's OWN descriptor fields the board offers, and must stay inside
 * this list so the board never offers a chip for a param it cannot run.
 * Re-check this list against the backend if either side ever changes;
 * nothing enforces the agreement except this test.
 */
const KANBAN_SUPPORTED_PARAMS = [
  'search',
  'name',
  'account',
  'assigned_to',
  'tags',
  'stage',
  'lead_source',
  'closed_on__gte',
  'closed_on__lte',
  'created_at__gte',
  'created_at__lte',
  'amount__gte',
  'amount__lte',
  'open',
  'rotten'
];

describe('BOARD_FIELDS', () => {
  it('is a subset of the pipeline descriptor field keys', () => {
    const pipelineKeys = fieldKeys(FILTERS.pipeline);
    for (const key of BOARD_FIELDS) {
      expect(pipelineKeys, `"${key}" is not a pipeline descriptor field`).toContain(key);
    }
  });

  it('names only params the kanban endpoint actually reads', () => {
    for (const key of BOARD_FIELDS) {
      expect(
        KANBAN_SUPPORTED_PARAMS,
        `"${key}" is not read by OpportunityKanbanView (deal_list_queryset)`
      ).toContain(key);
    }
  });

  it('does not offer stage, lead_source or amount on the board', () => {
    // The kanban endpoint reads all three now, but the board has no UI for
    // them yet, so they stay off it. Offering one is a deliberate change to
    // the board, and this is the test to update when that happens.
    expect(BOARD_FIELDS).not.toContain('stage');
    expect(BOARD_FIELDS).not.toContain('lead_source');
    expect(BOARD_FIELDS).not.toContain('amount');
  });
});

describe('BOARD_PRESETS', () => {
  it('is a subset of the pipeline descriptor preset keys', () => {
    const presetKeys = FILTERS.pipeline.presets.map((p) => p.key);
    for (const key of BOARD_PRESETS) {
      expect(presetKeys, `"${key}" is not a pipeline preset`).toContain(key);
    }
  });

  it('does not offer the "open" or "stalled" preset on the board', () => {
    // "open" writes ?open=true and "stalled" writes ?rotten=true. The kanban
    // endpoint reads both now, but the board already shows open deals only
    // (`dealListQuery` sets open=true for it) and has no UI for "stalled"
    // yet, so neither preset is offered there.
    expect(BOARD_PRESETS).not.toContain('open');
    expect(BOARD_PRESETS).not.toContain('stalled');
  });
});

describe('the board-safe param builder', () => {
  const url = new URL(
    'http://x/pipeline?stage=NEGOTIATION&lead_source=call&amount__gte=5000&amount__lte=10000&assigned_to=aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa&tags=bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'
  );

  it('drops stage, lead_source and the amount range', () => {
    const params = buildFilterQuery(BOARD_FIELDS, readFilters(url, 'pipeline'));
    expect(params.has('stage')).toBe(false);
    expect(params.has('lead_source')).toBe(false);
    expect(params.has('amount__gte')).toBe(false);
    expect(params.has('amount__lte')).toBe(false);
  });

  it('keeps assigned_to and tags', () => {
    const params = buildFilterQuery(BOARD_FIELDS, readFilters(url, 'pipeline'));
    expect(params.get('assigned_to')).toBe('aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa');
    expect(params.get('tags')).toBe('bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb');
  });
});
