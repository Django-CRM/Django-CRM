import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));
vi.mock('./organization.js', () => ({ viewerRole: () => 'ADMIN' }));

const { getWebForm, updateWebForm } = await import('./web-forms.js');

const cookies = /** @type {any} */ ({ get: () => 'token' });
const event = /** @type {any} */ ({ cookies });

const ACTIVE = { id: 'p1', user_details: { email: 'ada@example.com' } };

/**
 * Answer the four requests `getWebForm` makes. The people list is what
 * `/users/get-teams-and-users/` returns: active members only.
 *
 * @param {any} assignee the form's `assign_to_details`
 */
function serve(assignee) {
  apiRequest.mockImplementation(async (/** @type {string} */ url) => {
    if (url.startsWith('/webforms/')) {
      return { id: 'f1', assign_to: assignee?.id ?? null, assign_to_details: assignee };
    }
    if (url === '/users/get-teams-and-users/') return { profiles: [ACTIVE] };
    if (url === '/custom-fields/') return { definitions: [] };
    return { tags: [] };
  });
}

describe('getWebForm: the stored assignee the picker cannot offer', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('is returned, marked inactive, when the assignee was deactivated', async () => {
    // Without it the select has no option matching the stored id, submits
    // nothing, and the save quietly clears the assignee.
    serve({ id: 'gone', email: 'left@example.com', name: 'Left', is_active: false });

    const data = await getWebForm(event, 'f1');

    expect(data.missingAssignee).toEqual({
      id: 'gone',
      name: 'left@example.com',
      is_active: false
    });
  });

  it('is null when the assignee is in the picker', async () => {
    serve({ id: 'p1', email: 'ada@example.com', name: 'Ada', is_active: true });

    const data = await getWebForm(event, 'f1');

    expect(data.missingAssignee).toBeNull();
  });

  it('is null when the form has no assignee', async () => {
    serve(null);

    const data = await getWebForm(event, 'f1');

    expect(data.missingAssignee).toBeNull();
  });
});

describe('updateWebForm', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('resends the stored inactive assignee unchanged', async () => {
    // What the page posts when the admin saves without touching the picker:
    // the deactivated assignee's option is selected, so its id comes back.
    apiRequest.mockResolvedValue({});

    await updateWebForm(event, 'f1', { name: 'Contact us', assign_to: 'gone' });

    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/webforms/f1/');
    expect(opts.body.assign_to).toBe('gone');
  });
});
