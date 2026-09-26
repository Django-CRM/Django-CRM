/**
 * The shell's token refresh and org switch send the visitor's signed address
 * (`$lib/server/relay.js`): the API writes an audit row for each, and without
 * it the row records this server.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@sentry/sveltekit', () => ({
  sentryHandle:
    () =>
    (/** @type {any} */ { event, resolve }) =>
      resolve(event),
  handleErrorWithSentry: () => () => {}
}));
// `sequence` needs SvelteKit's request store, which only a running server has.
// Sentry's handle is a pass-through here, so the app's own handle is the last.
vi.mock('@sveltejs/kit/hooks', () => ({
  sequence: (/** @type {any[]} */ ...handles) => handles[handles.length - 1]
}));
vi.mock('axios', () => ({ default: { post: vi.fn(), get: vi.fn() } }));

const axios = (await import('axios')).default;
const { env: privateEnv } = await import('$env/dynamic/private');
const { handle } = await import('./hooks.server.js');

const SECRET = 'r'.repeat(48);
const VISITOR = '198.51.100.7';
const SIGNED = {
  'X-Forwarded-For': VISITOR,
  'X-BottleCRM-Relay-Secret': SECRET,
  'X-BottleCRM-Client-IP': VISITOR
};
const ORG = '11111111-2222-3333-4444-555555555555';

/** @param {Record<string, unknown>} claims */
const jwt = (claims) =>
  ['h', Buffer.from(JSON.stringify(claims)).toString('base64url'), 's'].join('.');
const hourFromNow = () => Math.floor(Date.now() / 1000) + 3600;

/** @param {Record<string, string>} jar */
function event(jar) {
  return /** @type {any} */ ({
    url: new URL('http://app.test/login'),
    request: new Request('http://app.test/login'),
    route: { id: '/(no-layout)/login' },
    locals: {},
    cookies: {
      get: (/** @type {string} */ name) => jar[name],
      set: vi.fn(),
      delete: vi.fn()
    },
    getClientAddress: () => VISITOR
  });
}

const resolve = vi.fn(async () => new Response('ok'));

/** @param {string} path */
const callTo = (path) =>
  vi.mocked(axios.post).mock.calls.find(([url]) => String(url).endsWith(path));

beforeEach(() => {
  privateEnv.RELAY_SECRET = SECRET;
  vi.mocked(axios.post).mockReset();
});

afterEach(() => {
  delete privateEnv.RELAY_SECRET;
});

describe('hooks.server.js relays the visitor', () => {
  it('on a token refresh', async () => {
    vi.mocked(axios.post).mockResolvedValue({
      data: { access: jwt({ user_id: 'u', exp: hourFromNow() }) }
    });

    await handle({
      event: event({ jwt_access: jwt({ user_id: 'u', exp: 1 }), jwt_refresh: 'r-1' }),
      resolve
    });

    const [, body, config] = /** @type {any[]} */ (callTo('/auth/refresh-token/'));
    expect(body).toEqual({ refresh: 'r-1' });
    expect(config.headers).toEqual(SIGNED);
  });

  it('on an org switch', async () => {
    vi.mocked(axios.post).mockResolvedValue({
      data: {
        access_token: jwt({ user_id: 'u', org_id: ORG, exp: hourFromNow() }),
        refresh_token: 'r-2',
        current_org: { id: ORG, name: 'Acme' }
      }
    });

    await handle({
      event: event({
        jwt_access: jwt({ user_id: 'u', exp: hourFromNow() }),
        jwt_refresh: 'r-1',
        org: ORG
      }),
      resolve
    });

    const [, , config] = /** @type {any[]} */ (callTo('/auth/switch-org/'));
    expect(config.headers).toMatchObject(SIGNED);
    expect(config.headers.Authorization).toMatch(/^Bearer /);
  });

  it('sends only the pre-1.12 X-Forwarded-For without a secret', async () => {
    delete privateEnv.RELAY_SECRET;
    vi.mocked(axios.post).mockResolvedValue({ data: {} });

    await handle({
      event: event({ jwt_access: jwt({ user_id: 'u', exp: 1 }), jwt_refresh: 'r-3' }),
      resolve
    });

    expect(/** @type {any[]} */ (callTo('/auth/refresh-token/'))[2].headers).toEqual({
      'X-Forwarded-For': VISITOR
    });
  });
});
