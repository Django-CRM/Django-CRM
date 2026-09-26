import { describe, it, expect } from 'vitest';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';

const url = (/** @type {string} */ qs) => new URL(`http://x/tickets?${qs}`);

describe('readFilters', () => {
  it('returns an empty object for a bare URL', () => {
    expect(readFilters(url(''), 'tickets')).toEqual({});
  });

  it('keeps a valid select value', () => {
    expect(readFilters(url('priority=High'), 'tickets')).toEqual({ priority: 'High' });
  });

  it('drops a select value that is not in the option list', () => {
    // "Medium" is a Task priority, not a Case priority. Forwarding it would
    // return an empty queue with no error, which reads as "no tickets".
    expect(readFilters(url('priority=Medium'), 'tickets')).toEqual({});
  });

  it('drops a key the descriptor does not declare', () => {
    // include_deleted is admin-gated server-side; the point here is that a
    // hand-typed param never becomes a variable this layer carries.
    expect(readFilters(url('include_deleted=true'), 'tickets')).toEqual({});
  });

  it('keeps an opaque id without checking that it exists', () => {
    // Shape is checked, existence is not: proving the id exists would cost a
    // fetch, and the API answering "no rows" for a real-looking id that was
    // since deleted is the correct outcome.
    const id = '11111111-2222-3333-4444-555555555555';
    const out = readFilters(url(`assigned_to=${id}`), 'tickets');
    expect(out).toEqual({ assigned_to: id });
  });

  it('normalises a boolean and drops a non-boolean', () => {
    expect(readFilters(url('sla_breached=true'), 'tickets')).toEqual({ sla_breached: 'true' });
    expect(readFilters(url('sla_breached=yes'), 'tickets')).toEqual({});
  });
});

describe('text fields', () => {
  it('passes a free-text value through unvalidated', () => {
    const url = new URL('http://x/contacts?city=Hyderabad');
    expect(readFilters(url, 'contacts')).toEqual({ city: 'Hyderabad' });
  });
});

describe('buildFilterQuery', () => {
  it('emits only allow-listed keys', () => {
    const q = buildFilterQuery(['priority'], { priority: 'High', assigned_to: 'x' });
    expect(q.get('priority')).toBe('High');
    expect(q.has('assigned_to')).toBe(false);
  });

  it('skips empty values', () => {
    const q = buildFilterQuery(['priority'], { priority: '' });
    expect(q.has('priority')).toBe(false);
  });
});

describe('range param names are exact, not derived', () => {
  it('tasks emits a DOUBLE underscore due date range', () => {
    const url = new URL('http://x/tasks?due_date__gte=2026-01-01&due_date__lte=2026-01-31');
    expect(readFilters(url, 'tasks')).toEqual({
      due_date__gte: '2026-01-01',
      due_date__lte: '2026-01-31'
    });
  });

  it('tasks ignores the single-underscore spelling', () => {
    // Invoices uses due_date_gte. If a shared helper ever derives the key by
    // concatenation, this is the test that fails instead of the filter
    // silently returning an unfiltered list.
    const url = new URL('http://x/tasks?due_date_gte=2026-01-01');
    expect(readFilters(url, 'tasks')).toEqual({});
  });

  it('rejects a malformed date', () => {
    const url = new URL('http://x/tasks?due_date__gte=01-01-2026');
    expect(readFilters(url, 'tasks')).toEqual({});
  });

  it('accepts a half-open numeric range and rejects non-numeric', () => {
    expect(readFilters(new URL('http://x/pipeline?amount__gte=5000'), 'pipeline')).toEqual({
      amount__gte: '5000'
    });
    expect(readFilters(new URL('http://x/pipeline?amount__gte=lots'), 'pipeline')).toEqual({});
  });
});

describe('invoices uses the single-underscore date convention', () => {
  // Pinned against backend/invoices/api_views.py:149-152
  // (`InvoiceListView.filter_queryset`), which reads `due_date_gte` /
  // `due_date_lte`, one underscore. Every other v2 module's date range uses
  // two (`due_date__gte`). Re-check that line before "correcting" either spelling.
  it('accepts due_date_gte', () => {
    const url = new URL('http://x/invoices?due_date_gte=2026-01-01');
    expect(readFilters(url, 'invoices')).toEqual({ due_date_gte: '2026-01-01' });
  });

  it('ignores the double-underscore spelling that every other page uses', () => {
    const url = new URL('http://x/invoices?due_date__gte=2026-01-01');
    expect(readFilters(url, 'invoices')).toEqual({});
  });
});

describe('opaque id fields: shape-checked before they reach the API', () => {
  // Every list endpoint answers 500 on a malformed id, because the value
  // reaches Django as `id__in=['x']` and psycopg raises before any handler
  // runs. Confirmed against /opportunities/, /opportunities/kanban/, /leads/,
  // /cases/, /accounts/ and /tasks/. A stale bookmark or an edited URL should
  // cost the filter, not the page.
  it('drops a tag id that is not a UUID', () => {
    const url = new URL('http://x/tickets?tags=x');
    expect(readFilters(url, 'tickets').tags).toBeUndefined();
  });

  it('drops an owner id that is not a UUID', () => {
    const url = new URL('http://x/tickets?assigned_to=not-an-id');
    expect(readFilters(url, 'tickets').assigned_to).toBeUndefined();
  });

  it('keeps a well formed UUID', () => {
    const id = '868f58fd-f783-4472-baac-216f5e22484c';
    const url = new URL(`http://x/tickets?tags=${id}`);
    expect(readFilters(url, 'tickets').tags).toBe(id);
  });

  it('drops an account id that is not a UUID', () => {
    const url = new URL('http://x/invoices?account=12345');
    expect(readFilters(url, 'invoices').account).toBeUndefined();
  });
});

describe('deal stage filter', () => {
  const pipeline = (/** @type {string} */ qs) => new URL(`http://x/pipeline?${qs}`);

  it('keeps a stage code, whatever the org named the stage', () => {
    // Stages are configured per org, so there is no fixed option list to
    // check against; the shape is what can be checked.
    expect(readFilters(pipeline('stage=SIGNED_2'), 'pipeline')).toEqual({ stage: 'SIGNED_2' });
  });

  it('drops a value that cannot be a stage code', () => {
    expect(readFilters(pipeline('stage=closed%20won'), 'pipeline')).toEqual({});
    expect(readFilters(pipeline(`stage=${'A'.repeat(65)}`), 'pipeline')).toEqual({});
  });
});
