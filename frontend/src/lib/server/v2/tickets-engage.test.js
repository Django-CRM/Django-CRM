/**
 * Watching, linked articles and saved replies on the ticket page. The API owns
 * every rule (who may watch, the write rule for linking, the read rule and
 * placeholders for a saved reply); these check the right endpoint, verb and
 * body, and that a saved reply only ever comes back as text for the composer.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicketWatchers, suggestTicketArticles } = await import('./tickets.js');
const { listUsableMacros } = await import('./macros.js');
const { actions } = await import('../../../routes/(app)/tickets/[id]/+page.server.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * @param {string} action
 * @param {Record<string, string>} fields
 */
function actionEvent(action, fields = {}) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return /** @type {any} */ ({
    request: new Request(`http://test/tickets/a?/${action}`, { method: 'POST', body }),
    params: { id: 'a' },
    cookies: { get: () => 'token' }
  });
}

/** @param {number} status @param {string} sentence */
function apiError(status, sentence) {
  return Object.assign(new Error(`errors: ${sentence}`), {
    status,
    body: { error: true, errors: sentence }
  });
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('watching', () => {
  it('reads the server’s own answer about the viewer', async () => {
    apiRequest.mockResolvedValue({ watchers: [{}, {}], count: 2, is_current_user_watching: true });
    expect(await getTicketWatchers(event, 'a')).toEqual({ count: 2, watching: true });
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/a/watchers/');
  });

  it('is not watching unless the server says so', async () => {
    apiRequest.mockResolvedValue({ watchers: [{ user_id: 'me' }], count: 1 });
    expect((await getTicketWatchers(event, 'a')).watching).toBe(false);
  });

  it('watch posts and unwatch deletes', async () => {
    apiRequest.mockResolvedValue({});
    expect(await actions.watch(actionEvent('watch'))).toEqual({ watching: true });
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual(['/cases/a/watch/', { method: 'POST' }]);
    expect(await actions.unwatch(actionEvent('unwatch'))).toEqual({ watching: false });
    expect(apiRequest.mock.calls[1].slice(0, 2)).toEqual(['/cases/a/watch/', { method: 'DELETE' }]);
  });

  it('shows a refusal instead of claiming success', async () => {
    apiRequest.mockRejectedValue(apiError(404, 'Not found.'));
    const out = /** @type {any} */ (await actions.watch(actionEvent('watch')));
    expect(out.status).toBe(400);
    expect(out.data.error).toBe('Not found.');
  });
});

describe('articles', () => {
  it('asks for suggestions seeded by the ticket, or matching the search', async () => {
    apiRequest.mockResolvedValue({ results: [{ id: 's1', title: 'Reset a password' }] });
    const rows = await suggestTicketArticles(event, 'a', '');
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/a/solution-suggestions/?limit=10');
    expect(rows).toEqual([{ id: 's1', title: 'Reset a password', snippet: '' }]);

    await suggestTicketArticles(event, 'a', 'vpn');
    const q = new URLSearchParams(apiRequest.mock.calls[1][0].split('?')[1]);
    expect(q.get('q')).toBe('vpn');
  });

  it('links with the article id in the body', async () => {
    apiRequest.mockResolvedValue({});
    const out = await actions.linkArticle(actionEvent('linkArticle', { article_id: 's1' }));
    expect(apiRequest).toHaveBeenCalledWith(
      '/cases/a/solutions/',
      { method: 'POST', body: { solution_id: 's1' } },
      expect.anything()
    );
    expect(out).toEqual({ articleLinked: true });
  });

  it('unlinks through the per-article route', async () => {
    apiRequest.mockResolvedValue({});
    await actions.unlinkArticle(actionEvent('unlinkArticle', { article_id: 's1' }));
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/cases/a/solutions/s1/',
      { method: 'DELETE' }
    ]);
  });

  it('surfaces the write rule’s refusal', async () => {
    apiRequest.mockRejectedValue(
      apiError(403, 'You do not have permission to change this ticket.')
    );
    const out = /** @type {any} */ (
      await actions.linkArticle(actionEvent('linkArticle', { article_id: 's1' }))
    );
    expect(out.data.articleError).toBe('You do not have permission to change this ticket.');
  });

  it('refuses a missing article id before calling the API', async () => {
    for (const name of ['linkArticle', 'unlinkArticle']) {
      const out = /** @type {any} */ (await actions[name](actionEvent(name)));
      expect(out.data.articleError).toBe('Which article? None was given.');
    }
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('saved replies', () => {
  it('lists only active ones, as id and title', async () => {
    apiRequest.mockResolvedValue({
      results: [{ id: 'm1', title: 'Greeting', body: 'Hi %customer_name%', scope: 'org' }]
    });
    expect(await listUsableMacros(event)).toEqual([{ id: 'm1', title: 'Greeting' }]);
    expect(apiRequest.mock.calls[0][0]).toBe('/macros/?active=true');
  });

  it('renders against this ticket and returns text only, sending nothing', async () => {
    apiRequest.mockResolvedValue({ rendered_body: 'Hi Liz' });
    const out = await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm1' }));
    expect(out).toEqual({ macroText: 'Hi Liz' });
    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(apiRequest).toHaveBeenCalledWith(
      '/macros/m1/render/',
      { method: 'POST', body: { case_id: 'a' } },
      expect.anything()
    );
  });

  it('says so when the server refuses the render', async () => {
    apiRequest.mockRejectedValue(apiError(404, 'Not found.'));
    const out = /** @type {any} */ (
      await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm1' }))
    );
    expect(out.data.macroError).toBe('Not found.');
  });

  it('asks for a choice before calling the API', async () => {
    const out = /** @type {any} */ (await actions.renderMacro(actionEvent('renderMacro')));
    expect(out.data.macroError).toBe('Pick a saved reply first.');
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
