import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/api-helpers.js', () => ({ apiRequest: vi.fn() }));

const { viewerIsAdmin } = await import('./organization.js');

/** @param {Record<string, unknown> | null} claims */
function cookiesWith(claims) {
  const token =
    claims === null
      ? undefined
      : `h.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.s`;
  return /** @type {any} */ ({
    get: (/** @type {string} */ name) => (name === 'jwt_access' ? token : undefined)
  });
}

describe('viewerIsAdmin', () => {
  it('reads the signed admin fact, not the role', () => {
    expect(viewerIsAdmin(cookiesWith({ role: 'ADMIN', is_organization_admin: true }))).toBe(true);
    expect(viewerIsAdmin(cookiesWith({ role: 'USER', is_organization_admin: true }))).toBe(true);
    expect(viewerIsAdmin(cookiesWith({ role: 'USER', is_organization_admin: false }))).toBe(false);
    expect(viewerIsAdmin(cookiesWith({ role: 'ADMIN' }))).toBe(false);
  });

  it('is false with no token or an undecodable one', () => {
    expect(viewerIsAdmin(cookiesWith(null))).toBe(false);
    expect(viewerIsAdmin(/** @type {any} */ ({ get: () => 'not-a-jwt' }))).toBe(false);
  });
});
