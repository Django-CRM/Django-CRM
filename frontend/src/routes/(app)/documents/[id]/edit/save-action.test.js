import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

// Imports only `@sveltejs/kit` and `$lib`, so the standalone vitest config can
// load it (see the note in `vitest.config.js`).
const { load, actions } = await import('./+page.server.js');

/**
 * `DocumentDetailView.put` replaces `shared_to` and `teams` with the body's
 * lists, keeping a deactivated person only when the body names them again;
 * `patch` never touches either list. So a save that changes sharing must
 * resubmit the shares the picker cannot show, and a save that leaves sharing
 * alone is a PATCH naming neither list, because when the people/teams list
 * failed to load (no checkboxes at all) a PUT would empty the team list.
 */

/** An unsigned JWT carrying the claims `viewerClaims` reads. */
function token(claims) {
  const part = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${part({ alg: 'none' })}.${part(claims)}.`;
}

const cookies = {
  get: (/** @type {string} */ k) =>
    k === 'jwt_access' ? token({ role: 'ADMIN', user_id: 'u1' }) : undefined
};

/** @param {[string, string | File][]} entries */
function saveEvent(entries) {
  const body = new FormData();
  for (const [key, value] of entries) body.append(key, value);
  return /** @type {any} */ ({
    request: new Request('http://test/documents/d1/edit?/save', { method: 'POST', body }),
    cookies,
    params: { id: 'd1' }
  });
}

/** Runs the action and returns the redirect it throws on success. */
async function save(entries) {
  try {
    return await actions.save(saveEvent(entries));
  } catch (/** @type {any} */ thrown) {
    return thrown;
  }
}

/** The form as drawn for a document shared with p1 (active) and team t1. */
const untouched = /** @type {[string, string][]} */ ([
  ['title', 'Signed contract'],
  ['status', 'active'],
  ['shared_to', 'p1'],
  ['teams', 't1'],
  ['shared_to_original', 'p1'],
  ['teams_original', 't1']
]);

describe('document edit save', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ error: false });
  });

  it('a rename with sharing untouched is a PATCH that names neither list', async () => {
    const result = await save([...untouched.filter(([k]) => k !== 'title'), ['title', 'Renamed']]);
    expect(result.status).toBe(303);
    expect(apiRequest).toHaveBeenCalledTimes(1);
    const [path, options] = apiRequest.mock.calls[0];
    expect(path).toBe('/documents/d1/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ title: 'Renamed', status: 'active' });
  });

  it('a failed people/teams load (no checkboxes, no originals) sends no empty list', async () => {
    const result = await save([
      ['title', 'Signed contract'],
      ['status', 'inactive']
    ]);
    expect(result.status).toBe(303);
    const [, options] = apiRequest.mock.calls[0];
    expect(options.method).toBe('PATCH');
    expect(options.body).not.toHaveProperty('shared_to');
    expect(options.body).not.toHaveProperty('teams');
    expect(options.body.status).toBe('inactive');
  });

  it('ticking somebody is a PUT carrying both lists', async () => {
    await save([...untouched, ['shared_to', 'p2']]);
    const [, options] = apiRequest.mock.calls[0];
    expect(options.method).toBe('PUT');
    expect(options.body.shared_to).toEqual(['p1', 'p2']);
    expect(options.body.teams).toEqual(['t1']);
  });

  it('a share with no checkbox rides along on a PUT, and alone changes nothing', async () => {
    // The page posts a deactivated person's share as both `shared_to` and
    // `shared_to_original` (`kept_shares`), so it survives a change of sharing
    // and does not by itself count as one.
    const kept = /** @type {[string, string][]} */ ([
      ['shared_to', 'p9'],
      ['shared_to_original', 'p9']
    ]);
    await save([...untouched, ...kept]);
    expect(apiRequest.mock.calls[0][1].method).toBe('PATCH');

    apiRequest.mockClear();
    await save([...untouched, ...kept, ['shared_to', 'p2']]);
    const [, options] = apiRequest.mock.calls[0];
    expect(options.method).toBe('PUT');
    expect(options.body.shared_to).toEqual(['p1', 'p9', 'p2']);
  });

  it('unticking the only team is a PUT with an explicit empty team list', async () => {
    await save(untouched.filter(([k, v]) => !(k === 'teams' && v === 't1')));
    const [, options] = apiRequest.mock.calls[0];
    expect(options.method).toBe('PUT');
    expect(options.body.shared_to).toEqual(['p1']);
    expect(options.body.teams).toEqual([]);
  });

  it('a rejected save echoes the ticked boxes back for the retry', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('x'), { status: 400, body: { errors: { title: ['taken'] } } })
    );
    const result = /** @type {any} */ (await save(untouched));
    expect(result.status).toBe(400);
    expect(result.data.values.shared_to).toEqual(['p1']);
    expect(result.data.values.teams).toEqual(['t1']);
  });

  describe('replacing the file', () => {
    const fetchMock = vi.fn();
    beforeEach(() => {
      fetchMock.mockReset();
      fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
      vi.stubGlobal('fetch', fetchMock);
    });
    afterEach(() => vi.unstubAllGlobals());

    const file = () => new File(['pdf'], 'contract.pdf', { type: 'application/pdf' });

    it('with sharing untouched is a multipart PATCH without either list', async () => {
      await save([...untouched, ['document_file', file()]]);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [, init] = fetchMock.mock.calls[0];
      expect(init.method).toBe('PATCH');
      expect(init.body.has('document_file')).toBe(true);
      expect(init.body.has('shared_to')).toBe(false);
      expect(init.body.has('teams')).toBe(false);
    });

    it('with sharing changed is a multipart PUT carrying both lists', async () => {
      await save([...untouched, ['shared_to', 'p2'], ['document_file', file()]]);
      const [, init] = fetchMock.mock.calls[0];
      expect(init.method).toBe('PUT');
      expect(JSON.parse(init.body.get('shared_to'))).toEqual(['p1', 'p2']);
      expect(JSON.parse(init.body.get('teams'))).toEqual(['t1']);
    });
  });
});

describe('document edit load', () => {
  // A block body: an arrow that returns the mock hands vitest a function,
  // which it then calls as a cleanup hook.
  beforeEach(() => {
    apiRequest.mockReset();
  });

  const doc = {
    doc_obj: {
      id: 'd1',
      title: 'Signed contract',
      status: 'active',
      document_file: 'docs/contract.pdf',
      shared_to: [{ id: 'p1', user_details: { name: 'Ada' } }],
      teams: [{ id: 't1', name: 'Support' }],
      created_by: { id: 'u1' }
    }
  };

  it('says the picker list failed, rather than passing it off as an empty org', async () => {
    apiRequest.mockImplementation((/** @type {string} */ path) => {
      if (path === '/documents/d1/') return Promise.resolve(doc);
      return Promise.reject(new Error('boom'));
    });
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies, params: { id: 'd1' } }))
    );
    expect(data.options_failed).toBe(true);
    expect(data.people).toEqual([]);
    // The stored shares still reach the page, so nothing reads as unshared.
    expect(data.document.shared_to).toEqual(['p1']);
    expect(data.document.teams).toEqual(['t1']);
  });

  it('names the shares the picker cannot offer, so the page can resubmit them', async () => {
    const shared = {
      doc_obj: {
        ...doc.doc_obj,
        shared_to: [
          { id: 'p1', user_details: { name: 'Ada' } },
          { id: 'p9', user_details: { name: 'Gone' } }
        ]
      }
    };
    apiRequest.mockImplementation((/** @type {string} */ path) =>
      Promise.resolve(path === '/documents/d1/' ? shared : { profiles: [{ id: 'p1' }], teams: [] })
    );
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies, params: { id: 'd1' } }))
    );
    expect(data.document.shared_to).toEqual(['p1', 'p9']);
    expect(data.document.kept_shares).toEqual(['p9']);
  });

  it('reports no failure when the list loads', async () => {
    apiRequest.mockImplementation((/** @type {string} */ path) =>
      Promise.resolve(path === '/documents/d1/' ? doc : { profiles: [], teams: [] })
    );
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies, params: { id: 'd1' } }))
    );
    expect(data.options_failed).toBe(false);
  });
});
