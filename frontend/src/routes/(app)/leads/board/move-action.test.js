import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

// This route module imports nothing from `$app/*`, so the standalone vitest
// config can load it (see the note in `vitest.config.js`).
const { actions } = await import('./+page.server.js');

/** @param {Record<string, string>} fields */
function moveEvent(fields) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return /** @type {any} */ ({
    request: new Request('http://test/leads/board?/move', { method: 'POST', body }),
    cookies: { get: () => 'token' }
  });
}

describe('leads board move action', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('persists a move into a stage', async () => {
    apiRequest.mockResolvedValue({ error: false });
    const result = await actions.move(moveEvent({ id: 'l-1', stage_id: 's-2', below_id: 'l-3' }));
    expect(result).toEqual({ success: true });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ stage_id: 's-2', below_lead_id: 'l-3' });
  });

  it('refuses "No stage" as a destination without calling the API', async () => {
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'l-1', stage_id: 'unstaged' }))
    );
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('refuses a request missing the lead or the stage', async () => {
    const result = /** @type {any} */ (await actions.move(moveEvent({ id: 'l-1' })));
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('keeps a 403 from the API a 403, with the API sentence', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('Permission denied'), { status: 403 }));
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'l-1', stage_id: 's-2' }))
    );
    expect(result.status).toBe(403);
    expect(result.data.error).toBe('Permission denied');
  });

  it('turns any other refusal into a 400 carrying the reason', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('This lead is in the Inbound pipeline.'), { status: 400 })
    );
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'l-1', stage_id: 's-9' }))
    );
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('This lead is in the Inbound pipeline.');
  });
});
