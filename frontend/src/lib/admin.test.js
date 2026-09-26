import { describe, expect, it } from 'vitest';

import { isOrgAdmin } from './admin.js';

describe('isOrgAdmin', () => {
  it('is true only when the server says so', () => {
    expect(isOrgAdmin({ is_organization_admin: true })).toBe(true);
    expect(isOrgAdmin({ is_organization_admin: false })).toBe(false);
  });

  it('admits a USER-role superuser, whose token carries the fact', () => {
    expect(isOrgAdmin({ role: 'USER', is_organization_admin: true })).toBe(true);
  });

  it('never reads the role', () => {
    expect(isOrgAdmin({ role: 'ADMIN' })).toBe(false);
    expect(isOrgAdmin({ role: 'ADMIN', is_organization_admin: false })).toBe(false);
  });

  it('refuses anything that is not literally true', () => {
    for (const value of ['true', 1, 'yes', {}, null, undefined]) {
      expect(isOrgAdmin({ is_organization_admin: value })).toBe(false);
    }
  });

  it('refuses a missing source', () => {
    expect(isOrgAdmin(null)).toBe(false);
    expect(isOrgAdmin(undefined)).toBe(false);
  });
});
