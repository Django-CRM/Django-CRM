import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const {
  getCustomObjects,
  listObjectsForSettings,
  getObject,
  listFields,
  deleteObject,
  turnOffField,
  listRecords,
  getRecord,
  createRecord,
  updateRecord,
  deleteRecord,
  uploadAttachment,
  deleteAttachment,
  getRelated,
  addRelated,
  removeRelated,
  searchLinkTargets,
  recordWriteError,
  recordPage,
  mayMassImport,
  resetObjectsAbsentMemo
} = await import('#lib/server/v2/custom-objects.js');

const event = { cookies: { get: () => 'token' } } as any;
const ID = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';
const ID2 = '1c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6c';
const status = (n: number) => Object.assign(new Error('failed'), { status: n });

beforeEach(() => {
  apiRequest.mockReset();
  resetObjectsAbsentMemo();
});

describe('getCustomObjects', () => {
  it('asks for the objects answer quietly', async () => {
    const answer = { has_custom_objects: true, objects: [], link_targets: [] };
    apiRequest.mockResolvedValueOnce(answer);
    expect(await getCustomObjects(event)).toBe(answer);
    expect(apiRequest).toHaveBeenCalledWith(
      '/objects/',
      { quietErrors: true, expectedStatuses: [404] },
      event
    );
  });

  it('answers null where the server has no custom objects', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    expect(await getCustomObjects(event)).toBeNull();
  });

  it('answers null on any other failure', async () => {
    apiRequest.mockRejectedValueOnce(status(500));
    expect(await getCustomObjects(event)).toBeNull();
    apiRequest.mockRejectedValueOnce(new Error('fetch failed'));
    expect(await getCustomObjects(event)).toBeNull();
  });

  it('answers null for a 200 body that is not the objects shape', async () => {
    apiRequest.mockResolvedValueOnce({ detail: 'ok' });
    expect(await getCustomObjects(event)).toBeNull();
    apiRequest.mockResolvedValueOnce({ objects: [], link_targets: 'x' });
    expect(await getCustomObjects(event)).toBeNull();
    apiRequest.mockResolvedValueOnce(null);
    expect(await getCustomObjects(event)).toBeNull();
  });
});

