import { describe, it, expect } from 'vitest';
import { mergeSideLabel } from './merge-label.js';

describe('mergeSideLabel', () => {
  const current = { id: 'a', name: 'Rosalind Beck' };

  it('uses the names when they differ', () => {
    const other = { id: 'b', name: 'Ros Beck' };
    expect(mergeSideLabel(current, current, other)).toBe('Rosalind Beck');
    expect(mergeSideLabel(other, current, other)).toBe('Ros Beck');
  });

  it('tells two records with one name apart', () => {
    const other = { id: 'b', name: 'Rosalind Beck' };
    expect(mergeSideLabel(current, current, other)).toBe('this record');
    expect(mergeSideLabel(other, current, other)).toBe('the other record');
  });
});
