import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const { load } = await import('./+page.server.js');

const ID = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';

function run(related: () => unknown) {
  apiRequest.mockImplementation(async (url: string) => {
    if (url.startsWith('/objects/related/')) return related();
    if (url.includes('/records/'))
      return {
        id: ID,
        name: 'Asha',
        custom_fields: {},
        comments: [],
        object: { key: 'student', label: 'Student' }
      };
    return { key: 'student', label: 'Student', is_active: true, fields: [] };
  });
  return (load as unknown as (e: never) => Promise<{ related: unknown; record: { id: string } }>)({
    cookies: { get: () => 'token' },
    params: { key: 'student', id: ID },
    locals: {},
    url: new URL('http://test/objects/student/' + ID)
  } as never);
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('record page load: related', () => {
  it('returns the related answer, asked with obj:<key>', async () => {
    const answer = { groups: [], can_edit: true, link_fields: [] };
    const out = await run(() => answer);
    expect(out.related).toBe(answer);
    expect(apiRequest.mock.calls.map((c) => c[0])).toContain(
      `/objects/related/?to=obj%3Astudent&id=${ID}`
    );
  });

  it('returns an empty answer as is', async () => {
    const out = await run(() => ({ groups: [], can_edit: false, link_fields: [] }));
    expect(out.related).toEqual({ groups: [], can_edit: false, link_fields: [] });
  });

  it('still loads the page, with null related, when the call is refused', async () => {
    const out = await run(() => {
      throw Object.assign(new Error('no'), { status: 403 });
    });
    expect(out.related).toBeNull();
    expect(out.record.id).toBe(ID);
  });
});
