import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { buildBody, eventsFromForm, listWebhooks, createWebhook, updateWebhook, getWebhook } =
  await import('$lib/server/v2/webhooks.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

describe('buildBody', () => {
  it('sends only the editable fields, never a secret or an org', () => {
    expect(
      buildBody({
        url: 'https://h.example.com/',
        events: ['lead.created'],
        format: 'json',
        secret: 'whsec_mine',
        org: 'attacker-org',
        secret_hint: 'whsec_...abcd'
      })
    ).toEqual({ url: 'https://h.example.com/', events: ['lead.created'], format: 'json' });
  });

  it('leaves out what the caller did not give, so a toggle stays a toggle', () => {
    expect(buildBody({ is_active: false })).toEqual({ is_active: false });
  });
});

describe('eventsFromForm', () => {
  it('reads every checked box', () => {
    const form = new FormData();
    form.append('events', 'lead.created');
    form.append('events', 'deal.won');
    expect(eventsFromForm(form)).toEqual(['lead.created', 'deal.won']);
  });
});

describe('listWebhooks', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('folds a member 403 into forbidden', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('no'), { status: 403 }));
    expect(await listWebhooks(event)).toEqual({ forbidden: true });
  });

  it('passes any other failure on', async () => {
    apiRequest.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }));
    await expect(listWebhooks(event)).rejects.toThrow('boom');
  });

  it('returns the endpoints and the catalogue', async () => {
    apiRequest.mockResolvedValue({
      endpoints: [{ id: '1' }],
      event_catalogue: [{ module: 'lead', label: 'Leads', events: ['lead.created'] }],
      limit: 10
    });
    const out = await listWebhooks(event);
    expect(out.forbidden).toBe(false);
    expect(out.endpoints).toHaveLength(1);
    expect(out.catalogue[0].module).toBe('lead');
    expect(out.limit).toBe(10);
  });
});

describe('writes', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('creates with POST and edits with PATCH, never PUT', async () => {
    apiRequest.mockResolvedValue({});
    await createWebhook(event, { url: 'https://h.example.com/', events: ['lead.created'] });
    await updateWebhook(event, 'abc', { is_active: true });
    expect(apiRequest.mock.calls[0][0]).toBe('/webhooks/');
    expect(apiRequest.mock.calls[0][1].method).toBe('POST');
    expect(apiRequest.mock.calls[1][0]).toBe('/webhooks/abc/');
    expect(apiRequest.mock.calls[1][1]).toEqual({ method: 'PATCH', body: { is_active: true } });
  });

  it('pages the delivery log by offset', async () => {
    apiRequest.mockResolvedValue({ results: [], count: 45 });
    const out = await getWebhook(event, 'abc', 20);
    expect(apiRequest.mock.calls[2][0]).toBe('/webhooks/abc/deliveries/?limit=20&offset=20');
    expect(out.deliveryCount).toBe(45);
    expect(out.offset).toBe(20);
  });
});
