import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const leadsModule = await import('#lib/server/v2/leads.js');
const { createLead, convertLead, updateLead, getLeadForEdit } = leadsModule;
// Cast rather than shaping a full Cookies mock: createLead only ever calls
// `cookies.get`, and `apiRequest` itself is mocked above, so nothing here
// touches `getAll`/`set`/`delete`/`serialize`. Without the cast svelte-check
// flags this object against SvelteKit's full `Cookies` type on every call site
// below, which is noise for a shape the test deliberately keeps minimal.
const event = { cookies: { get: () => 'token' } } as any;

describe('createLead', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('POSTs to /leads/ and returns the created lead', async () => {
    apiRequest.mockResolvedValue({ id: 'abc', first_name: 'Ada' });
    const result = await createLead(event, { first_name: 'Ada', last_name: 'Lovelace' });

    expect(apiRequest).toHaveBeenCalledOnce();
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/leads/');
    expect(options.method).toBe('POST');
    expect(options.body.first_name).toBe('Ada');
    expect(result).toMatchObject({ id: 'abc' });
  });

  it('sends an empty field as null rather than an empty string', async () => {
    apiRequest.mockResolvedValue({ id: 'abc' });
    await createLead(event, { first_name: 'Ada', job_title: '' });
    expect(apiRequest.mock.calls[0][1].body.job_title).toBeNull();
  });

  it('coerces opportunity_amount to a number', async () => {
    apiRequest.mockResolvedValue({ id: 'abc' });
    await createLead(event, { first_name: 'Ada', opportunity_amount: '4200.50' });
    expect(apiRequest.mock.calls[0][1].body.opportunity_amount).toBe(4200.5);
  });

  it('wraps a single owner id in a list, matching the API contract', async () => {
    apiRequest.mockResolvedValue({ id: 'abc' });
    await createLead(event, { first_name: 'Ada', assigned_to: 'profile-1' });
    expect(apiRequest.mock.calls[0][1].body.assigned_to).toEqual(['profile-1']);
  });

  it('never forwards a client-supplied org, which the backend derives from the JWT', async () => {
    apiRequest.mockResolvedValue({ id: 'abc' });
    await createLead(event, { first_name: 'Ada', org: 'attacker-org', created_by: 'someone' });
    const body = apiRequest.mock.calls[0][1].body;
    expect(body.org).toBeUndefined();
    expect(body.created_by).toBeUndefined();
  });
});

describe('convertLead', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PATCHes the lead detail URL with status: converted, and nothing else', async () => {
    apiRequest.mockResolvedValue({
      error: false,
      account_id: 'acc-1',
      contact_id: 'con-1',
      opportunity_id: 'opp-1'
    });
    const result = await convertLead(event, 'lead-1');

    expect(apiRequest).toHaveBeenCalledOnce();
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/leads/lead-1/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ status: 'converted' });
    expect(result.account_id).toBe('acc-1');
  });

  it('refuses to call the API without a lead id', async () => {
    await expect(convertLead(event, '')).rejects.toThrow(/id is required/);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('lets a rejected conversion (e.g. the email guard) propagate to the caller', async () => {
    const rejection = Object.assign(new Error('{"error":true,"errors":{"email":["required"]}}'), {
      status: 400
    });
    apiRequest.mockRejectedValue(rejection);
    await expect(convertLead(event, 'lead-1')).rejects.toBe(rejection);
  });
});

