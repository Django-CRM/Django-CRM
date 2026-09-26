/**
 * Merging a ticket into another, and undoing it.
 *
 * The detail load reads two things off the API: `can_merge` (may this person
 * merge THIS ticket) and `merged_from_cases` (what was merged into it, each
 * with its own `can_unmerge`). A source the viewer may not open arrives with
 * `name: null, restricted: true` and must read as the phrase a hidden parent
 * does. The picker lists only `merge-targets/`, never the general list.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { isRedirect } from '@sveltejs/kit';
import { RESTRICTED_TICKET_NAME } from '$lib/v2/enums.js';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicket, getMergeTargets, mergeTicket, unmergeTicket } = await import('./tickets.js');
const { actions } = await import('../../../routes/(app)/tickets/[id]/+page.server.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/** @param {Record<string, any>} extra */
function detail(extra) {
  return {
    cases_obj: { id: 'a', name: 'Ticket a', status: 'New', priority: 'Low' },
    comment_permission: true,
    ...extra
  };
}

/**
 * @param {string} action
 * @param {Record<string, string>} fields
 */
function actionEvent(action, fields) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return /** @type {any} */ ({
    request: new Request(`http://test/tickets/a?/${action}`, { method: 'POST', body }),
    params: { id: 'a' },
    cookies: { get: () => 'token' }
  });
}

/**
 * What `apiRequest` throws for the `{error: true, errors: "<sentence>"}` envelope.
 *
 * @param {number} status
 * @param {string} sentence
 */
function apiError(status, sentence) {
  return Object.assign(new Error(`errors: ${sentence}`), {
    status,
    body: { error: true, errors: sentence }
  });
}

describe('getTicket merge fields', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('maps can_merge and each merged-from source, redacting a restricted one', async () => {
    apiRequest.mockResolvedValue(
      detail({
        can_merge: true,
        merged_from_cases: [
          {
            id: 's-1',
            name: 'Printer again',
            merged_at: '2026-09-20T10:00:00Z',
            restricted: false,
            can_unmerge: true
          },
          {
            id: 's-2',
            name: null,
            merged_at: '2026-09-21T10:00:00Z',
            restricted: true,
            can_unmerge: false
          }
        ]
      })
    );
    const data = await getTicket(event, 'a');

    expect(data.canMerge).toBe(true);
    expect(data.mergedFrom).toEqual([
      {
        id: 's-1',
        name: 'Printer again',
        merged_at: '2026-09-20T10:00:00Z',
        restricted: false,
        can_unmerge: true
      },
      {
        id: 's-2',
        name: RESTRICTED_TICKET_NAME,
        merged_at: '2026-09-21T10:00:00Z',
        restricted: true,
        can_unmerge: false
      }
    ]);
  });

  it('offers nothing when the API does not say yes', async () => {
    apiRequest.mockResolvedValue(detail({}));
    const data = await getTicket(event, 'a');
    expect(data.canMerge).toBe(false);
    expect(data.mergedFrom).toEqual([]);
  });
});

describe('getMergeTargets', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks merge-targets for this ticket with the search, and returns only its results', async () => {
    apiRequest.mockResolvedValue({
      results: [
        { id: 't-1', name: 'Outage', status: 'New', priority: 'High', account_name: 'Acme' },
        { id: 't-2', name: 'Other', status: 'Pending', priority: 'Low', account_name: null }
      ]
    });
    const targets = await getMergeTargets(event, 'a', 'out age');

    expect(apiRequest).toHaveBeenCalledOnce();
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/a/merge-targets/?search=out+age');
    expect(targets.map((t) => t.id)).toEqual(['t-1', 't-2']);
    expect(targets[1].account_name).toBeNull();
  });
});

describe('mergeTicket and unmergeTicket', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ error: false });
  });

  it('POSTs an empty body to the merge endpoint, source first', async () => {
    await mergeTicket(event, 'a', 'b');
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/a/merge/b/');
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'POST', body: {} });
  });

  it('POSTs an empty body to the source ticket unmerge endpoint', async () => {
    await unmergeTicket(event, 's-1');
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/s-1/unmerge/');
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'POST', body: {} });
  });
});

describe('ticket detail merge actions', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('merges this ticket (never a form-supplied source) and goes to the target', async () => {
    apiRequest.mockResolvedValue({ error: false, target_case: { id: 'b' } });
    let thrown;
    try {
      await actions.merge(actionEvent('merge', { into: 'b', source: 'zzz' }));
    } catch (err) {
      thrown = err;
    }
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/a/merge/b/');
    expect(isRedirect(thrown)).toBe(true);
    expect(/** @type {any} */ (thrown).location).toBe('/tickets/b');
    expect(/** @type {any} */ (thrown).status).toBe(303);
  });

  it('refuses a merge with no target, without calling the API', async () => {
    const result = /** @type {any} */ (await actions.merge(actionEvent('merge', {})));
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('keeps a 403 merge refusal a 403 with the API sentence', async () => {
    const sentence = 'You must be an admin, or the creator of both tickets, to merge them.';
    apiRequest.mockRejectedValue(apiError(403, sentence));
    const result = /** @type {any} */ (await actions.merge(actionEvent('merge', { into: 'b' })));
    expect(result.status).toBe(403);
    expect(result.data.error).toBe(sentence);
  });

  it('turns another merge refusal into a 400 with the API sentence', async () => {
    apiRequest.mockRejectedValue(apiError(400, 'Cannot merge a ticket into itself.'));
    const result = /** @type {any} */ (await actions.merge(actionEvent('merge', { into: 'a' })));
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('Cannot merge a ticket into itself.');
  });

  it('unmerges the source named by the form and says so', async () => {
    apiRequest.mockResolvedValue({
      error: false,
      source_case: { id: 's-1', name: 'Printer again' }
    });
    const result = await actions.unmerge(actionEvent('unmerge', { source_id: 's-1' }));
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/s-1/unmerge/');
    expect(result).toEqual({ unmerged: 'Unmerged "Printer again".' });
  });

  it('refuses an unmerge with no source, without calling the API', async () => {
    const result = /** @type {any} */ (await actions.unmerge(actionEvent('unmerge', {})));
    expect(result.status).toBe(400);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('carries an unmerge refusal sentence', async () => {
    apiRequest.mockRejectedValue(apiError(400, 'This ticket is not currently merged.'));
    const result = /** @type {any} */ (
      await actions.unmerge(actionEvent('unmerge', { source_id: 's-1' }))
    );
    expect(result.status).toBe(400);
    expect(result.data.error).toBe('This ticket is not currently merged.');
  });
});
