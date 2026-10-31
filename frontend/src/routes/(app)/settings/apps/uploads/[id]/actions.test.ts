import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const { load, actions } = (await import('./+page.server.js')) as any;
const { appliedTitle, noNewKeyLine } = await import('#lib/v2/private-apps.js');

const cookies = { get: () => 'token' } as any;
const failure = (status: number, body = {}) => Object.assign(new Error('x'), { status, body });

const PREVIEW = {
  mode: 'install',
  action: 'install',
  blocked: [],
  items: [],
  objects: { needed: 1, included: 3, used: 0 },
  access: { scopes: ['leads:read'], added: [], removed: [] },
  sample_data: { offered: false, default_on: false, records: 0 }
};
const UPLOAD = {
  id: 'u1',
  app_id: 'crm-sync',
  name: 'CRM sync',
  description: 'Syncs things',
  version: 2
};

function event(fields: Record<string, string> = {}, id = 'u1') {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return {
    request: new Request('http://test/x', { method: 'POST', body }),
    params: { id },
    cookies
  } as any;
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('load', () => {
  it('returns the preview and the installed version of the app', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/private-apps/uploads/u1/') return { upload: UPLOAD, preview: PREVIEW };
      if (path === '/private-apps/') return { apps: [{ id: 'crm-sync', version: 1 }] };
      throw failure(404);
    });
    const data = await load(event());
    expect(data.upload).toEqual(UPLOAD);
    expect(data.preview).toEqual(PREVIEW);
    expect(data.installedVersion).toBe(1);
  });

  it('shows the detail of a 409 instead of an error page', async () => {
    apiRequest.mockRejectedValueOnce(failure(409, { detail: 'Version 2 is already installed.' }));
    expect(await load(event())).toEqual({ stale: 'Version 2 is already installed.' });
  });

  it('is a 404 for a malformed or unknown id', async () => {
    await expect(load(event({}, '../x'))).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
    apiRequest.mockRejectedValueOnce(failure(404));
    await expect(load(event())).rejects.toMatchObject({ status: 404 });
  });
});

describe('apply', () => {
  it('reports an upgrade that keeps its key as no new key, with access', async () => {
    apiRequest.mockResolvedValueOnce({ app: { id: 'a', name: 'A', version: 3 }, key: null });
    const out = await actions.apply(
      event({ asks_access: '1', had_access: '1', trust: 'on', mode: 'upgrade' })
    );
    expect(out.applied).toMatchObject({
      key: null,
      mode: 'upgrade',
      asksAccess: true,
      hadAccess: true
    });
    const { mode, asksAccess, hadAccess } = out.applied;
    expect(noNewKeyLine(mode, asksAccess, hadAccess)).toContain('existing key keeps working');
    expect(appliedTitle(mode)).toBe('Updated');
  });

  it('says nothing about a key after an update that never had access', async () => {
    apiRequest.mockResolvedValueOnce({ app: { id: 'a', name: 'A', version: 2 }, key: null });
    const out = await actions.apply(event({ mode: 'upgrade' }));
    expect(out.applied).toMatchObject({ mode: 'upgrade', asksAccess: false, hadAccess: false });
    const { mode, asksAccess, hadAccess } = out.applied;
    expect(noNewKeyLine(mode, asksAccess, hadAccess)).toBe('');
  });

  it('reports an update that dropped all access as a stopped key', async () => {
    apiRequest.mockResolvedValueOnce({ app: { id: 'a', name: 'A', version: 2 }, key: null });
    const out = await actions.apply(event({ mode: 'upgrade', had_access: '1' }));
    const { mode, asksAccess, hadAccess } = out.applied;
    expect(noNewKeyLine(mode, asksAccess, hadAccess)).toContain('stopped working');
  });

  it('is a 400 without the checkbox when access is asked, and calls nothing', async () => {
    const out = await actions.apply(event({ asks_access: '1' }));
    expect(out.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('passes trust true when ticked and hands back the key once', async () => {
    apiRequest.mockResolvedValueOnce({
      app: { id: 'crm-sync', name: 'CRM sync', version: 2 },
      key: 'k-1'
    });
    const out = await actions.apply(event({ asks_access: '1', trust: 'on' }));
    expect(apiRequest).toHaveBeenCalledWith(
      '/private-apps/uploads/u1/apply/',
      expect.objectContaining({ method: 'POST', body: { trust: true } }),
      { cookies }
    );
    expect(out).toEqual({
      applied: {
        name: 'CRM sync',
        version: 2,
        key: 'k-1',
        mode: 'install',
        asksAccess: true,
        hadAccess: false
      }
    });
    expect(appliedTitle(out.applied.mode)).toBe('Installed');
  });

  it('applies with no access and no checkbox, trust false', async () => {
    apiRequest.mockResolvedValueOnce({ app: { id: 'a', name: 'A', version: 1 }, key: null });
    const out = await actions.apply(event());
    expect(apiRequest.mock.calls[0][1].body).toEqual({ trust: false });
    expect(out.applied.key).toBeNull();
  });

  it('shows a blocked install as a 409 with the reason', async () => {
    apiRequest.mockRejectedValueOnce(failure(409, { detail: 'Blocked.' }));
    const out = await actions.apply(event());
    expect(out.status).toBe(409);
    expect(out.data.error).toBe('Blocked.');
  });

  it('refuses a malformed upload id before any request', async () => {
    const out = await actions.apply(event({}, 'a/b'));
    expect(out.status).toBe(404);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
