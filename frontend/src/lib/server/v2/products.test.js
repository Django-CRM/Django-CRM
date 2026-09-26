import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/api-helpers.js', () => ({ apiRequest: vi.fn() }));

const { CURRENCY_CHOICES } = await import('$lib/server/v2/products.js');

describe('CURRENCY_CHOICES', () => {
  it('labels every currency "CODE, Name", as the backend CURRENCY_CODES does', () => {
    for (const { code, label } of CURRENCY_CHOICES) {
      expect(label).toMatch(new RegExp(`^${code}, \\S`));
    }
  });
});
