import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const { load, actions } = (await import('./+page.server.js')) as any;
const { resetConnectedAppsAbsentMemo } = await import('#lib/server/v2/connected-apps.js');

const cookies = { get: () => 'token' } as any;
const failure = (status: number, body = {}) => Object.assign(new Error('x'), { status, body });

const APP = {
  id: 'a1',
  kind: 'example',
  name: 'Example app',
  scopes: ['leads:read'],
  subscriptions: 0,
  connected_at: '2026-10-01T00:00:00Z',
  connected_by: { id: 'p1', name: 'Ada' },
  last_used_at: null,
  last_failed_delivery: null
};
const LIST = {
  apps: [APP],
  catalogue: [
    { kind: 'example', name: 'Example app' },
    { kind: 'other', name: null }
  ],
  scopes: ['leads:read', 'leads:write']
};

function event(fields: Record<string, string | string[]> = {}) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    for (const v of Array.isArray(value) ? value : [value]) body.append(key, v);
  }
  return { request: new Request('http://test/x', { method: 'POST', body }), cookies } as any;
}

beforeEach(() => {
  apiRequest.mockReset();
  resetConnectedAppsAbsentMemo();
});

describe('load', () => {
  it('returns the apps, the catalogue and the offered scopes', async () => {
    apiRequest.mockResolvedValueOnce(LIST);
    expect(await load({ cookies })).toEqual({ forbidden: false, ...LIST });
  });

  it('answers a page 404 where the API answers 404, and remembers it', async () => {
    apiRequest.mockRejectedValueOnce(failure(404));
    await expect(load({ cookies })).rejects.toMatchObject({ status: 404 });
    await expect(load({ cookies })).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  it('says forbidden for a non-admin', async () => {
    apiRequest.mockRejectedValueOnce(failure(403));
    expect(await load({ cookies })).toEqual({ forbidden: true });
  });

  it('lets any other failure through to the error page', async () => {
    apiRequest.mockRejectedValueOnce(failure(500));
    await expect(load({ cookies })).rejects.toMatchObject({ status: 500 });
  });
});

describe('connect', () => {
  it('posts the kind and the ticked scopes and hands back the key once', async () => {
    apiRequest.mockResolvedValueOnce({ app: APP, key: 'k-123' });
    const out = await actions.connect(event({ kind: 'example', scopes: ['leads:read'] }));
    expect(apiRequest).toHaveBeenCalledWith(
      '/connected-apps/',
      { method: 'POST', body: { kind: 'example', scopes: ['leads:read'] } },
      { cookies }
    );
    expect(out).toEqual({ revealed: { how: 'connected', name: 'Example app', key: 'k-123' } });
  });

  it('sends a trimmed name, and refuses one that is empty or over 60 characters', async () => {
    apiRequest.mockResolvedValueOnce({ app: APP, key: 'k' });
    await actions.connect(event({ kind: 'other', name: '  Mine ', scopes: [] }));
    expect(apiRequest.mock.calls[0][1].body).toEqual({ kind: 'other', name: 'Mine', scopes: [] });
    for (const name of ['   ', 'x'.repeat(61)]) {
      const out = await actions.connect(event({ kind: 'other', name, scopes: [] }));
      expect(out.status).toBe(400);
    }
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  it("shows the server's message, such as the limit of 25", async () => {
    apiRequest.mockRejectedValueOnce(
      failure(400, { error: true, errors: 'An organization can have at most 25 connected apps.' })
    );
    const out = await actions.connect(event({ kind: 'example', scopes: ['leads:read'] }));
    expect(out.status).toBe(400);
    expect(out.data.error).toBe('An organization can have at most 25 connected apps.');
  });

  it('says only an admin can, on a 403', async () => {
    apiRequest.mockRejectedValueOnce(failure(403));
    const out = await actions.connect(event({ kind: 'example', scopes: [] }));
    expect(out.status).toBe(403);
    expect(out.data.error).toBe('Only an admin can manage connected apps.');
  });
});

describe('replace and disconnect', () => {
  it('replace returns the new key once', async () => {
    apiRequest.mockResolvedValueOnce({ app: APP, key: 'k-new' });
    const out = await actions.replace(event({ id: 'a1' }));
    expect(apiRequest).toHaveBeenCalledWith(
      '/connected-apps/a1/replace-key/',
      { method: 'POST' },
      { cookies }
    );
    expect(out.revealed).toEqual({ how: 'replaced', name: 'Example app', key: 'k-new' });
  });

  it('disconnect deletes by id', async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    expect(await actions.disconnect(event({ id: 'a1' }))).toEqual({ disconnected: 'a1' });
    expect(apiRequest).toHaveBeenCalledWith(
      '/connected-apps/a1/',
      { method: 'DELETE' },
      { cookies }
    );
  });

  it('refuses an id that is not a plain key, so no path can be built from it', async () => {
    for (const id of ['', '../x', 'a/b']) {
      expect((await actions.disconnect(event({ id }))).status).toBe(400);
      expect((await actions.replace(event({ id }))).status).toBe(400);
    }
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('keeps the real status, with a sensible sentence for an app already gone', async () => {
    apiRequest.mockRejectedValueOnce(failure(404));
    const gone = await actions.disconnect(event({ id: 'a1' }));
    expect(gone.status).toBe(404);
    expect(gone.data.error).toBe('That app is not connected any more.');
    apiRequest.mockRejectedValueOnce(failure(500));
    expect((await actions.replace(event({ id: 'a1' }))).status).toBe(500);
    apiRequest.mockRejectedValueOnce(new Error('offline'));
    expect((await actions.replace(event({ id: 'a1' }))).status).toBe(400);
  });

  it('reports a refusal from the API', async () => {
    apiRequest.mockRejectedValueOnce(failure(403));
    const out = await actions.disconnect(event({ id: 'a1' }));
    expect(out.status).toBe(403);
  });
});
