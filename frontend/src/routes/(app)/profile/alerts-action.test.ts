import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const { actions } = await import('./+page.server.js');

function alertsEvent(fields: string[]) {
  const body = new FormData();
  for (const name of fields) body.set(name, 'on');
  return {
    request: new Request('http://test/profile?/alerts', { method: 'POST', body }),
    cookies: { get: () => 'token' }
  } as any;
}

describe('profile alerts action', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('sends both switches, an unchecked one as an explicit false', async () => {
    apiRequest.mockResolvedValue({});
    await actions.alerts(alertsEvent(['lead_alerts_push']));
    expect(apiRequest.mock.calls[0][0]).toBe('/profile/');
    expect(apiRequest.mock.calls[0][1]).toMatchObject({ method: 'PATCH' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({
      lead_alerts_email: false,
      lead_alerts_push: true
    });
  });

  it('sends false for both when nothing is checked', async () => {
    apiRequest.mockResolvedValue({});
    await actions.alerts(alertsEvent([]));
    expect(apiRequest.mock.calls[0][1].body).toEqual({
      lead_alerts_email: false,
      lead_alerts_push: false
    });
  });

  it('reports a refused save', async () => {
    apiRequest.mockRejectedValue(new Error('boom'));
    const result = await actions.alerts(alertsEvent([]));
    expect(result).toMatchObject({ status: 500, data: { scope: 'alerts' } });
  });
});