describe('the source fields', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('sends source, sub-source and reference as typed, and empty ones as empty strings', async () => {
    apiRequest.mockResolvedValue({});
    await createLead(event, {
      first_name: 'Ada',
      source: 'google ads',
      source_detail: 'Spring open day',
      source_ref: ''
    });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.source).toBe('google ads');
    expect(body.source_detail).toBe('Spring open day');
    expect(body.source_ref).toBe('');
    expect(body.job_title).toBeNull();

    await createLead(event, { first_name: 'Ada' });
    const second = apiRequest.mock.calls[1][1].body;
    expect([second.source, second.source_detail, second.source_ref]).toEqual(['', '', '']);
  });

  it('clears a sub-source on edit with an empty string, and leaves absent ones out', async () => {
    apiRequest.mockResolvedValue({});
    await updateLead(event, 'lead-1', { source_detail: '', source: 'walk-in' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ source_detail: '', source: 'walk-in' });
  });

  it('prefills the edit form with the stored sub-source and reference', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/leads/lead-1/') {
        return {
          lead_obj: {
            id: 'lead-1',
            first_name: 'Ada',
            source: 'fax',
            source_detail: 'Zillow',
            source_ref: 'L-42',
            assigned_to: [],
            custom_fields: {}
          }
        };
      }
      return {};
    });
    const { form, lead } = await getLeadForEdit(event, 'lead-1');
    expect(form).toMatchObject({ source: 'fax', source_detail: 'Zillow', source_ref: 'L-42' });
    expect(lead.source_detail).toBe('Zillow');
  });
});

describe('unanswered leads and response times', () => {
  const { listLeads, getResponseTimes, responseDays } = leadsModule as any;
  const row = (extra: Record<string, any>) => ({
    id: extra.id,
    first_name: extra.id,
    status: 'assigned',
    created_at: '2026-10-20T09:00:00Z',
    last_contacted: null,
    assigned_to: [],
    ...extra
  });

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('forwards ?unanswered=true and keeps the oldest wait first', async () => {
    apiRequest.mockResolvedValueOnce({
      open_leads: {
        open_leads: [
          row({ id: 'b', is_unanswered: true, waiting_since: '2026-10-24T08:00:00Z' }),
          row({ id: 'a', is_unanswered: true, waiting_since: '2026-10-23T08:00:00Z' })
        ]
      },
      totals: { count: 2, unworked_over_a_week: 0 }
    });
    const { results, totals, responseLive } = await listLeads(
      event,
      new URLSearchParams({ unanswered: 'true' })
    );
    expect(apiRequest.mock.calls[0][0]).toBe('/leads/?unanswered=true&limit=25');
    expect(results.map((r: any) => r.id)).toEqual(['a', 'b']);
    expect(results[0]).toMatchObject({
      is_unanswered: true,
      waiting_since: '2026-10-23T08:00:00Z'
    });
    expect(totals.count).toBe(2);
    expect(responseLive).toBe(true);
  });

  it('carries the response fields and the E.164 phone on every row', async () => {
    apiRequest.mockResolvedValueOnce({
      open_leads: {
        open_leads: [
          row({
            id: 'a',
            phone_e164: '+919876543210',
            is_unanswered: false,
            first_response_at: '2026-10-20T09:12:00Z',
            first_response_by_details: { id: 'p1', name: 'Maria Lopez' }
          })
        ]
      }
    });
    const { results } = await listLeads(event);
    expect(results[0]).toMatchObject({
      phone_e164: '+919876543210',
      first_response_at: '2026-10-20T09:12:00Z',
      first_response_by: 'Maria Lopez',
      is_unanswered: false,
      waiting_since: null
    });
  });

  it('tells an API without response tracking by its rows, and cannot tell from none', async () => {
    apiRequest.mockResolvedValueOnce({ open_leads: { open_leads: [row({ id: 'a' })] } });
    expect((await listLeads(event, new URLSearchParams({ unanswered: 'true' }))).responseLive).toBe(
      false
    );
    apiRequest.mockResolvedValueOnce({ open_leads: { open_leads: [] } });
    expect((await listLeads(event)).responseLive).toBeNull();
  });

  it('reads response times for the period asked, and shapes each row', async () => {
    apiRequest.mockResolvedValueOnce({
      since: '2026-09-24',
      rows: [
        {
          profile: 'p1',
          name: 'Maria Lopez',
          answered: 12,
          median_seconds: 420,
          within_5_minutes: 42,
          within_1_hour: 92,
          unanswered_now: 1,
          email: 'not read'
        },
        { profile: 'p2', name: '', answered: 0, median_seconds: null }
      ]
    });
    const { since, rows } = await getResponseTimes(event, 30);
    expect(apiRequest.mock.calls[0][0]).toBe('/leads/response-times/?days=30');
    expect(since).toBe('2026-09-24');
    expect(rows[0]).toEqual({
      profile: 'p1',
      name: 'Maria Lopez',
      answered: 12,
      median_seconds: 420,
      within_5_minutes: 42,
      within_1_hour: 92,
      unanswered_now: 1
    });
    expect(rows[1]).toMatchObject({ name: 'Unknown', median_seconds: null, unanswered_now: 0 });
  });

  it('asks only for a period the API takes', async () => {
    expect([responseDays('7'), responseDays('90'), responseDays('30')]).toEqual([7, 90, 30]);
    expect([responseDays('6'), responseDays('91'), responseDays('x'), responseDays(null)]).toEqual([
      30, 30, 30, 30
    ]);
    apiRequest.mockResolvedValueOnce({ since: null, rows: [] });
    await getResponseTimes(event, 365);
    expect(apiRequest.mock.calls[0][0]).toBe('/leads/response-times/?days=30');
  });

  it('answers a page 404 on an API without response times, and the module page when Leads is off', async () => {
    apiRequest.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 404, body: {} }));
    await expect(getResponseTimes(event, 30)).rejects.toMatchObject({
      status: 404,
      body: { message: 'Response times are not available here.' }
    });
    apiRequest.mockRejectedValueOnce(
      Object.assign(new Error('x'), {
        status: 404,
        body: { code: 'module_off', detail: 'Leads is switched off.', module: 'leads' }
      })
    );
    await expect(getResponseTimes(event, 30)).rejects.toMatchObject({
      status: 404,
      body: { code: 'module_off' }
    });
  });
});

