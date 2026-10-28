import { describe, it, expect } from 'vitest';
import {
  acceptsDrops,
  cardControls,
  boardFieldOptions,
  boardFieldRequired,
  boardLanes,
  moveFields,
  moveTargets
} from './object-board.js';

const col = (kind: 'option' | 'none' | 'other', value: string | null, n = 1) => ({
  kind,
  value,
  label: value ?? kind,
  count: n,
  records: Array.from({ length: Math.min(n, 2) }, (_, i) => ({
    id: `${kind}${i}`,
    name: `${kind}${i}`,
    can_edit: true,
    custom_fields: {},
    assigned_to: [],
    tags: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z'
  }))
});

describe('boardLanes', () => {
  it('keeps the API order and gives each lane a distinct id', () => {
    const lanes = boardLanes([
      col('option', 'a'),
      col('option', 'b'),
      col('none', null),
      col('other', null)
    ]);
    expect(lanes.map((l) => l.id)).toEqual(['option:a', 'option:b', 'none', 'other']);
    expect(lanes[2].value).toBeNull();
  });

  it('marks a lane truncated when the count exceeds the cards sent', () => {
    const [lane] = boardLanes([col('option', 'a', 150)]);
    expect(lane.truncated).toBe(true);
    expect(lane.count).toBe(150);
  });

  it('reads a missing column list as an empty board', () => {
    expect(boardLanes(undefined)).toEqual([]);
  });
});

describe('drop rules', () => {
  it('takes drops in options, in No value only when optional, never in Other', () => {
    expect(acceptsDrops({ kind: 'option' }, true)).toBe(true);
    expect(acceptsDrops({ kind: 'none' }, false)).toBe(true);
    expect(acceptsDrops({ kind: 'none' }, true)).toBe(false);
    expect(acceptsDrops({ kind: 'other' }, false)).toBe(false);
  });

  it('offers the Move to menu only the lanes that take drops, but not its own', () => {
    const lanes = boardLanes([
      col('option', 'a'),
      col('option', 'b'),
      col('none', null),
      col('other', null)
    ]);
    expect(moveTargets(lanes, true, 'option:a').map((l) => l.id)).toEqual(['option:b']);
    expect(moveTargets(lanes, false, 'option:a').map((l) => l.id)).toEqual(['option:b', 'none']);
  });
});

describe('cardControls', () => {
  it('lets an editable card be dragged and moved from the menu', () => {
    expect(cardControls({ canEdit: true })).toEqual({ drag: true, moveSelect: true });
  });

  it('locks every card while the object is not writable', () => {
    expect(cardControls({ canEdit: true }, false)).toEqual({ drag: false, moveSelect: false });
  });

  it('gives a locked card neither a drag nor a Move to select', () => {
    expect(cardControls({ canEdit: false })).toEqual({ drag: false, moveSelect: false });
  });
});

describe('moveFields', () => {
  it('sends the option value and the neighbours that exist', () => {
    expect(moveFields({ kind: 'option', value: 'sold' }, 'r1', 'x', undefined)).toEqual({
      record: 'r1',
      kind: 'option',
      value: 'sold',
      above_id: 'x'
    });
  });

  it('sends no value for No value', () => {
    expect(moveFields({ kind: 'none', value: null }, 'r1')).toEqual({ record: 'r1', kind: 'none' });
  });
});

describe('board field setting', () => {
  const fields = [
    { key: 'stage', label: 'Stage', field_type: 'dropdown', is_active: true, is_required: true },
    { key: 'old', label: 'Old', field_type: 'dropdown', is_active: false },
    { key: 'price', label: 'Price', field_type: 'number', is_active: true },
    { key: 'owner', label: 'Owner', field_type: 'link', is_active: true }
  ];

  it('offers only active dropdown fields', () => {
    expect(boardFieldOptions(fields)).toEqual([{ value: 'stage', label: 'Stage' }]);
    expect(boardFieldOptions(undefined)).toEqual([]);
  });

  it('knows whether the board field is required', () => {
    expect(boardFieldRequired(fields, 'stage')).toBe(true);
    expect(boardFieldRequired(fields, 'price')).toBe(false);
  });
});
