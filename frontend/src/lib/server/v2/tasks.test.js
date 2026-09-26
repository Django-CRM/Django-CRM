import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTask } = await import('$lib/server/v2/tasks.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * `task_obj.contacts` is `ContactLinkSerializer`: `id`, `first_name`,
 * `last_name` and `email`. This read `primary_email`, which the API has never
 * sent, so every contact's email came out blank.
 */
describe('task contacts', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads the email the API sends', async () => {
    apiRequest.mockResolvedValue({
      task_obj: {
        id: 't1',
        title: 'Call',
        contacts: [{ id: 'c1', first_name: 'Pat', last_name: 'Doe', email: 'pat@example.com' }]
      }
    });

    const { contacts } = await getTask(event, 't1');

    expect(contacts).toEqual([{ id: 'c1', name: 'Pat Doe', email: 'pat@example.com' }]);
  });

  it('labels a nameless contact by that email', async () => {
    apiRequest.mockResolvedValue({
      task_obj: {
        id: 't1',
        title: 'Call',
        contacts: [{ id: 'c1', first_name: '', last_name: '', email: 'pat@example.com' }]
      }
    });

    const { contacts } = await getTask(event, 't1');

    expect(contacts[0].name).toBe('pat@example.com');
  });
});
