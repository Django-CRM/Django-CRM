import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const { loadRelated, relatedActions } = await import('#lib/server/v2/related-records.js');

const event = { cookies: { get: () => 'token' } } as any;
const ID = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';
const REC = '1c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6c';
const status = (n: number, body?: any) => Object.assign(new Error('failed'), { status: n, body });

function post(values: Record<string, string>) {
  const form = new FormData();
  for (const [k, v] of Object.entries(values)) form.set(k, v);
  return { cookies: event.cookies, params: { id: ID }, request: { formData: async () => form } };
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('loadRelated', () => {
  it('loads nothing, quietly, where the server has no custom objects', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    expect(await loadRelated(event, 'lead', ID)).toBeNull();
    expect(apiRequest.mock.calls[0][1]).toMatchObject({ quietErrors: true });
  });

  it('answers the related payload', async () => {
    const answer = { groups: [], can_edit: false, link_fields: [] };
    apiRequest.mockResolvedValueOnce(answer);
    expect(await loadRelated(event, 'lead', ID)).toBe(answer);
  });
});

describe('relatedActions', () => {
  it('asks to choose a record when Add arrives empty, without calling the API', async () => {
    const out = await relatedActions('lead').relatedAdd(
      post({ record: '', field: 'property:enquiries' }) as any
    );
    expect(out).toMatchObject({
      status: 400,
      data: { related: { error: 'Choose a record to add.' } }
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('adds through the API with the page record as the target', async () => {
    apiRequest.mockResolvedValueOnce({ added: true });
    const out = await relatedActions('lead').relatedAdd(
      post({ record: REC, field: 'property:enquiries' }) as any
    );
    expect(out).toEqual({ related: { done: true } });
    expect(apiRequest).toHaveBeenCalledWith(
      '/objects/related/',
      { method: 'POST', body: { to: 'lead', id: ID, record: REC, field: 'enquiries' } },
      expect.anything()
    );
  });

  it('reads the target from the request when given a function', async () => {
    apiRequest.mockResolvedValueOnce({ added: true });
    const actions = relatedActions((e) => `obj:${e.params.key}`);
    const out = await actions.relatedAdd({
      ...post({ record: REC, field: 'student:enquiries' }),
      params: { id: ID, key: 'batch' }
    } as any);
    expect(out).toEqual({ related: { done: true } });
    expect(apiRequest).toHaveBeenCalledWith(
      '/objects/related/',
      { method: 'POST', body: { to: 'obj:batch', id: ID, record: REC, field: 'enquiries' } },
      expect.anything()
    );
  });

  it('removes through the API', async () => {
    apiRequest.mockResolvedValueOnce(null);
    const out = await relatedActions('deal').relatedRemove(
      post({ record: REC, field: 'enquiries' }) as any
    );
    expect(out).toEqual({ related: { done: true } });
    expect(apiRequest.mock.calls[0][0]).toContain('to=deal');
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'DELETE' });
  });

  it('refuses a malformed record id before calling the API', async () => {
    const out = (await relatedActions('lead').relatedAdd(
      post({ record: 'nope', field: 'property:enquiries' }) as any
    )) as any;
    expect(out.status).toBe(400);
    expect(out.data.related.error).toBe('No such record, or you cannot open it.');
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('refuses a malformed object or field key before calling the API', async () => {
    for (const field of ['Bad:enquiries', 'property:Bad Key', '', 'a:b:c']) {
      const out = (await relatedActions('lead').relatedAdd(
        post({ record: REC, field }) as any
      )) as any;
      expect(out.status).toBe(400);
    }
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('passes the API status through', async () => {
    apiRequest.mockRejectedValueOnce(status(403, { detail: 'You cannot edit this.' }));
    const out = (await relatedActions('lead').relatedAdd(
      post({ record: REC, field: 'property:enquiries' }) as any
    )) as any;
    expect(out.status).toBe(403);
    expect(typeof out.data.related.error).toBe('string');
  });
});