describe('outcomes', () => {
  const { getOutcomes } = leadsModule as any;

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks for the period and returns the API shape', async () => {
    apiRequest.mockResolvedValueOnce({
      since: '2026-09-28',
      measured_since: '2026-10-20',
      rows: [
        {
          profile: 'p1',
          name: 'Maria Lopez',
          won: 3,
          lost: 1,
          win_rate: 75,
          median_seconds_to_win: 86400,
          new_leads: 9,
          email: 'not read'
        },
        { profile: 'p2', name: '', won: 0, lost: 0, win_rate: null, median_seconds_to_win: null }
      ],
      totals: { won: 3, lost: 1, win_rate: 75, median_seconds_to_win: 86400, new_leads: 12 }
    });
    const out = await getOutcomes(event, 30);
    expect(apiRequest.mock.calls[0][0]).toBe('/leads/outcomes/?days=30');
    expect(out.since).toBe('2026-09-28');
    expect(out.measured_since).toBe('2026-10-20');
    expect(out.rows[0]).toEqual({
      profile: 'p1',
      name: 'Maria Lopez',
      won: 3,
      lost: 1,
      win_rate: 75,
      median_seconds_to_win: 86400,
      new_leads: 9
    });
    expect(out.rows[1]).toMatchObject({
      name: 'Unknown',
      win_rate: null,
      median_seconds_to_win: null,
      new_leads: 0
    });
    expect(out.totals).toEqual({
      won: 3,
      lost: 1,
      win_rate: 75,
      median_seconds_to_win: 86400,
      new_leads: 12
    });
  });

  it('keeps totals null for a non-admin and clamps the period', async () => {
    apiRequest.mockResolvedValueOnce({
      since: '2026-10-21',
      measured_since: '2026-10-21',
      rows: [],
      totals: null
    });
    const out = await getOutcomes(event, 365);
    expect(apiRequest.mock.calls[0][0]).toBe('/leads/outcomes/?days=30');
    expect(out.totals).toBeNull();
  });

  it('answers a page 404 on an API without outcomes', async () => {
    apiRequest.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 404, body: {} }));
    await expect(getOutcomes(event, 30)).rejects.toMatchObject({
      status: 404,
      body: { message: 'Outcomes are not available here.' }
    });
  });
});
