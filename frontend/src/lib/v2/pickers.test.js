import { describe, it, expect } from 'vitest';

import { missingOption, missingOptions, inactiveOptionLabel, sameIds } from './pickers.js';

const people = [
  { id: 'p1', name: 'Ada Lovelace' },
  { id: 'p2', name: 'Grace Hopper' }
];

describe('missingOptions', () => {
  it('returns the stored entries the options list cannot offer', () => {
    const stored = [{ id: 'p1' }, { id: 'gone', email: 'left@x.io' }];
    expect(missingOptions(people, stored)).toEqual([{ id: 'gone', email: 'left@x.io' }]);
  });

  it('returns nothing when every stored entry is in the list', () => {
    expect(missingOptions(people, [{ id: 'p1' }, { id: 'p2' }])).toEqual([]);
  });

  it('keeps the stored order', () => {
    const stored = [{ id: 'a' }, { id: 'p1' }, { id: 'b' }];
    expect(missingOptions(people, stored).map((s) => s.id)).toEqual(['a', 'b']);
  });

  it('treats an empty options list as offering nothing', () => {
    // What a failed `getOrgPeopleAndTeams` fetch looks like. Keeping the
    // stored value is still the right outcome: the form cannot offer an
    // alternative, so discarding it would be pure loss.
    expect(missingOptions([], [{ id: 'p1' }])).toEqual([{ id: 'p1' }]);
  });

  it('survives null and undefined on either side', () => {
    expect(missingOptions(null, null)).toEqual([]);
    expect(missingOptions(undefined, undefined)).toEqual([]);
    expect(missingOptions(people, undefined)).toEqual([]);
  });

  it('ignores stored entries with no id', () => {
    expect(missingOptions(people, [null, {}, { id: '' }])).toEqual([]);
  });
});

describe('missingOption', () => {
  it('returns the stored target when the list cannot offer it', () => {
    expect(missingOption(people, { id: 'gone', name: 'Departed' })).toEqual({
      id: 'gone',
      name: 'Departed'
    });
  });

  it('returns null when the list can offer it', () => {
    expect(missingOption(people, { id: 'p1', name: 'Ada Lovelace' })).toBeNull();
  });

  it('returns null when nothing is stored', () => {
    expect(missingOption(people, null)).toBeNull();
    expect(missingOption(people, undefined)).toBeNull();
  });
});

describe('inactiveOptionLabel', () => {
  it('says the target is no longer active', () => {
    expect(inactiveOptionLabel('Ada Lovelace')).toBe('Ada Lovelace (no longer active)');
  });

  it('falls back to Unnamed rather than rendering null', () => {
    // Both `shapeTarget` and `shapeAssignee` resolve a missing
    // `user_details.name` to null, so a bare interpolation would read
    // "null (no longer active)".
    expect(inactiveOptionLabel(null)).toBe('Unnamed (no longer active)');
    expect(inactiveOptionLabel('')).toBe('Unnamed (no longer active)');
  });
});

describe('a stored team when the team list failed to load', () => {
  // `getOrgPeopleAndTeams` answers `teams: []` on a failed fetch. A team
  // select with no option for the stored team falls back to its first option
  // ("Any team" / "No team", value ''), and saving turns that into null. The
  // approval rule and escalation policy forms render this as its own option.
  it('is kept as an option of its own', () => {
    expect(missingOption([], { id: 't1', name: 'Support' })).toEqual({ id: 't1', name: 'Support' });
  });

  it('adds nothing when no team is stored', () => {
    expect(missingOption([], null)).toBeNull();
  });
});

describe('sameIds', () => {
  it('ignores order and repeats', () => {
    expect(sameIds(['a', 'b'], ['b', 'a', 'a'])).toBe(true);
  });

  it('treats two empty lists as unchanged', () => {
    // What a form posts when its picker failed to load: no boxes, no
    // originals. Unchanged, so the action leaves the field out.
    expect(sameIds([], [])).toBe(true);
  });

  it('sees an addition, a removal and a swap', () => {
    expect(sameIds(['a', 'b'], ['a'])).toBe(false);
    expect(sameIds(['a'], ['a', 'b'])).toBe(false);
    expect(sameIds(['a'], ['b'])).toBe(false);
  });
});
