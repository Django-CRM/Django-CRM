/**
 * The emailed sign-in link must survive being fetched by a mail scanner.
 *
 * Microsoft Defender Safe Links, Cloudflare email security and similar
 * gateways GET (and HEAD) every link in an incoming message before the person
 * sees it. When opening the link spent the single-use token, the scanner
 * signed in and took the session cookies, and the person's own click landed on
 * "Link expired or invalid". Opening the page now only renders a button; the
 * token is spent by the form POST that button submits.
 *
 * This route module imports nothing from `$app/*`, so the standalone vitest
 * config can load it (see the note in `vitest.config.js`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('axios', () => ({ default: { post: vi.fn() } }));

const axios = (await import('axios')).default;
const { load, actions } = await import('./+page.server.js');

const getClientAddress = () => '198.51.100.7';

function jar() {
  return { set: vi.fn() };
}

/**
 * @param {string} query
 * @returns {Promise<any>}
 */
async function submit(query, cookies = jar()) {
  const url = new URL(`http://app.test/login/verify${query}`);
  const request = new Request(url, { method: 'POST', body: new FormData() });
  return actions.default(/** @type {any} */ ({ url, request, cookies, getClientAddress }));
}

beforeEach(() => {
  vi.mocked(axios.post).mockReset();
});

describe('opening the link', () => {
  it('does not spend the token', async () => {
    const result = await load(
      /** @type {any} */ ({ url: new URL('http://app.test/login/verify?token=t') })
    );
    expect(axios.post).not.toHaveBeenCalled();
    expect(result).toEqual({});
  });

  it('says so when the link carries no token', async () => {
    const result = await load(
      /** @type {any} */ ({ url: new URL('http://app.test/login/verify') })
    );
    expect(result).toEqual({ error: 'Missing verification token.' });
  });
});

describe('pressing the button', () => {
  it('spends the token, sets the session cookies and goes to /org', async () => {
    vi.mocked(axios.post).mockResolvedValue({ data: { access_token: 'a', refresh_token: 'r' } });
    const cookies = jar();
    const thrown = await submit('?token=t', cookies).catch((/** @type {any} */ e) => e);

    expect(thrown).toMatchObject({ status: 303, location: '/org' });
    const [url, body] = vi.mocked(axios.post).mock.calls[0];
    expect(url).toMatch(/\/api\/auth\/magic-link\/verify\/$/);
    expect(body).toEqual({ token: 't' });
    expect(cookies.set.mock.calls.map((/** @type {any[]} */ c) => c[0])).toEqual([
      'jwt_access',
      'jwt_refresh'
    ]);
  });

  it('refuses a request with no token without calling the API', async () => {
    const result = /** @type {any} */ (await submit(''));
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('Missing verification token.');
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("shows the API's reason for a spent or expired token and sets no cookies", async () => {
    vi.mocked(axios.post).mockRejectedValue({
      response: { status: 400, data: { error: 'Invalid or expired link' } }
    });
    const cookies = jar();
    const result = /** @type {any} */ (await submit('?token=t', cookies));
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('Invalid or expired link');
    expect(cookies.set).not.toHaveBeenCalled();
  });

  it('falls back to a generic sentence when the API is unreachable', async () => {
    vi.mocked(axios.post).mockRejectedValue(new Error('connect ECONNREFUSED'));
    const result = /** @type {any} */ (await submit('?token=t'));
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('Verification failed');
  });
});
