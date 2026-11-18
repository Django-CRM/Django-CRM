/**
 * Every server-side call to an anonymous API endpoint that records the
 * caller's address sends the signed visitor address (`relayHeaders`), and
 * nothing else about the visitor's own headers.
 *
 * Route modules are imported by path: these import nothing the harness
 * cannot resolve (see vitest.config.js). Token refresh and the shell's own org
 * switch live in hooks.server.ts and are tested in hooks.server.test.ts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('axios', () => ({ default: { post: vi.fn(), get: vi.fn() } }));
vi.mock('#lib/server/packs.js', () => ({ listPacks: vi.fn(), applyPack: vi.fn() }));
vi.mock('#lib/server/v2/apps.js', () => ({ installApp: vi.fn() }));

const axios = (await import('axios')).default;
// The stub that vitest.config.js aliases `$app/env/private` to, so this is the module the code
// under test reads. Imported by path because only the stub has a setter.
const { setRelaySecret } = await import('../../../test/stubs/app-env-private.js');
const { requestLogin, verifyLogin } = await import('#lib/server/portal.js');
const login = await import('../../routes/(no-layout)/login/+page.server.js');
const verify = await import('../../routes/(no-layout)/login/verify/+page.server.js');
const logout = await import('../../routes/(no-layout)/logout/+page.server.js');
const selectOrg = await import('../../routes/(no-layout)/org/+page.server.js');
const newOrg = await import('../../routes/(no-layout)/org/new/+page.server.js');

const SECRET = 'r'.repeat(48);
const VISITOR = '198.51.100.7';
const BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/142.0 Safari/537.36';
const SIGNED = {
  'X-Forwarded-For': VISITOR,
  'X-BottleCRM-Relay-Secret': SECRET,
  'X-BottleCRM-Client-IP': VISITOR,
  'X-BottleCRM-User-Agent': BROWSER
};
const getClientAddress = () => VISITOR;
/** The visitor's own request, as SvelteKit hands it to a load or action. */
const visit = (url = 'http://app.test/', init = {}) =>
  new Request(url, { ...init, headers: { 'user-agent': BROWSER } });

const cookies = () => ({ get: vi.fn(() => 'refresh'), set: vi.fn(), delete: vi.fn() });

async function ignoringRedirect(run: () => unknown) {
  try {
    await run();
  } catch (err: any) {
    if (!err?.status || err.status < 300 || err.status >= 400) throw err;
  }
}

const fetchMock = vi.fn();

beforeEach(() => {
  setRelaySecret(SECRET);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(axios.post).mockReset();
  vi.mocked(axios.post).mockResolvedValue({ data: { access_token: 'a', refresh_token: 'r' } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setRelaySecret(undefined);
});

describe('password sign-in request (records the address on the OTP)', () => {
  const submit = () => {
    const body = new FormData();
    body.set('email', 'ada@example.com');
    body.set('password', 'Correct-Horse-Battery-42!');
    const request = visit('http://app.test/login', { method: 'POST', body });
    return login.actions.default({ request, getClientAddress } as any);
  };

  it('sends the signed visitor address', async () => {
    await submit();
    const [url, , config] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/magic-link\/request\/$/);
    expect(config?.headers).toMatchObject(SIGNED);
  });

  it('sends only the unsigned, pre-1.12 address without a secret', async () => {
    setRelaySecret(undefined);
    await submit();
    expect(vi.mocked(axios.post).mock.calls[0][2]?.headers).toEqual({
      'Content-Type': 'application/json',
      'X-Forwarded-For': VISITOR
    });
  });
});

describe('magic-link verify (the sign-in audit row records the address)', () => {
  it('sends the signed visitor address', async () => {
    const link = new URL('http://app.test/login/verify?token=t');
    await ignoringRedirect(() =>
      verify.actions.default({
        url: link,
        request: visit(link.href, { method: 'POST', body: new FormData() }),
        cookies: cookies(),
        getClientAddress
      } as any)
    );
    const [url, , config] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/magic-link\/verify\/$/);
    expect(config?.headers).toMatchObject(SIGNED);
  });
});

describe('Google sign-in (the audit row, and the per-address cap on failure rows)', () => {
  it('sends the signed visitor address', async () => {
    const jar = {
      get: vi.fn((name: string) => (name === 'oauth_state' ? 's' : 'verifier')),
      set: vi.fn(),
      delete: vi.fn()
    };
    await ignoringRedirect(() =>
      login.load({
        url: new URL('http://app.test/login?code=c&state=s'),
        request: visit('http://app.test/login?code=c&state=s'),
        cookies: jar,
        getClientAddress
      } as any)
    );
    const [url, , config] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/google\/callback\/$/);
    expect(config?.headers).toMatchObject(SIGNED);
  });
});

describe('logout (the audit row records the address)', () => {
  it('sends the signed visitor address', async () => {
    await ignoringRedirect(() =>
      logout.load({
        locals: {},
        cookies: cookies(),
        fetch: fetchMock,
        getClientAddress,
        request: visit('http://app.test/logout')
      } as any)
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/logout\/$/);
    expect(init.headers).toMatchObject(SIGNED);
  });
});

describe('portal sign-in (records the address on the token)', () => {
  it('sends the signed visitor address with the request', async () => {
    await requestLogin('org-1', 'ada@example.com', { getClientAddress, request: visit() });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/portal\/login\/org-1\/request\/$/);
    expect(init.headers).toMatchObject(SIGNED);
  });

  it('sends only the unsigned, pre-1.12 address without a secret', async () => {
    setRelaySecret(undefined);
    await requestLogin('org-1', 'ada@example.com', { getClientAddress, request: visit() });
    expect(fetchMock.mock.calls[0][1].headers).toEqual({
      'Content-Type': 'application/json',
      'X-Forwarded-For': VISITOR
    });
  });

  it('does not add it where nothing records it (verify)', async () => {
    await verifyLogin('org-1', 'ada@example.com', '123456');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
  });
});

describe('org switch (the audit row records the address)', () => {
  const ORG = '11111111-2222-3333-4444-555555555555';
  const form = (fields: Record<string, string>) => {
    const body = new FormData();
    for (const [k, v] of Object.entries(fields)) body.set(k, v);
    return visit('http://app.test/org', { method: 'POST', body });
  };
  const callTo = (path: string) =>
    vi.mocked(axios.post).mock.calls.find(([url]) => String(url).endsWith(path));

  it('sends the signed visitor address when picking an org', async () => {
    await ignoringRedirect(() =>
      selectOrg.actions.selectOrg({
        request: form({ org_id: ORG }),
        cookies: cookies(),
        getClientAddress
      } as any)
    );
    const [, , config] = callTo('/api/auth/switch-org/') as any[];
    expect(config.headers).toMatchObject(SIGNED);
  });

  it('sends it on the switch that applies a pack to a new org', async () => {
    vi.mocked(axios.post)
      .mockResolvedValueOnce({ data: { org: { id: ORG } } })
      .mockResolvedValueOnce({ data: { access_token: 'a' } });
    await ignoringRedirect(() =>
      newOrg.actions.default({
        request: form({ org_name: 'Acme', vertical: 'agency' }),
        cookies: cookies(),
        locals: { user: { id: 'u' } },
        getClientAddress
      } as any)
    );
    const [, , config] = callTo('/api/auth/switch-org/') as any[];
    expect(config.headers).toMatchObject(SIGNED);
  });
});
