import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getMacros, createMacro, updateMacro, deleteMacro, activateMacro } =
  await import('$lib/server/v2/macros.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * `getMacros`'s `can_create_org` comes from `viewerIsAdmin`, which decodes the
 * `is_organization_admin` claim out of the `jwt_access` cookie directly (no
 * network call), so a plain `{ get: () => 'token' }` mock (as `event` above)
 * can't drive it: a `'token'` has no `.` to split, `viewerIsAdmin` catches
 * that and returns false. This builds a JWT-shaped string with a real
 * base64url payload carrying the claims the API signs, so the decode path
 * actually runs.
 *
 * @param {string} role
 */
function eventWithRole(role) {
  const payload = Buffer.from(
    JSON.stringify({ role, is_organization_admin: role === 'ADMIN' }),
    'utf-8'
  ).toString('base64url');
  const token = `h.${payload}.s`;
  return /** @type {any} */ ({
    cookies: { get: (/** @type {string} */ name) => (name === 'jwt_access' ? token : null) }
  });
}

describe('getMacros', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('can_create_org is true for an ADMIN token and false for a non-admin one', async () => {
    // The page's whole permission model rests on this one boolean: it is
    // what decides whether the scope select offers "Everyone in the org" and
    // whether `canWrite` treats an org macro as editable. `_resolve_scope_and_owner`
    // re-derives admin status independently server-side, but a display hint
    // that is wired backwards is still worth catching here.
    apiRequest.mockImplementation(async (/** @type {string} */ url) => {
      if (url === '/macros/') return { results: [], totals: {}, placeholders: [] };
      if (url === '/profile/') return { user_obj: { id: 'profile-1' } };
      throw new Error(`unexpected url: ${url}`);
    });

    const admin = await getMacros(eventWithRole('ADMIN'));
    expect(admin.can_create_org).toBe(true);

    const member = await getMacros(eventWithRole('USER'));
    expect(member.can_create_org).toBe(false);
  });

  it('my_profile_id is the id the page compares a macro owner against', async () => {
    // `canWrite` in the page does `m.owner?.id === data.my_profile_id` for a
    // personal macro. `getMacros` reshapes `owner` from the raw profile id
    // the API sends into `{ id, name }`; this proves that reshape keeps the
    // id in the same identity space `myProfileId` resolves, so a viewer's
    // own personal macro actually compares equal rather than silently never
    // matching.
    apiRequest.mockImplementation(async (/** @type {string} */ url) => {
      if (url === '/macros/') {
        return {
          results: [
            {
              id: 'm1',
              scope: 'personal',
              owner: 'profile-77',
              owner_name: 'a@example.com',
              is_active: true,
              usage_count: 0,
              unknown_placeholders: []
            },
            {
              id: 'm2',
              scope: 'org',
              owner: null,
              is_active: true,
              usage_count: 0,
              unknown_placeholders: []
            }
          ],
          totals: {},
          placeholders: []
        };
      }
      if (url === '/profile/') return { user_obj: { id: 'profile-77' } };
      throw new Error(`unexpected url: ${url}`);
    });

    const data = await getMacros(eventWithRole('USER'));

    expect(data.my_profile_id).toBe('profile-77');
    const mine = data.macros.find((/** @type {any} */ m) => m.id === 'm1');
    expect(mine.owner.id).toBe(data.my_profile_id);
  });

  it('my_profile_id falls back to an empty string, never a value that could collide with an owner id', async () => {
    apiRequest.mockImplementation(async (/** @type {string} */ url) => {
      if (url === '/macros/') return { results: [], totals: {}, placeholders: [] };
      if (url === '/profile/') throw new Error('502');
      throw new Error(`unexpected url: ${url}`);
    });

    const data = await getMacros(eventWithRole('USER'));
    expect(data.my_profile_id).toBe('');
  });
});

describe('createMacro', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('POSTs title, body, scope and is_active to /macros/', async () => {
    apiRequest.mockResolvedValue({ id: 'm1' });

    await createMacro(event, {
      title: '  Ask for logs  ',
      body: 'Could you attach the log file?',
      scope: 'personal',
      is_active: true
    });

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/macros/');
    expect(options.method).toBe('POST');
    expect(options.body).toEqual({
      title: 'Ask for logs',
      body: 'Could you attach the log file?',
      scope: 'personal',
      is_active: true
    });
  });

  it('never forwards owner or org, which the backend derives from the token', async () => {
    // `_resolve_scope_and_owner` sets owner from `request.profile`. A client
    // that could name an owner could file a macro as somebody else.
    apiRequest.mockResolvedValue({});

    await createMacro(event, {
      title: 't',
      body: 'b',
      scope: 'personal',
      owner: 'someone-else',
      org: 'attacker-org'
    });

    const body = apiRequest.mock.calls[0][1].body;
    expect(body.owner).toBeUndefined();
    expect(body.org).toBeUndefined();
  });

  it('rejects an unknown scope before making a request', async () => {
    await expect(createMacro(event, { title: 't', body: 'b', scope: 'global' })).rejects.toThrow(
      /scope/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects an empty title or body before making a request', async () => {
    await expect(createMacro(event, { title: '  ', body: 'b', scope: 'org' })).rejects.toThrow(
      /title/i
    );
    await expect(createMacro(event, { title: 't', body: '  ', scope: 'org' })).rejects.toThrow(
      /body/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('updateMacro', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PATCHes rather than PUTs, because PUT is not partial', async () => {
    // `MacroDetailView.put` runs the serializer with `partial=False` and
    // `MacroSerializer` requires title and body, so a PUT missing either
    // returns 400. PATCH is the partial verb.
    apiRequest.mockResolvedValue({});

    await updateMacro(event, 'm1', { title: 'New title', body: 'b', scope: 'personal' });

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/macros/m1/');
    expect(options.method).toBe('PATCH');
    expect(options.body.title).toBe('New title');
  });
});

describe('deleteMacro', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('DELETEs the detail endpoint', async () => {
    apiRequest.mockResolvedValue(null);
    await deleteMacro(event, 'm1');
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/macros/m1/');
    expect(options.method).toBe('DELETE');
  });

  it('refuses an empty id before making a request', async () => {
    await expect(deleteMacro(event, '')).rejects.toThrow(/which/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('activateMacro', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PATCHes the detail endpoint with only { is_active: true }', async () => {
    // The "Turn on" control must never resend `title`/`body`/`scope`: a
    // blank `title` slipping through here would blank a live macro's title.
    apiRequest.mockResolvedValue({});

    await activateMacro(event, 'm1');

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/macros/m1/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ is_active: true });
  });

  it('refuses an empty id before making a request', async () => {
    await expect(activateMacro(event, '')).rejects.toThrow(/which/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
