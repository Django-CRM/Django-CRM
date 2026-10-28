import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const { actions } = await import('./+page.server.js');

const ID = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';
const NOTE = '1c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6c';

function event(fields: Record<string, string>) {
  const body = new FormData();
  for (const [k, v] of Object.entries(fields)) body.set(k, v);
  return {
    request: new Request('http://test/x', { method: 'POST', body }),
    cookies: { get: () => 'token' },
    params: { key: 'property', id: ID }
  } as never;
}

const call = (name: 'comment' | 'editNote' | 'deleteNote', fields: Record<string, string>) =>
  (actions[name] as (e: never) => Promise<unknown>)(event(fields)) as Promise<
    {
      status?: number;
      data?: { noteError?: string; noteFailure?: { id: string; message: string } };
    } & Record<string, unknown>
  >;

beforeEach(() => {
  apiRequest.mockReset();
});

describe('record note actions', () => {
  it('posts a trimmed comment to the record detail route', async () => {
    apiRequest.mockResolvedValue({});
    expect(await call('comment', { comment: '  Called the owner  ' })).toEqual({ commented: true });
    expect(apiRequest.mock.calls[0][0]).toBe(`/objects/property/records/${ID}/`);
    expect(apiRequest.mock.calls[0][1]).toMatchObject({
      method: 'POST',
      body: { comment: 'Called the owner' }
    });
  });

  it('refuses an empty comment without calling the API', async () => {
    const result = await call('comment', { comment: '   ' });
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('shows a plan refusal as a 403', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Upgrade.'), { status: 403 }));
    expect((await call('comment', { comment: 'x' })).status).toBe(403);
  });

  it('patches a note by comment id', async () => {
    apiRequest.mockResolvedValue({});
    expect(await call('editNote', { note: NOTE, comment: ' Better ' })).toEqual({
      noteEdited: true
    });
    expect(apiRequest.mock.calls[0][0]).toBe(`/objects/property/comments/${NOTE}/`);
    expect(apiRequest.mock.calls[0][1]).toMatchObject({
      method: 'PATCH',
      body: { comment: 'Better' }
    });
  });

  it("refuses an empty edit and an edit of someone else's note", async () => {
    expect((await call('editNote', { note: NOTE, comment: ' ' })).status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
    apiRequest.mockRejectedValue(Object.assign(new Error('no'), { status: 403 }));
    expect((await call('editNote', { note: NOTE, comment: 'x' })).status).toBe(403);
  });

  it('names the note a failed edit or delete belongs to', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Only the author.'), { status: 403 }));
    expect((await call('editNote', { note: NOTE, comment: 'x' })).data?.noteFailure).toEqual({
      id: NOTE,
      message: 'Only the author.'
    });
    expect((await call('deleteNote', { note: NOTE })).data?.noteFailure).toEqual({
      id: NOTE,
      message: 'Only the author.'
    });
  });

  it('deletes a note by comment id', async () => {
    apiRequest.mockResolvedValue(null);
    expect(await call('deleteNote', { note: NOTE })).toEqual({ noteDeleted: true });
    expect(apiRequest.mock.calls[0][0]).toBe(`/objects/property/comments/${NOTE}/`);
    expect(apiRequest.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
  });

  it('answers 404 for a note id that is not an id', async () => {
    await expect(call('deleteNote', { note: 'nope' })).rejects.toMatchObject({ status: 404 });
  });
});

describe('related actions', () => {
  const REC = '2c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6d';
  const run = (name: 'relatedAdd' | 'relatedRemove') =>
    (actions[name] as (e: never) => Promise<unknown>)(
      event({ record: REC, field: 'student:enquiries' })
    );

  it('adds with to=obj:<key>', async () => {
    apiRequest.mockResolvedValue({ added: true });
    expect(await run('relatedAdd')).toEqual({ related: { done: true } });
    expect(apiRequest.mock.calls[0][1]).toMatchObject({
      method: 'POST',
      body: { to: 'obj:property', id: ID, record: REC, field: 'enquiries' }
    });
  });

  it('removes with to=obj:<key>', async () => {
    apiRequest.mockResolvedValue(null);
    await run('relatedRemove');
    expect(apiRequest.mock.calls[0][0]).toContain('to=obj%3Aproperty');
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'DELETE' });
  });
});
