import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { canonicalSlug, getHelpArticle, getHelpCenter, listAllArticles, pageNumber, sitemapXml } =
  await import('$lib/server/help-center.js');
const { env: privateEnv } = await import('$env/dynamic/private');

const SECRET = 'r'.repeat(48);

/** @param {Record<string, string>} [headers] */
function event(headers = {}) {
  return /** @type {any} */ ({
    request: new Request('http://app.test/help-center/acme', { headers }),
    getClientAddress: () => '198.51.100.7'
  });
}

/** @param {number} status @param {any} body */
function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete privateEnv.RELAY_SECRET;
});

describe('canonicalSlug', () => {
  const url = (/** @type {string} */ path) => new URL(`http://app.test${path}`);

  it('passes a canonical slug through', () => {
    expect(canonicalSlug('acme-help', url('/help-center/acme-help'))).toBe('acme-help');
  });

  it('redirects a mixed-case slug to lowercase, keeping the rest of the URL', () => {
    try {
      canonicalSlug('Acme', url('/help-center/Acme/articles/x?q=1'));
      expect.unreachable();
    } catch (/** @type {any} */ err) {
      expect(err.status).toBe(301);
      expect(err.location).toBe('/help-center/acme/articles/x?q=1');
    }
  });

  it.each(['ab', '-acme', 'acme-', 'ac--me', 'ac_me', 'a'.repeat(51)])(
    '404s an address that could never exist: %s',
    (slug) => {
      try {
        canonicalSlug(slug, url(`/help-center/${slug}`));
        expect.unreachable();
      } catch (/** @type {any} */ err) {
        expect(err.status).toBe(404);
      }
    }
  );
});

describe('API calls', () => {
  it('sends no credential and the signed visitor address', async () => {
    privateEnv.RELAY_SECRET = SECRET;
    fetchMock.mockResolvedValue(
      reply(200, { help_center: { name: 'Acme' }, articles: [], articles_count: 0 })
    );
    await getHelpCenter(event({ cookie: 'jwt_access=staff' }), 'acme', { q: 'reset', page: 2 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://localhost:8000/api/public/help/acme/?limit=20&offset=20&q=reset');
    expect(init.headers.Authorization).toBeUndefined();
    expect(init.headers['X-BottleCRM-Client-IP']).toBe('198.51.100.7');
    expect(init.headers['X-BottleCRM-Relay-Secret']).toBe(SECRET);
  });

  it('never passes on the incoming forwarded chain, which the visitor writes', async () => {
    privateEnv.RELAY_SECRET = SECRET;
    fetchMock.mockResolvedValue(reply(200, { articles: [] }));
    await getHelpCenter(event({ 'x-forwarded-for': '203.0.113.9' }), 'acme');
    const { headers } = fetchMock.mock.calls[0][1];
    // The visitor's address as this server saw it, not the header they sent.
    expect(headers['X-Forwarded-For']).toBe('198.51.100.7');
    expect(headers['X-BottleCRM-Client-IP']).toBe('198.51.100.7');
  });

  it('sends only the pre-1.12 X-Forwarded-For without a relay secret', async () => {
    fetchMock.mockResolvedValue(reply(200, { articles: [] }));
    await getHelpCenter(event({ 'x-forwarded-for': '203.0.113.9' }), 'acme');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({
      Accept: 'application/json',
      'X-Forwarded-For': '198.51.100.7'
    });
  });

  it('turns the API 404 into a page 404', async () => {
    fetchMock.mockResolvedValue(reply(404, { error: 'Not found' }));
    await expect(getHelpCenter(event(), 'acme')).rejects.toMatchObject({ status: 404 });
  });

  it('404s a malformed article id without calling the API', async () => {
    await expect(getHelpArticle(event(), 'acme', 'not-a-uuid')).rejects.toMatchObject({
      status: 404
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('surfaces a throttled request as 429', async () => {
    fetchMock.mockResolvedValue(reply(429, {}));
    await expect(getHelpCenter(event(), 'acme')).rejects.toMatchObject({ status: 429 });
  });

  it('walks every page for the sitemap and stops at the count', async () => {
    const page = (/** @type {number} */ n, /** @type {number} */ start) =>
      Array.from({ length: n }, (_, i) => ({
        id: `id-${start + i}`,
        updated_at: '2026-09-01T00:00:00Z'
      }));
    fetchMock
      .mockResolvedValueOnce(reply(200, { articles: page(100, 0), articles_count: 150 }))
      .mockResolvedValueOnce(reply(200, { articles: page(50, 100), articles_count: 150 }));
    const all = await listAllArticles(event(), 'acme');
    expect(all).toHaveLength(150);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toContain('limit=100&offset=100');
  });
});

describe('pageNumber', () => {
  it.each([
    [null, 1],
    ['3', 3],
    ['0', 1],
    ['-2', 1],
    ['1.5', 1],
    ['abc', 1]
  ])('%s -> %s', (raw, expected) => {
    expect(pageNumber(raw)).toBe(expected);
  });
});

describe('sitemapXml', () => {
  it('lists the index and each article with its date', () => {
    const xml = sitemapXml('https://app.example.com', 'acme', [
      { id: 'a1', updated_at: '2026-09-01T10:00:00Z' }
    ]);
    expect(xml).toContain('<loc>https://app.example.com/help-center/acme</loc>');
    expect(xml).toContain(
      '<loc>https://app.example.com/help-center/acme/articles/a1</loc><lastmod>2026-09-01</lastmod>'
    );
  });

  it('escapes markup characters', () => {
    const xml = sitemapXml('https://a.test', 'acme', [{ id: '<x>&' }]);
    expect(xml).toContain('/articles/&lt;x&gt;&amp;</loc>');
  });
});
