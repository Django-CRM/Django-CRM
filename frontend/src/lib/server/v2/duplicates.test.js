import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { checkDuplicates, recordDuplicates, matchedLabel, mergeRoute, getMergePair } =
  await import('$lib/server/v2/duplicates.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';

/** @param {Record<string, string>} fields */
function formRequest(fields) {
  const body = new FormData();
  for (const [k, v] of Object.entries(fields)) body.set(k, v);
  return /** @type {any} */ ({ formData: async () => body });
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('matchedLabel', () => {
  it('joins the rules in plain words', () => {
    expect(matchedLabel(['email'])).toBe('email');
    expect(matchedLabel(['email', 'phone'])).toBe('email and phone');
    expect(matchedLabel(['email', 'phone', 'name'])).toBe('email, phone and name');
    expect(matchedLabel([])).toBe('');
  });
});

describe('checkDuplicates', () => {
  it("POSTs only the module's own non-blank fields, never in the URL", async () => {
    apiRequest.mockResolvedValue({ duplicates: [] });
    await checkDuplicates(event, 'contacts', {
      email: ' a@x.com ',
      phone: '',
      name: 'not a contact field',
      org: 'another-org',
      first_name: 42
    });
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/contacts/duplicates/');
    expect(options).toEqual({ method: 'POST', body: { email: 'a@x.com' } });
  });

  it('asks nothing when there is nothing to match on', async () => {
    expect(await checkDuplicates(event, 'accounts', {})).toEqual([]);
    expect(await checkDuplicates(event, 'accounts', /** @type {any} */ (null))).toEqual([]);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it.each(['invoices', '__proto__', 'constructor'])(
    'refuses %s, which is not a module with duplicates',
    async (module) => {
      await expect(checkDuplicates(event, module, { email: 'a@x.com' })).rejects.toMatchObject({
        status: 404
      });
      expect(apiRequest).not.toHaveBeenCalled();
    }
  );

  it('shapes each hit for the page', async () => {
    apiRequest.mockResolvedValue({
      duplicates: [
        {
          id: '1',
          name: 'Ann',
          email: 'a@x.com',
          phone: null,
          matched_on: ['email'],
          can_delete: 1
        }
      ]
    });
    const hits = await checkDuplicates(event, 'leads', { email: 'a@x.com' });
    expect(hits).toEqual([
      { id: '1', name: 'Ann', email: 'a@x.com', phone: '', matched_on: 'email', can_delete: true }
    ]);
  });
});

describe('recordDuplicates', () => {
  it('answers none when the check fails, so the page still loads', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }));
    expect(await recordDuplicates(event, 'accounts', A)).toEqual({
      can_delete: false,
      duplicates: []
    });
  });

  it('never builds a path from an id that is not a UUID', async () => {
    await expect(recordDuplicates(event, 'accounts', '../../org')).rejects.toMatchObject({
      status: 404
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('getMergePair', () => {
  it('turns a hidden or missing record into a 404', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('No'), { status: 404 }));
    await expect(getMergePair(event, 'contacts', A, B)).rejects.toMatchObject({ status: 404 });
  });

  it('refuses to compare a record with itself', async () => {
    await expect(getMergePair(event, 'contacts', A, A)).rejects.toMatchObject({ status: 404 });
  });

  it.each(['../../org/api-key', '../../../auth/me', 'nope', `${A}/../..`])(
    'refuses ?with=%s before any request is made',
    async (other) => {
      await expect(getMergePair(event, 'contacts', A, other)).rejects.toMatchObject({
        status: 404
      });
      expect(apiRequest).not.toHaveBeenCalled();
    }
  );

  it('still loads when a duplicates call fails, offering that record as not deletable', async () => {
    apiRequest.mockImplementation(async (/** @type {string} */ path) => {
      if (path.endsWith('/duplicates/')) throw Object.assign(new Error('boom'), { status: 500 });
      const id = path.split('/')[2];
      return { contact_obj: { id, first_name: 'Ann', last_name: id === A ? 'One' : 'Two' } };
    });
    const pair = await getMergePair(event, 'contacts', A, B);
    expect(pair.current.can_delete).toBe(false);
    expect(pair.other.can_delete).toBe(false);
    expect(pair.current.name).toBe('Ann One');
  });
});

describe('mergeRoute merge action', () => {
  const { actions } = mergeRoute('contacts');
  const params = { id: A };

  it('merges the other record into the one chosen, then opens the keeper', async () => {
    apiRequest.mockResolvedValue({ error: false, id: B });
    await expect(
      actions.merge({
        ...event,
        params,
        request: formRequest({ other: B, keep: B, confirm: 'on' })
      })
    ).rejects.toMatchObject({ status: 303, location: `/contacts/${B}` });
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe(`/contacts/${B}/merge/`);
    expect(options).toEqual({ method: 'POST', body: { merge_id: A } });
  });

  it('keeps only one of the two records on the page', async () => {
    const result = await actions.merge({
      ...event,
      params,
      request: formRequest({ other: B, keep: 'somebody-else', confirm: 'on' })
    });
    expect(result).toMatchObject({ status: 400 });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it.each(['../../org', 'nope', ''])('refuses other=%j as a 404', async (other) => {
    const result = await actions.merge({
      ...event,
      params,
      request: formRequest({ other, keep: other, confirm: 'on' })
    });
    expect(result).toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('asks for the confirmation first', async () => {
    const result = await actions.merge({
      ...event,
      params,
      request: formRequest({ other: B, keep: A })
    });
    expect(result).toMatchObject({ status: 400 });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("passes on the API's refusal", async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('forbidden'), {
        status: 403,
        body: { error: true, errors: 'You may not delete the record being merged away.' }
      })
    );
    const result = await actions.merge({
      ...event,
      params,
      request: formRequest({ other: B, keep: A, confirm: 'on' })
    });
    expect(result).toMatchObject({ status: 403 });
  });
});