describe('the absent memo', () => {
  afterEach(() => vi.useRealTimers());
  const answer = { has_custom_objects: true, objects: [], link_targets: [] };

  it('skips the API for 5 minutes after a 404 from the objects list', async () => {
    vi.useFakeTimers();
    apiRequest.mockRejectedValueOnce(status(404));
    expect(await getCustomObjects(event)).toBeNull();
    expect(await getCustomObjects(event)).toBeNull();
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(5 * 60 * 1000 - 1);
    expect(await getCustomObjects(event)).toBeNull();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(2);
    apiRequest.mockResolvedValueOnce(answer);
    expect(await getCustomObjects(event)).toBe(answer);
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  it('sends Related to the objects list once it lapses, so a 404 there renews it', async () => {
    vi.useFakeTimers();
    apiRequest.mockRejectedValueOnce(status(404));
    await getCustomObjects(event);
    vi.advanceTimersByTime(5 * 60 * 1000 + 1);

    apiRequest.mockRejectedValueOnce(status(404));
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    expect(apiRequest.mock.calls.map(([url]) => url)).toEqual(['/objects/', '/objects/']);
  });

  it('is cleared by a 200 from the objects list', async () => {
    vi.useFakeTimers();
    apiRequest.mockRejectedValueOnce(status(404));
    await getCustomObjects(event);
    vi.advanceTimersByTime(5 * 60 * 1000 + 1);

    const related = { groups: [], can_edit: false, link_fields: [] };
    apiRequest.mockResolvedValueOnce(answer).mockResolvedValueOnce(related);
    expect(await getRelated(event, 'lead', ID)).toBe(related);
    apiRequest.mockResolvedValueOnce(related);
    expect(await getRelated(event, 'lead', ID)).toBe(related);
    expect(apiRequest.mock.calls.map(([url]) => url)).toEqual([
      '/objects/',
      '/objects/',
      `/objects/related/?to=lead&id=${ID}`,
      `/objects/related/?to=lead&id=${ID}`
    ]);
  });

  it('is not set by a 404 from the related endpoint', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    apiRequest.mockResolvedValueOnce(answer);
    expect(await getCustomObjects(event)).toBe(answer);
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  it('is not set by another failure', async () => {
    apiRequest.mockRejectedValueOnce(status(500));
    await getCustomObjects(event);
    apiRequest.mockResolvedValueOnce(answer);
    expect(await getCustomObjects(event)).toBe(answer);
  });
});

describe('listObjectsForSettings', () => {
  it('includes archived objects', async () => {
    apiRequest.mockResolvedValueOnce({ objects: [] });
    await listObjectsForSettings(event);
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/?include_archived=true');
  });

  it('turns a 404 into a page 404 that reads as a missing page', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    await expect(listObjectsForSettings(event)).rejects.toMatchObject({
      status: 404,
      body: { message: 'Custom objects are not available here.' }
    });
  });

  it.each([
    ['listObjectsForSettings', () => listObjectsForSettings(event)],
    ['getObject', () => getObject(event, 'property')],
    ['listFields', () => listFields(event, 'property')],
    ['listRecords', () => listRecords(event, 'property')],
    ['getRecord', () => getRecord(event, 'property', ID)]
  ])('%s expects a 404 so the backend log stays quiet', async (_name, call) => {
    apiRequest.mockResolvedValueOnce({});
    await call();
    expect(apiRequest.mock.calls[0][1]).toMatchObject({ expectedStatuses: [404] });
  });

  it('rethrows other failures unchanged', async () => {
    const err = status(500);
    apiRequest.mockRejectedValueOnce(err);
    await expect(listObjectsForSettings(event)).rejects.toBe(err);
  });
});

describe('keys and ids', () => {
  it.each(['../leads', 'Property', 'a', 'x'.repeat(29), 'obj:property'])(
    'refuses the object key %s without calling the API',
    async (key) => {
      await expect(getObject(event, key)).rejects.toMatchObject({ status: 404 });
      expect(apiRequest).not.toHaveBeenCalled();
    }
  );

  it('refuses a malformed field key without calling the API', async () => {
    await expect(turnOffField(event, 'property', '../x')).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('refuses a malformed record id without calling the API', async () => {
    await expect(getRecord(event, 'property', 'not-a-uuid')).rejects.toMatchObject({
      status: 404
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('objects and fields', () => {
  it('turns an object 404 into the object message', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    await expect(getObject(event, 'property')).rejects.toMatchObject({
      status: 404,
      body: { message: 'That object does not exist, or you do not have access to it.' }
    });
  });

  it('returns the fields array', async () => {
    apiRequest.mockResolvedValueOnce({ fields: [{ key: 'price' }] });
    expect(await listFields(event, 'property')).toEqual([{ key: 'price' }]);
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/fields/');
  });

  it('resolves null when an archived object is deleted for good', async () => {
    apiRequest.mockResolvedValueOnce(null);
    expect(await deleteObject(event, 'property')).toBeNull();
    expect(apiRequest.mock.calls[0][1].method).toBe('DELETE');
  });

  it('turns a field off with a DELETE', async () => {
    apiRequest.mockResolvedValueOnce({ key: 'price', is_active: false });
    await turnOffField(event, 'property', 'price');
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/fields/price/');
    expect(apiRequest.mock.calls[0][1].method).toBe('DELETE');
  });
});

describe('records', () => {
  it('lists with limit 20 and an offset from the page', async () => {
    apiRequest.mockResolvedValueOnce({ count: 41, results: [] });
    const answer = await listRecords(event, 'property', { q: '12 Park', page: 3 });
    expect(answer).toEqual({ count: 41, results: [] });
    expect(apiRequest.mock.calls[0][0]).toBe(
      '/objects/property/records/?q=12%20Park&limit=20&offset=40'
    );
  });

  it('sends the filters repeated, after q and before the paging', async () => {
    apiRequest.mockResolvedValueOnce({ count: 0, results: [] });
    const filters = new URLSearchParams('q=elm&tags=t1&tags=t2&cf_pets=true&limit=500&offset=9');
    await listRecords(event, 'property', { page: 2, filters });
    expect(apiRequest.mock.calls[0][0]).toBe(
      '/objects/property/records/?q=elm&tags=t1&tags=t2&cf_pets=true&limit=20&offset=20'
    );
  });

  it('starts at offset 0 and leaves out an empty search', async () => {
    apiRequest.mockResolvedValueOnce({ count: 0, results: [] });
    await listRecords(event, 'property', { q: '', page: 1 });
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/records/?limit=20&offset=0');
  });

  it('turns a record 404 into a page 404', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    await expect(getRecord(event, 'property', ID)).rejects.toMatchObject({
      status: 404,
      body: { message: 'That record does not exist, or you do not have access to it.' }
    });
  });

  it('turns a record write 404 into a page 404', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    await expect(createRecord(event, 'property', { name: 'x' })).rejects.toMatchObject({
      status: 404
    });
    apiRequest.mockRejectedValueOnce(status(404));
    await expect(deleteRecord(event, 'property', ID)).rejects.toMatchObject({ status: 404 });
  });

  it('rethrows a refused write unchanged', async () => {
    const err = Object.assign(new Error('bad'), { status: 400, body: { name: ['Required.'] } });
    apiRequest.mockRejectedValueOnce(err);
    await expect(updateRecord(event, 'property', ID, { name: '' })).rejects.toBe(err);
  });

  it('saves a record with PATCH', async () => {
    apiRequest.mockResolvedValueOnce({ id: ID });
    await updateRecord(event, 'property', ID, { name: 'x' });
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe(`/objects/property/records/${ID}/`);
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ name: 'x' });
  });

  it('creates a record with POST', async () => {
    apiRequest.mockResolvedValueOnce({ id: ID });
    await createRecord(event, 'property', { name: 'x' });
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/records/');
    expect(apiRequest.mock.calls[0][1].method).toBe('POST');
  });
});

describe('attachments', () => {
  it('uploads the file as the multipart field "file"', async () => {
    const file = new File(['hi'], 'a.txt');
    apiRequest.mockResolvedValueOnce({ id: ID2 });
    await uploadAttachment(event, 'property', ID, file);
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe(`/objects/property/records/${ID}/attachments/`);
    expect(options.method).toBe('POST');
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get('file')).toBe(file);
  });

  it('deletes an attachment', async () => {
    apiRequest.mockResolvedValueOnce(null);
    await deleteAttachment(event, 'property', ID, ID2);
    expect(apiRequest.mock.calls[0][0]).toBe(`/objects/property/records/${ID}/attachments/${ID2}/`);
    expect(apiRequest.mock.calls[0][1].method).toBe('DELETE');
  });

  it('refuses a malformed attachment id without calling the API', async () => {
    await expect(deleteAttachment(event, 'property', ID, 'nope')).rejects.toMatchObject({
      status: 404
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('related', () => {
  it('asks quietly and answers null on any failure', async () => {
    apiRequest.mockRejectedValueOnce(status(404));
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    expect(apiRequest).toHaveBeenCalledWith(
      `/objects/related/?to=lead&id=${ID}`,
      { quietErrors: true, expectedStatuses: [404] },
      event
    );
  });

  it('answers null for a 200 body that is not the related shape', async () => {
    apiRequest.mockResolvedValueOnce({ detail: 'ok' });
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    apiRequest.mockResolvedValueOnce({ groups: [], link_fields: null });
    expect(await getRelated(event, 'lead', ID)).toBeNull();
    const good = { groups: [], can_edit: false, link_fields: [] };
    apiRequest.mockResolvedValueOnce(good);
    expect(await getRelated(event, 'lead', ID)).toBe(good);
  });

  it('answers null without calling the API for a bad target or id', async () => {
    expect(await getRelated(event, 'invoice', ID)).toBeNull();
    expect(await getRelated(event, 'lead', 'nope')).toBeNull();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('asks about a custom record with obj:<key>', async () => {
    apiRequest.mockResolvedValueOnce({ groups: [], can_edit: false, link_fields: [] });
    await getRelated(event, 'obj:batch', ID);
    expect(apiRequest.mock.calls[0][0]).toBe(`/objects/related/?to=obj%3Abatch&id=${ID}`);
  });

  it('refuses a bad obj: key without calling the API', async () => {
    expect(await getRelated(event, 'obj:Bad Key', ID)).toBeNull();
    expect(await getRelated(event, 'obj:', ID)).toBeNull();
    await expect(
      addRelated(event, { to: 'obj:Bad Key', id: ID, record: ID2, field: 'enquiries' })
    ).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('adds a link with a POST body', async () => {
    apiRequest.mockResolvedValueOnce({ added: true });
    await addRelated(event, { to: 'lead', id: ID, record: ID2, field: 'enquiries' });
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe('/objects/related/');
    expect(options.method).toBe('POST');
    expect(options.body).toEqual({ to: 'lead', id: ID, record: ID2, field: 'enquiries' });
  });

  it('removes a Related link with a DELETE carrying the query', async () => {
    apiRequest.mockResolvedValueOnce(null);
    await removeRelated(event, { to: 'lead', id: ID, record: ID2, field: 'enquiries' });
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe(`/objects/related/?to=lead&id=${ID}&record=${ID2}&field=enquiries`);
    expect(options.method).toBe('DELETE');
  });

  it('refuses a malformed field key on a write without calling the API', async () => {
    await expect(
      removeRelated(event, { to: 'lead', id: ID, record: ID2, field: '../x' })
    ).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('searchLinkTargets', () => {
  const person = { id: 'p1', email: 'a@b.c' };
  const fixtures: Record<string, [string, any, string]> = {
    lead: [
      '/leads/',
      { open_leads: { open_leads: [{ id: 'x1', first_name: 'Ada', last_name: 'Lovelace' }] } },
      'Ada Lovelace'
    ],
    contact: [
      '/contacts/',
      { count: 1, results: [{ id: 'x1', first_name: 'Ada', last_name: 'Lovelace' }] },
      'Ada Lovelace'
    ],
    account: [
      '/accounts/',
      { active_accounts: { open_accounts: [{ id: 'x1', name: 'Acme' }] } },
      'Acme'
    ],
    deal: ['/opportunities/', { opportunities: [{ id: 'x1', name: 'Big deal' }] }, 'Big deal'],
    ticket: [
      '/cases/',
      { cases: [{ id: 'x1', name: 'Broken tap', assigned_to: [person] }] },
      'Broken tap'
    ],
    task: ['/tasks/', { tasks: [{ id: 'x1', title: 'Call back' }] }, 'Call back']
  };

  it.each([
    ['lead', '/leads/'],
    ['contact', '/contacts/'],
    ['account', '/accounts/'],
    ['deal', '/opportunities/'],
    ['ticket', '/cases/'],
    ['task', '/tasks/']
  ])('searches %s records through %s', async (target, endpoint) => {
    const [, body, name] = fixtures[target];
    apiRequest.mockResolvedValueOnce(body);
    expect(await searchLinkTargets(event, target, '12 Park')).toEqual([{ id: 'x1', name }]);
    const url = apiRequest.mock.calls[0][0];
    expect(url.startsWith(`${endpoint}?`)).toBe(true);
    const params = new URL(url, 'http://x').searchParams;
    expect(params.get('search')).toBe('12 Park');
    expect(params.get('limit')).toBe('20');
  });

  it('names a lead with only a company, then only an email, then Untitled', async () => {
    apiRequest.mockResolvedValueOnce({
      open_leads: {
        open_leads: [
          { id: 'a', first_name: '', last_name: '', company_name: 'Acme Ltd', email: 'x@y.z' },
          { id: 'b', first_name: '', last_name: '', email: 'x@y.z' },
          { id: 'c', first_name: '', last_name: '' }
        ]
      }
    });
    expect((await searchLinkTargets(event, 'lead', '')).map((r) => r.name)).toEqual([
      'Acme Ltd',
      'x@y.z',
      'Untitled'
    ]);
  });

  it('trims the query and still searches when it is empty', async () => {
    apiRequest.mockResolvedValueOnce({ opportunities: [] });
    await searchLinkTargets(event, 'deal', '   ');
    const params = new URL(apiRequest.mock.calls[0][0], 'http://x').searchParams;
    expect(params.get('search') ?? '').toBe('');
    expect(params.get('limit')).toBe('20');
  });

  it('keeps at most 20 rows', async () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ id: `d${i}`, name: `Deal ${i}` }));
    apiRequest.mockResolvedValueOnce({ opportunities: rows });
    expect(await searchLinkTargets(event, 'deal', '')).toHaveLength(20);
  });

  it('searches a custom object through its records', async () => {
    apiRequest.mockResolvedValueOnce({ count: 1, results: [{ id: 'r1', name: '12 Park Lane' }] });
    expect(await searchLinkTargets(event, 'obj:property', '12 Park')).toEqual([
      { id: 'r1', name: '12 Park Lane' }
    ]);
    expect(apiRequest.mock.calls[0][0]).toBe('/objects/property/records/?q=12%20Park&limit=20');
  });

  it.each(['invoice', 'obj:../x', 'obj:', 'constructor', 'toString', '__proto__'])(
    'finds nothing for %s without calling the API',
    async (target) => {
      expect(await searchLinkTargets(event, target, 'x')).toEqual([]);
      expect(apiRequest).not.toHaveBeenCalled();
    }
  );
});

describe('recordWriteError', () => {
  const fields = [
    { key: 'price', label: 'Price' },
    { key: 'owner', label: 'Owner' }
  ];

  it('names the refused field in a record error', () => {
    const err = {
      status: 400,
      body: {
        custom_fields: { price: 'must be at most 1000 characters' },
        links: { owner: 'This field links to a record you cannot open.' }
      }
    };
    expect(recordWriteError(err, fields)).toBe(
      'Price: must be at most 1000 characters\nOwner: This field links to a record you cannot open.'
    );
  });

  it('says an assignee refusal as its own line beside the other lines', () => {
    const err = {
      status: 400,
      body: {
        assigned_to: ['Choose active members of this organization.'],
        custom_fields: { price: 'must be at most 1000 characters' }
      }
    };
    expect(recordWriteError(err, fields)).toBe(
      'Price: must be at most 1000 characters\nAssignees: Choose active members of this organization.'
    );
  });

  it('labels the name with name_label and reads a message list', () => {
    const err = { status: 400, body: { name: ['This field is required.'] } };
    expect(recordWriteError(err, fields, 'Address')).toBe('Address: This field is required.');
  });

  it('says the size refusal on its own', () => {
    const err = {
      status: 400,
      body: { custom_fields: { _size: 'Custom field values can total 65536 bytes.' } }
    };
    expect(recordWriteError(err, fields)).toBe('Custom field values can total 65536 bytes.');
  });

  it('uses detail as is', () => {
    const err = { status: 403, body: { detail: 'Your organization plan does not include this.' } };
    expect(recordWriteError(err, fields)).toBe('Your organization plan does not include this.');
  });

  it('falls back to the readable error', () => {
    expect(recordWriteError(new Error(''), fields)).toBe('Could not save this record.');
  });
});

describe('recordPage', () => {
  const attachment = (id: string, file_name: string, is_image: boolean) => ({
    id,
    file_name,
    is_image,
    can_delete: true
  });
  const definition = { key: 'property', label: 'Property', is_active: true, fields: [] };

  it('splits photos from files and gives each a proxy href', async () => {
    const house = attachment(ID, 'house.jpg', true);
    const plan = attachment(ID2, 'plan.svg', false);
    const notes = attachment('2d9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6d', 'notes.pdf', false);
    apiRequest.mockImplementation(async (path) =>
      path === '/objects/property/'
        ? definition
        : { id: ID, name: '12 Park Lane', attachments: [house, plan, notes] }
    );
    const page = await recordPage(event, 'property', ID);
    expect(page.definition).toBe(definition);
    expect(page.photos.map((a: any) => a.file_name)).toEqual(['house.jpg']);
    expect(page.files.map((a: any) => a.file_name)).toEqual(['plan.svg', 'notes.pdf']);
    expect(page.photos[0].href).toBe(`/api/attachments/${ID}/download`);
    expect(page.files[0].href).toBe(`/api/attachments/${ID2}/download`);
    expect(page.record.id).toBe(ID);
  });

  it('answers 404 for an archived object', async () => {
    apiRequest.mockImplementation(async (path) =>
      path === '/objects/property/'
        ? { ...definition, is_active: false }
        : { id: ID, name: 'x', attachments: [] }
    );
    await expect(recordPage(event, 'property', ID)).rejects.toMatchObject({ status: 404 });
  });

  it('answers 404 for a bad id without calling the API', async () => {
    await expect(recordPage(event, 'property', 'nope')).rejects.toMatchObject({ status: 404 });
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('mayMassImport', () => {
  it('lets an admin without asking', async () => {
    apiRequest.mockReset();
    expect(await mayMassImport(event, true)).toBe(true);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('follows has_sales_access for a member, and refuses when the profile cannot be read', async () => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValueOnce({ user_obj: { has_sales_access: true } });
    expect(await mayMassImport(event, false)).toBe(true);
    apiRequest.mockResolvedValueOnce({ user_obj: { has_sales_access: false } });
    expect(await mayMassImport(event, false)).toBe(false);
    apiRequest.mockRejectedValueOnce(status(500));
    expect(await mayMassImport(event, false)).toBe(false);
  });
});
