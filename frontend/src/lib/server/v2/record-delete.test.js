import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { deleteLead } = await import('$lib/server/v2/leads.js');
const { deleteContact } = await import('$lib/server/v2/contacts.js');
const { deleteAccount } = await import('$lib/server/v2/accounts.js');
const { deleteDeal } = await import('$lib/server/v2/deals.js');
const { deleteArticle, getArticle } = await import('$lib/server/v2/solutions.js');
const { actions } = await import('../../../routes/(app)/accounts/[id]/+page.server.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

beforeEach(() => {
  apiRequest.mockReset();
});

describe('deleting a lead, contact, account, deal or article', () => {
  it.each([
    [deleteLead, '/leads/x1/'],
    [deleteContact, '/contacts/x1/'],
    [deleteAccount, '/accounts/x1/'],
    [deleteDeal, '/opportunities/x1/'],
    [deleteArticle, '/cases/solutions/x1/']
  ])('sends DELETE to the record', async (del, endpoint) => {
    apiRequest.mockResolvedValue({ error: false });
    await del(event, 'x1');
    expect(apiRequest).toHaveBeenCalledWith(
      endpoint,
      { method: 'DELETE' },
      { cookies: event.cookies }
    );
  });
});

describe('the account page delete action', () => {
  it('lands on the list once the account is gone', async () => {
    apiRequest.mockResolvedValue({ error: false });
    await expect(
      actions.delete(/** @type {any} */ ({ ...event, params: { id: 'a1' } }))
    ).rejects.toMatchObject({ status: 303, location: '/accounts' });
  });

  it("keeps the API's reason and status when it refuses", async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('conflict'), {
        status: 409,
        body: { error: true, errors: 'This account still has invoices.' }
      })
    );
    const result = await actions.delete(/** @type {any} */ ({ ...event, params: { id: 'a1' } }));
    expect(result).toMatchObject({
      status: 409,
      data: { deleteError: 'This account still has invoices.' }
    });
  });

  it('turns a server error into a 400, never a 5xx form result', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }));
    const result = await actions.delete(/** @type {any} */ ({ ...event, params: { id: 'a1' } }));
    expect(result).toMatchObject({ status: 400 });
  });
});

describe('a knowledge-base article carries the API write rule', () => {
  it.each([
    [{ can_edit: true }, true],
    [{ can_edit: false }, false],
    [{}, false]
  ])('can_edit %j reads as %s', async (fact, expected) => {
    apiRequest.mockResolvedValue({
      id: 's1',
      title: 'Reset',
      description: 'Steps',
      status: 'draft',
      linked_cases: [],
      case_count: 0,
      ...fact
    });
    const loaded = await getArticle(event, 's1');
    expect(loaded.canEdit).toBe(expected);
  });
});

describe('an article carries the API delete rule', () => {
  it.each([
    [{ can_delete: true }, true],
    [{}, false]
  ])('can_delete %j reads as %s', async (fact, expected) => {
    apiRequest.mockResolvedValue({ id: 's1', title: 'T', linked_cases: [], ...fact });
    expect((await getArticle(event, 's1')).canDelete).toBe(expected);
  });
});

describe.each([
  ['deal', '../../../routes/(app)/pipeline/[id]/+page.server.js', '/pipeline'],
  ['article', '../../../routes/(app)/solutions/[id]/+page.server.js', '/solutions']
])('the %s page delete action', (_name, route, list) => {
  it('lands on the list once it is gone', async () => {
    const { actions } = await import(/* @vite-ignore */ route);
    apiRequest.mockResolvedValue(null);
    await expect(
      actions.delete(/** @type {any} */ ({ ...event, params: { id: 'x1' } }))
    ).rejects.toMatchObject({ status: 303, location: list });
  });

  it("shows the API's refusal with its status", async () => {
    const { actions } = await import(/* @vite-ignore */ route);
    apiRequest.mockRejectedValue(
      Object.assign(new Error('forbidden'), {
        status: 403,
        body: { error: true, errors: 'You do not have Permission to perform this action' }
      })
    );
    const result = await actions.delete(/** @type {any} */ ({ ...event, params: { id: 'x1' } }));
    expect(result).toMatchObject({
      status: 403,
      data: { error: 'You do not have Permission to perform this action' }
    });
  });
});
