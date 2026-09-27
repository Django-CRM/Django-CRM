/**
 * The shell hands every page the org's timezone from the JWT's org claim, so
 * a form's "today" default (`todayIn`) is the org's day without a fetch.
 */
import { describe, it, expect, vi } from 'vitest';

// Every count and the terminology lookup fail; the shell tolerates that.
vi.mock('$lib/api-helpers.js', () => ({
  apiRequest: async () => {
    throw new Error('offline');
  }
}));

const { load } = await import('./+layout.server.js');

/** @param {any} orgSettings */
function event(orgSettings) {
  return /** @type {any} */ ({
    locals: { org: { name: 'Acme' }, org_settings: orgSettings, profile: {} },
    cookies: { get: () => 'token' }
  });
}

describe('the shell org', () => {
  it('carries the org timezone from the token claim', async () => {
    const shell = /** @type {any} */ (
      await load(event({ default_currency: 'INR', timezone: 'Asia/Kolkata' }))
    );
    expect(shell.org.timezone).toBe('Asia/Kolkata');
  });

  it('is UTC for a token minted before the claim existed', async () => {
    const shell = /** @type {any} */ (await load(event({ default_currency: 'USD' })));
    expect(shell.org.timezone).toBe('UTC');
  });
});
