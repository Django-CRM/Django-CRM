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
    request: new Request('http://test/tickets/board?/move', { method: 'POST', body }),
    cookies: { get: () => 'token' }
  });
}

/**
 * What `apiRequest` throws: the flattened message, the status, and the body.
 *
 * @param {number} status
 * @param {string} message
 * @param {any} body
 */
function apiError(status, message, body) {
  return Object.assign(new Error(message), { status, body });
}

describe('tickets board move action', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('persists a move into a status column', async () => {
    apiRequest.mockResolvedValue({ error: false });
    const result = await actions.move(
      moveEvent({ id: 'c-1', lane: 'Pending', mode: 'status', below_id: 'c-3' })
    );
    expect(result).toEqual({ success: true });
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/c-1/move/');
    expect(apiRequest.mock.calls[0][1].body).toEqual({ status: 'Pending', below_case_id: 'c-3' });
  });

  it('persists a move into a pipeline stage as a stage_id', async () => {
    apiRequest.mockResolvedValue({ error: false });
    await actions.move(moveEvent({ id: 'c-1', lane: 's-2', mode: 'pipeline' }));
    expect(apiRequest.mock.calls[0][1].body).toEqual({ stage_id: 's-2' });
  });

  it('refuses a request missing the ticket or the column, without calling the API', async () => {
    const noLane = /** @type {any} */ (await actions.move(moveEvent({ id: 'c-1' })));
    const noId = /** @type {any} */ (await actions.move(moveEvent({ lane: 'New' })));
    expect(noLane.status).toBe(400);
    expect(noId.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('keeps a 403 (a ticket the caller may read but not change) a 403, with the reason', async () => {
    apiRequest.mockRejectedValue(
      apiError(403, 'You do not have permission to perform this action.', {
        detail: 'You do not have permission to perform this action.'
      })
    );
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'c-1', lane: 'New', mode: 'status' }))
    );
    expect(result.status).toBe(403);
    expect(result.data.error).toBe('You do not have permission to perform this action.');
  });

  it('carries the close gate sentence on a refused move into Closed, without the field name', async () => {
    const sentence = 'An approval is required before this case can be closed (rule: Refunds).';
    apiRequest.mockRejectedValue(
      apiError(400, `status: ${sentence}`, { error: true, errors: { status: [sentence] } })
    );
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'c-1', lane: 'Closed', mode: 'status' }))
    );
    expect(result.status).toBe(400);
    expect(result.data.error).toBe(sentence);
  });

  it('carries a WIP limit refusal as a 400', async () => {
    const sentence = "Stage 'Triage' has reached its WIP limit of 5";
    apiRequest.mockRejectedValue(apiError(400, sentence, { error: sentence }));
    const result = /** @type {any} */ (
      await actions.move(moveEvent({ id: 'c-1', lane: 's-1', mode: 'pipeline' }))
    );
    expect(result.status).toBe(400);
    expect(result.data.error).toBe(sentence);
  });
});
