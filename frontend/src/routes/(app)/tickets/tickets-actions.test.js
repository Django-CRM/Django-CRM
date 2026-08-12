import { describe, it, expect } from 'vitest';
import { parseBulkForm } from '$lib/server/v2/bulk-form.js';

function fd(entries) {
  const f = new FormData();
  for (const [k, v] of entries) f.append(k, v);
  return f;
}

describe('parseBulkForm', () => {
  it('reads ids and a scalar field', () => {
    const { ids, fields } = parseBulkForm(
      fd([
        ['ids', 'a'],
        ['ids', 'b'],
        ['field', 'priority'],
        ['value', 'Urgent']
      ])
    );
    expect(ids).toEqual(['a', 'b']);
    expect(fields).toEqual({ priority: 'Urgent' });
  });

  it('reads a Close with a date', () => {
    const { fields } = parseBulkForm(
      fd([
        ['ids', 'a'],
        ['field', 'status'],
        ['value', 'Closed'],
        ['closed_on', '2026-05-09']
      ])
    );
    expect(fields).toEqual({ status: 'Closed', closed_on: '2026-05-09' });
  });

  it('reads a multi-value m2m field', () => {
    const { fields } = parseBulkForm(
      fd([
        ['ids', 'a'],
        ['field', 'tags'],
        ['value', 't1'],
        ['value', 't2']
      ])
    );
    expect(fields).toEqual({ tags: ['t1', 't2'] });
  });
});
