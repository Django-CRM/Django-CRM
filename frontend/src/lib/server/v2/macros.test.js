import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const {
  getMacros,
  createMacro,
  updateMacro,
  deleteMacro,
  activateMacro,
  macroActionChips,
  optionsWithStored,
  keptActions,
  applySummary,
  applyMacro,
  listUsableMacros,
  MACRO_STATUSES
} = await import('$lib/server/v2/macros.js');

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

  it('accepts an empty body when the macro carries an action', async () => {
    apiRequest.mockResolvedValue({});
    await createMacro(event, { title: 't', body: '', scope: 'personal', set_status: 'Closed' });
    expect(apiRequest.mock.calls[0][1].body).toMatchObject({ body: '', set_status: 'Closed' });
  });

  it('forwards the four actions as the serializer names them', async () => {
    apiRequest.mockResolvedValue({});
    await createMacro(event, {
      title: 't',
      body: 'b',
      scope: 'org',
      set_status: 'Pending',
      set_priority: 'High',
      set_assignees: ['p1'],
      add_tags: ['t1']
    });
    expect(apiRequest.mock.calls[0][1].body).toEqual({
      title: 't',
      body: 'b',
      scope: 'org',
      set_status: 'Pending',
      set_priority: 'High',
      set_assignees: ['p1'],
      add_tags: ['t1']
    });
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

describe('macro actions', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('offers every status but Duplicate, which only a merge sets', () => {
    expect(MACRO_STATUSES).not.toContain('Duplicate');
    expect(MACRO_STATUSES).toContain('Closed');
  });

  it('builds one chip per action, in order, naming deactivated and archived rows', () => {
    expect(
      macroActionChips({
        set_status: 'Closed',
        set_priority: '',
        set_assignees_details: [
          { id: 'a', name: 'Ann', email: 'ann@x.test', is_active: true },
          { id: 'b', name: '', email: 'bo@x.test', is_active: false }
        ],
        add_tags_details: [{ id: 't', name: 'vip', is_active: false }]
      })
    ).toEqual([
      { key: 'status', label: 'Status: Closed' },
      { key: 'assignees', label: 'Assign: Ann, bo@x.test (deactivated)' },
      { key: 'tags', label: 'Tag: vip (archived)' }
    ]);
    expect(macroActionChips({})).toEqual([]);
  });

  it('keeps a stored row the active list no longer offers, so a save cannot drop it', () => {
    const options = optionsWithStored(
      [{ id: 'a', name: 'Ann' }],
      [
        { id: 'a', name: 'Ann', is_active: true },
        { id: 'b', name: 'Bo', is_active: false }
      ],
      (row) => `${row.name}!`
    );
    expect(options).toEqual([
      { id: 'a', name: 'Ann' },
      { id: 'b', name: 'Bo!' }
    ]);
  });

  it('reads only known action names from the composer, in API order', () => {
    const form = new FormData();
    form.append('macro_action', 'tags');
    form.append('macro_action', 'delete');
    form.append('macro_action', 'status');
    expect(keptActions(form)).toEqual(['status', 'tags']);
  });

  it('says what applied and why anything was skipped', () => {
    expect(
      applySummary({
        applied: ['status'],
        skipped: [{ action: 'tags', reason: 'Every tag this macro adds is archived.' }]
      })
    ).toBe('Macro applied: status. Every tag this macro adds is archived.');
    expect(applySummary({ applied: [], skipped: [] })).toBe(
      'The macro changed nothing on this ticket.'
    );
  });

  it('POSTs the ticket and the kept actions to apply/', async () => {
    apiRequest.mockResolvedValue({ applied: ['priority'], skipped: [] });
    await applyMacro(event, 'm1', 'c1', ['priority']);
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/macros/m1/apply/');
    expect(options).toEqual({ method: 'POST', body: { case_id: 'c1', only: ['priority'] } });
  });

  it('omits `only` to apply everything, for a macro with no text', async () => {
    apiRequest.mockResolvedValue({ applied: [], skipped: [] });
    await applyMacro(event, 'm1', 'c1', undefined);
    expect(apiRequest.mock.calls[0][1].body).toEqual({ case_id: 'c1' });
  });

  it('tells the composer which macros have text and what each one does', async () => {
    apiRequest.mockResolvedValue({
      results: [
        { id: 'm1', title: 'Words', body: 'Hi' },
        { id: 'm2', title: 'Close', body: '  ', set_status: 'Closed' }
      ]
    });
    expect(await listUsableMacros(event)).toEqual([
      { id: 'm1', title: 'Words', has_body: true, chips: [] },
      {
        id: 'm2',
        title: 'Close',
        has_body: false,
        chips: [{ key: 'status', label: 'Status: Closed' }]
      }
    ]);
  });
});
