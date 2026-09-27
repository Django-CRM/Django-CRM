import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/api-helpers.js', () => ({ apiRequest: vi.fn() }));

const { CURRENCY_CHOICES } = await import('$lib/server/v2/products.js');
const { CURRENCY_CODES, CURRENCY_SYMBOLS } = await import('$lib/constants/filters.js');

describe('CURRENCY_CHOICES', () => {
  it('labels every currency "CODE, Name", as the backend CURRENCY_CODES does', () => {
    for (const { code, label } of CURRENCY_CHOICES) {
      expect(label).toMatch(new RegExp(`^${code}, \\S`));
    }
  });

  it('offers exactly the codes of the shared picker list and its symbol map', () => {
    const codes = CURRENCY_CHOICES.map((c) => c.code);
    expect(CURRENCY_CODES.filter((c) => c.value).map((c) => c.value)).toEqual(codes);
    expect(Object.keys(CURRENCY_SYMBOLS)).toEqual(codes);
  });

  it('includes the South African rand (issue #770)', () => {
    expect(CURRENCY_CHOICES).toContainEqual({ code: 'ZAR', label: 'ZAR, Rand' });
    expect(CURRENCY_SYMBOLS.ZAR).toBe('R');
  });
});
