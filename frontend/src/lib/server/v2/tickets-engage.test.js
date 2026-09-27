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
 * @param {Record<string, string | string[]>} fields a list sends the key once per item
 */
function actionEvent(action, fields = {}) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    for (const v of [value].flat()) body.append(key, v);
  }
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

/**
 * Answer each API call by its path and verb (`'PATCH /cases/a/'`, or just the
 * path for any verb). An Error value is thrown instead of returned.
 *
 * @param {Record<string, any>} table
 */
function routes(table) {
  apiRequest.mockImplementation(async (path, opts = {}) => {
    const key = `${opts.method ?? 'GET'} ${path}`;
    const hit = key in table ? table[key] : table[path];
    if (hit === undefined) throw new Error(`unexpected call: ${key}`);
    if (hit instanceof Error) throw hit;
    return hit;
  });
}

/** The API calls made, as `'VERB /path'`. */
function calls() {
  return apiRequest.mock.calls.map(([path, opts = {}]) => `${opts.method ?? 'GET'} ${path}`);
}

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
    expect(await listUsableMacros(event)).toEqual([
      { id: 'm1', title: 'Greeting', has_body: true, chips: [] }
    ]);
    expect(apiRequest.mock.calls[0][0]).toBe('/macros/?active=true');
  });

  it('renders against this ticket and returns text only, sending nothing', async () => {
    routes({
      '/macros/?active=true': { results: [{ id: 'm1', title: 'Greeting', body: 'Hi' }] },
      '/macros/m1/render/': { rendered_body: 'Hi Liz' }
    });
    const out = await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm1' }));
    expect(out).toEqual({ macroText: 'Hi Liz', macroId: 'm1' });
    expect(apiRequest).toHaveBeenCalledTimes(2);
    expect(apiRequest).toHaveBeenCalledWith(
      '/macros/m1/render/',
      { method: 'POST', body: { case_id: 'a' } },
      expect.anything()
    );
  });

  it('lists a macro’s action chips and whether it has any text', async () => {
    apiRequest.mockResolvedValue({
      results: [{ id: 'm2', title: 'Close it', body: '  ', set_status: 'Closed' }]
    });
    expect(await listUsableMacros(event)).toEqual([
      {
        id: 'm2',
        title: 'Close it',
        has_body: false,
        chips: [{ key: 'status', label: 'Status: Closed' }]
      }
    ]);
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

describe('macro actions on send', () => {
  const posted = { 'POST /cases/a/': {} };
  const applied = { applied: ['status', 'tags'], skipped: [] };

  it('posts the reply, then applies only the kept actions', async () => {
    routes({ ...posted, 'POST /macros/m1/apply/': applied });
    const out = await actions.reply(
      actionEvent('reply', { body: 'Done', macro_id: 'm1', macro_action: ['tags', 'status'] })
    );
    expect(out).toEqual({
      sent: true,
      internal: false,
      macroNote: 'Macro applied: status, tags.'
    });
    expect(calls()).toEqual(['POST /cases/a/', 'POST /macros/m1/apply/']);
    expect(apiRequest.mock.calls[1][1].body).toEqual({ case_id: 'a', only: ['status', 'tags'] });
  });

  it('applies nothing when every chip was taken off', async () => {
    routes(posted);
    const out = await actions.reply(actionEvent('reply', { body: 'Done', macro_id: 'm1' }));
    expect(out).toEqual({ sent: true, internal: false });
    expect(calls()).toEqual(['POST /cases/a/']);
  });

  it('keeps the reply marked sent when the apply is refused after it posted', async () => {
    routes({ ...posted, 'POST /macros/m1/apply/': apiError(400, 'Approval needed to close.') });
    const out = /** @type {any} */ (
      await actions.reply(
        actionEvent('reply', { body: 'Done', macro_id: 'm1', macro_action: 'status' })
      )
    );
    expect(out.status).toBe(400);
    expect(out.data.sent).toBe(true);
    expect(out.data.error).toBe(
      "Reply posted, but the macro's actions were not applied: Approval needed to close."
    );
  });

  it('still applies the macro when the status change is refused, and names that part', async () => {
    routes({
      ...posted,
      'PATCH /cases/a/': apiError(400, 'This ticket is merged.'),
      'POST /macros/m1/apply/': { applied: ['tags'], skipped: [] }
    });
    const out = /** @type {any} */ (
      await actions.reply(
        actionEvent('reply', {
          body: 'Done',
          status: 'Pending',
          macro_id: 'm1',
          macro_action: 'tags'
        })
      )
    );
    expect(calls()).toEqual(['POST /cases/a/', 'PATCH /cases/a/', 'POST /macros/m1/apply/']);
    expect(out.status).toBe(400);
    expect(out.data).toEqual({
      sent: true,
      macroNote: 'Macro applied: tags.',
      error: 'Reply posted, but the status stayed put: This ticket is merged.'
    });
  });

  it('names both parts when the status and the macro are both refused', async () => {
    routes({
      ...posted,
      'PATCH /cases/a/': apiError(400, 'No.'),
      'POST /macros/m1/apply/': apiError(403, 'Not yours.')
    });
    const out = /** @type {any} */ (
      await actions.reply(
        actionEvent('reply', {
          body: 'Done',
          status: 'Pending',
          macro_id: 'm1',
          macro_action: 'tags'
        })
      )
    );
    expect(out.data.sent).toBe(true);
    expect(out.data.error).toBe(
      "Reply posted, but the status stayed put: No. Also, the macro's actions were not applied: Not yours."
    );
  });

  it('never applies anything when the reply itself fails', async () => {
    routes({ 'POST /cases/a/': apiError(400, 'Too long.') });
    const out = /** @type {any} */ (
      await actions.reply(
        actionEvent('reply', { body: 'Done', macro_id: 'm1', macro_action: 'status' })
      )
    );
    expect(out.data.sent).toBeUndefined();
    expect(calls()).toEqual(['POST /cases/a/']);
  });
});

describe('a macro with no text', () => {
  const list = {
    '/macros/?active=true': {
      results: [
        { id: 'm1', title: 'Greeting', body: 'Hi' },
        { id: 'm2', title: 'Close it', body: '', set_status: 'Closed' }
      ]
    }
  };

  it('Apply applies every action it carries, at once', async () => {
    routes({ 'POST /macros/m2/apply/': { applied: ['status'], skipped: [] } });
    const out = await actions.applyMacro(actionEvent('applyMacro', { macro_id: 'm2' }));
    expect(out).toEqual({ macroApplied: 'Macro applied: status.' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ case_id: 'a' });
  });

  it('Apply reports a refusal in the picker', async () => {
    routes({ 'POST /macros/m2/apply/': apiError(403, 'Not yours.') });
    const out = /** @type {any} */ (
      await actions.applyMacro(actionEvent('applyMacro', { macro_id: 'm2' }))
    );
    expect(out.data.macroError).toBe('Not yours.');
  });

  it('Insert without script applies it instead of rendering nothing', async () => {
    routes({ ...list, 'POST /macros/m2/apply/': { applied: ['status'], skipped: [] } });
    const out = await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm2' }));
    expect(out).toEqual({ macroApplied: 'Macro applied: status.' });
    expect(calls()).toEqual(['GET /macros/?active=true', 'POST /macros/m2/apply/']);
  });

  it('Insert still renders a macro that has text', async () => {
    routes({ ...list, 'POST /macros/m1/render/': { rendered_body: 'Hi Liz' } });
    const out = await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm1' }));
    expect(out).toEqual({ macroText: 'Hi Liz', macroId: 'm1' });
    expect(calls()).not.toContain('POST /macros/m1/apply/');
  });

  it('Insert renders, never applies, when the list cannot be read', async () => {
    routes({
      '/macros/?active=true': apiError(500, 'Down.'),
      'POST /macros/m2/render/': { rendered_body: '' }
    });
    const out = await actions.renderMacro(actionEvent('renderMacro', { macro_id: 'm2' }));
    expect(out).toEqual({ macroText: '', macroId: 'm2' });
    expect(calls()).not.toContain('POST /macros/m2/apply/');
  });
});

describe('closing from the status buttons', () => {
  it('sends no closed_on: the API dates the close in the org’s timezone', async () => {
    routes({ 'PATCH /cases/a/': {} });
    const out = await actions.setStatus(actionEvent('setStatus', { status: 'Closed' }));
    expect(out).toEqual({ moved: 'Closed' });
    expect(calls()).toEqual(['PATCH /cases/a/']);
    expect(apiRequest.mock.calls[0][1].body).toEqual({ status: 'Closed' });
  });

  it('shows the API’s own refusal when the close is gated', async () => {
    const refusal =
      'An approval is required before this case can be closed (rule: Close needs sign-off).';
    routes({ 'PATCH /cases/a/': apiError(400, refusal) });
    const out = /** @type {any} */ (
      await actions.setStatus(actionEvent('setStatus', { status: 'Closed' }))
    );
    expect(out.status).toBe(400);
    expect(out.data.error).toContain(refusal);
  });

  it('sends just the status for any other status', async () => {
    routes({ 'PATCH /cases/a/': {} });
    await actions.setStatus(actionEvent('setStatus', { status: 'Pending' }));
    expect(calls()).toEqual(['PATCH /cases/a/']);
    expect(apiRequest.mock.calls[0][1].body).toEqual({ status: 'Pending' });
  });
});
