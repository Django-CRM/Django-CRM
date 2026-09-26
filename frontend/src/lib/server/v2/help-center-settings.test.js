import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getHelpCenterSettings, saveErrorMessage, updateHelpCenterSettings } =
  await import('$lib/server/v2/help-center-settings.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

describe('help center settings', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads the org-scoped settings endpoint', async () => {
    apiRequest.mockResolvedValue({ help_center_enabled: false, can_edit: true });
    await getHelpCenterSettings(event);
    expect(apiRequest.mock.calls[0][0]).toBe('/org/help-center/');
  });

  it('PATCHes exactly the two fields, trimmed', async () => {
    apiRequest.mockResolvedValue({});
    await updateHelpCenterSettings(event, { enabled: true, slug: '  acme ' });
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/org/help-center/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ help_center_enabled: true, help_center_slug: 'acme' });
  });

  it('never forwards anything else the form carried', async () => {
    apiRequest.mockResolvedValue({});
    await updateHelpCenterSettings(
      event,
      /** @type {any} */ ({ enabled: false, slug: 'acme', org: 'attacker', is_active: false })
    );
    expect(Object.keys(apiRequest.mock.calls[0][1].body).sort()).toEqual([
      'help_center_enabled',
      'help_center_slug'
    ]);
  });
});

describe('saveErrorMessage', () => {
  const fallback = (/** @type {any} */ _err, /** @type {string} */ text) => text;

  it('reads the address rule the API broke, without the field name', () => {
    const err = { body: { help_center_slug: ['That address is already taken. Choose another.'] } };
    expect(saveErrorMessage(err, fallback)).toBe('That address is already taken. Choose another.');
  });

  it('falls back when the API explained nothing', () => {
    expect(saveErrorMessage(new Error('boom'), fallback)).toBe('Could not save the help center.');
  });
});
