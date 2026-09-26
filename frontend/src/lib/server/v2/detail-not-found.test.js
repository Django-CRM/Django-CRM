import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getLead } = await import('$lib/server/v2/leads.js');
const { getAccount } = await import('$lib/server/v2/accounts.js');
const { getContact } = await import('$lib/server/v2/contacts.js');
const { getDeal } = await import('$lib/server/v2/deals.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * The API answers a record this profile may not open exactly as one that does
 * not exist: 404, same body (owner decision for 1.11.0). The page cannot tell
 * the two apart, so its copy has to cover both, and no 403 branch is left to
 * suggest the record is real.
 */
describe.each([
  ['lead', getLead],
  ['account', getAccount],
  ['contact', getContact],
  ['deal', getDeal]
])('%s detail', (_kind, load) => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }));
  });

  it('turns a 404 into a page 404 whose copy covers no access', async () => {
    await expect(load(event, 'id-1')).rejects.toMatchObject({
      status: 404,
      body: { message: expect.stringContaining('do not have access') }
    });
    expect(apiRequest.mock.calls.some(([path]) => String(path).includes('id-1'))).toBe(true);
  });
});
