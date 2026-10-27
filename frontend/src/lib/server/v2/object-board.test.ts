import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const { getBoard, moveAction, readMove, setBoardField } =
  await import('#lib/server/v2/object-board.js');

const R1 = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';
const R2 = '1c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6c';
const cookies = { get: () => 'token' } as never;

function formOf(fields: Record<string, string>) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.set(k, v);
  return form;
}

function moveEvent(fields: Record<string, string>) {
  return {
    cookies,
    params: { key: 'property' },
    request: new Request('http://test/x?/move', { method: 'POST', body: formOf(fields) })
  };
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('readMove', () => {
  it('builds the contract body for an option', () => {
    expect(readMove(formOf({ record: R1, kind: 'option', value: 'sold', above_id: R2 }))).toEqual({
      body: { record: R1, value: 'sold', above_id: R2, below_id: null }
    });
  });

  it('sends null for No value', () => {
    expect(readMove(formOf({ record: R1, kind: 'none' }))).toEqual({
      body: { record: R1, value: null, above_id: null, below_id: null }
    });
  });

  it('refuses Other values, an unknown kind, a missing value and a bad record', () => {
    expect(readMove(formOf({ record: R1, kind: 'other' }))).toHaveProperty('error');
    expect(readMove(formOf({ record: R1, kind: 'x' }))).toHaveProperty('error');
    expect(readMove(formOf({ record: R1, kind: 'option' }))).toHaveProperty('error');
    expect(readMove(formOf({ record: 'nope', kind: 'none' }))).toHaveProperty('error');
  });

  it('drops a neighbour that is not an id', () => {
    const read = readMove(formOf({ record: R1, kind: 'none', above_id: 'zzz' }));
    expect(read).toMatchObject({ body: { above_id: null } });
  });
});

describe('moveAction', () => {
  it('posts the contract body to the move endpoint', async () => {
    apiRequest.mockResolvedValue({ record: { id: R1, can_edit: true } });
    const result = await moveAction(
      moveEvent({ record: R1, kind: 'option', value: 'sold', below_id: R2 })
    );
    expect(result).toEqual({ success: true, record: { id: R1, can_edit: true } });
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/board/move/');
    expect(apiRequest.mock.calls[0][1]).toMatchObject({
      method: 'POST',
      body: { record: R1, value: 'sold', above_id: null, below_id: R2 }
    });
  });

  it('does not call the API for a move it can tell is refused', async () => {
    const result = (await moveAction(moveEvent({ record: R1, kind: 'other' }))) as never as {
      status: number;
    };
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('keeps a 404 a 404 with a sentence', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Not found.'), { status: 404 }));
    const result = (await moveAction(moveEvent({ record: R1, kind: 'none' }))) as never as {
      status: number;
      data: { error: string };
    };
    expect(result.status).toBe(404);
    expect(result.data.error).toMatch(/not one you can move/);
  });

  it('shows a single-field 400 without the raw field key', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error("value: Choose one of this field's options."), { status: 400 })
    );
    const result = (await moveAction(
      moveEvent({ record: R1, kind: 'option', value: 'x' })
    )) as never as { status: number; data: { error: string } };
    expect(result.status).toBe(400);
    expect(result.data.error).toBe("Choose one of this field's options.");
  });

  it('passes the status of any other refusal', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('You cannot change this record.'), { status: 403 })
    );
    const result = (await moveAction(moveEvent({ record: R1, kind: 'none' }))) as never as {
      status: number;
    };
    expect(result.status).toBe(403);
  });
});

describe('getBoard and setBoardField', () => {
  it('forwards the list filters but not paging', async () => {
    apiRequest.mockResolvedValue({ field: { key: 'stage', label: 'Stage' }, columns: [] });
    await getBoard({ cookies }, 'property', new URLSearchParams('q=a b&page=2&limit=9&tags=t1'));
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/board/?q=a%20b&tags=t1');
  });

  it('turns a 404 into a page 404', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('x'), { status: 404 }));
    await expect(getBoard({ cookies }, 'property')).rejects.toMatchObject({ status: 404 });
  });

  it('patches only board_field', async () => {
    apiRequest.mockResolvedValue({});
    await setBoardField({ cookies }, 'property', '');
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/');
    expect(apiRequest.mock.calls[0][1]).toMatchObject({
      method: 'PATCH',
      body: { board_field: '' }
    });
  });
});
