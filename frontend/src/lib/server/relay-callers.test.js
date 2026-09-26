/**
 * Every server-side call to an anonymous API endpoint that records the
 * caller's address sends the signed visitor address (`relayHeaders`), and
 * nothing else about the visitor's own headers.
 *
 * Route modules are imported by path: these import nothing the harness
 * cannot resolve (see vitest.config.js). Token refresh and the shell's own org
 * switch live in hooks.server.js and are tested in hooks.server.test.js.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('axios', () => ({ default: { post: vi.fn(), get: vi.fn() } }));
vi.mock('$lib/server/packs.js', () => ({ listPacks: vi.fn(), applyPack: vi.fn() }));

const axios = (await import('axios')).default;
const { env: privateEnv } = await import('$env/dynamic/private');
const { requestLogin, verifyLogin } = await import('$lib/server/portal.js');
const login = await import('../../routes/(no-layout)/login/+page.server.js');
const verify = await import('../../routes/(no-layout)/login/verify/+page.server.js');
const logout = await import('../../routes/(no-layout)/logout/+page.server.js');
const selectOrg = await import('../../routes/(no-layout)/org/+page.server.js');
const newOrg = await import('../../routes/(no-layout)/org/new/+page.server.js');

const SECRET = 'r'.repeat(48);
const VISITOR = '198.51.100.7';
const SIGNED = {
  'X-Forwarded-For': VISITOR,
  'X-BottleCRM-Relay-Secret': SECRET,
  'X-BottleCRM-Client-IP': VISITOR
};
const getClientAddress = () => VISITOR;

const cookies = () => ({ get: vi.fn(() => 'refresh'), set: vi.fn(), delete: vi.fn() });

/** @param {() => unknown} run */
async function ignoringRedirect(run) {
  try {
    await run();
  } catch (/** @type {any} */ err) {
    if (!err?.status || err.status < 300 || err.status >= 400) throw err;
  }
}

const fetchMock = vi.fn();

beforeEach(() => {
  privateEnv.RELAY_SECRET = SECRET;
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(axios.post).mockReset();
  vi.mocked(axios.post).mockResolvedValue({ data: { access_token: 'a', refresh_token: 'r' } });
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete privateEnv.RELAY_SECRET;
});

describe('magic-link request (records the address on the token)', () => {
  const submit = () => {
    const body = new FormData();
    body.set('email', 'ada@example.com');
    const request = new Request('http://app.test/login', { method: 'POST', body });
    return login.actions.default(/** @type {any} */ ({ request, getClientAddress }));
  };

  it('sends the signed visitor address', async () => {
    await submit();
    const [url, , config] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/magic-link\/request\/$/);
    expect(config?.headers).toMatchObject(SIGNED);
  });

  it('sends only the unsigned, pre-1.12 address without a secret', async () => {
    delete privateEnv.RELAY_SECRET;
    await submit();
    expect(vi.mocked(axios.post).mock.calls[0][2]?.headers).toEqual({
      'Content-Type': 'application/json',
      'X-Forwarded-For': VISITOR
    });
  });
});

describe('magic-link verify (the sign-in audit row records the address)', () => {
  it('sends the signed visitor address', async () => {
    await ignoringRedirect(() =>
      verify.load(
        /** @type {any} */ ({
          url: new URL('http://app.test/login/verify?token=t'),
          cookies: cookies(),
          getClientAddress
        })
      )
    );
    const [url, , config] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/magic-link\/verify\/$/);
    expect(config?.headers).toMatchObject(SIGNED);
  });
});

describe('logout (the audit row records the address)', () => {
  it('sends the signed visitor address', async () => {
    await ignoringRedirect(() =>
      logout.load(
        /** @type {any} */ ({ locals: {}, cookies: cookies(), fetch: fetchMock, getClientAddress })
      )
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/logout\/$/);
    expect(init.headers).toMatchObject(SIGNED);
  });
});

describe('portal sign-in (records the address on the token)', () => {
  it('sends the signed visitor address with the request', async () => {
    await requestLogin('org-1', 'ada@example.com', { getClientAddress });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/portal\/login\/org-1\/request\/$/);
    expect(init.headers).toMatchObject(SIGNED);
  });

  it('sends only the unsigned, pre-1.12 address without a secret', async () => {
    delete privateEnv.RELAY_SECRET;
    await requestLogin('org-1', 'ada@example.com', { getClientAddress });
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
  /** @param {Record<string, string>} fields */
  const form = (fields) => {
    const body = new FormData();
    for (const [k, v] of Object.entries(fields)) body.set(k, v);
    return new Request('http://app.test/org', { method: 'POST', body });
  };
  /** @param {string} path */
  const callTo = (path) =>
    vi.mocked(axios.post).mock.calls.find(([url]) => String(url).endsWith(path));

  it('sends the signed visitor address when picking an org', async () => {
    await ignoringRedirect(() =>
      selectOrg.actions.selectOrg(
        /** @type {any} */ ({
          request: form({ org_id: ORG }),
          cookies: cookies(),
          getClientAddress
        })
      )
    );
    const [, , config] = /** @type {any[]} */ (callTo('/api/auth/switch-org/'));
    expect(config.headers).toMatchObject(SIGNED);
  });

  it('sends it on the switch that applies a pack to a new org', async () => {
    vi.mocked(axios.post)
      .mockResolvedValueOnce({ data: { org: { id: ORG } } })
      .mockResolvedValueOnce({ data: { access_token: 'a' } });
    await ignoringRedirect(() =>
      newOrg.actions.default(
        /** @type {any} */ ({
          request: form({ org_name: 'Acme', vertical: 'agency' }),
          cookies: cookies(),
          locals: { user: { id: 'u' } },
          getClientAddress
        })
      )
    );
    const [, , config] = /** @type {any[]} */ (callTo('/api/auth/switch-org/'));
    expect(config.headers).toMatchObject(SIGNED);
  });
});
