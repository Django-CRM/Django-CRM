import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const {
  stopTimer,
  listTicketTime,
  startTicketTimer,
  logTicketTime,
  setEntryBillable,
  deleteEntry,
  getTimeReport
} = await import('$lib/server/v2/timesheet.js');
// Cast rather than shaping a full Cookies mock, matching leads.test.js and
// tags.test.js: stopTimer only ever calls `cookies.get`, and `apiRequest`
// itself is mocked above, so nothing here touches
// `getAll`/`set`/`delete`/`serialize`. Without the cast svelte-check flags
// this object against SvelteKit's full `Cookies` type on every call site
// below, which is noise for a shape the test deliberately keeps minimal.
const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

describe('stopTimer', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('POSTs to the stop endpoint for the given entry', async () => {
    apiRequest.mockResolvedValue({ id: 'e1', ended_at: '2026-08-04T10:00:00Z' });
    const result = await stopTimer(event, 'e1');

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/time-entries/e1/stop/');
    expect(options.method).toBe('POST');
    expect(result.ended_at).toBeTruthy();
  });

  it('refuses a missing entry id rather than posting to a malformed path', async () => {
    await expect(stopTimer(event, '')).rejects.toThrow(/entry/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('the ticket panel', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('lists a ticket’s entries and hands back an array even when the API does not', async () => {
    apiRequest.mockResolvedValue([{ id: 'e1' }]);
    expect(await listTicketTime(event, 't1')).toEqual([{ id: 'e1' }]);
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/t1/time-entries/');

    // A 204, an error envelope, or a paginated object would each render as
    // `entries.length` of undefined in the panel.
    apiRequest.mockResolvedValue(null);
    expect(await listTicketTime(event, 't1')).toEqual([]);
  });

  it('starts a timer on the ticket, with no body to be trusted', async () => {
    apiRequest.mockResolvedValue({ id: 'e1', ended_at: null });
    await startTicketTimer(event, 't1');

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/cases/t1/time-entries/start/');
    expect(options.method).toBe('POST');
    expect(options.body).toBeUndefined();
  });

  it('turns minutes into a window ending now', async () => {
    apiRequest.mockResolvedValue({ id: 'e1' });
    await logTicketTime(event, 't1', { minutes: '45', description: '  Traced it  ' });

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/cases/t1/time-entries/');
    const spanMinutes =
      (Date.parse(options.body.ended_at) - Date.parse(options.body.started_at)) / 60000;
    expect(spanMinutes).toBe(45);
    expect(options.body.description).toBe('Traced it');
    expect(options.body.billable).toBe(false);
    // Absent, not null: the API takes the model default for both.
    expect('hourly_rate' in options.body).toBe(false);
    expect('currency' in options.body).toBe(false);
  });

  it('sends the rate and currency when it is given them', async () => {
    apiRequest.mockResolvedValue({ id: 'e1' });
    await logTicketTime(event, 't1', {
      minutes: 30,
      description: 'Call',
      billable: true,
      hourlyRate: '85',
      currency: 'EUR'
    });

    const { body } = apiRequest.mock.calls[0][1];
    expect(body.hourly_rate).toBe('85.00');
    expect(body.currency).toBe('EUR');
    expect(body.billable).toBe(true);
  });

  it('rejects a duration that is not a whole number of minutes inside a day', async () => {
    for (const minutes of ['0', '-30', '1.5', '1441', 'half an hour', '']) {
      await expect(logTicketTime(event, 't1', { minutes, description: 'x' })).rejects.toThrow(
        /minutes/i
      );
    }
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects an empty description, which the API requires, and a negative rate', async () => {
    await expect(logTicketTime(event, 't1', { minutes: 30, description: '   ' })).rejects.toThrow(
      /spent on/i
    );
    await expect(
      logTicketTime(event, 't1', { minutes: 30, description: 'x', hourlyRate: '-5' })
    ).rejects.toThrow(/positive/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('sends only the billable flag when flipping one, leaving the rest of the entry alone', async () => {
    apiRequest.mockResolvedValue({ id: 'e1', billable: true });
    await setEntryBillable(event, 'e1', true);

    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/time-entries/e1/');
    expect(options.method).toBe('PUT');
    expect(options.body).toEqual({ billable: true });
  });

  it('deletes by id, and refuses to send a request without one', async () => {
    apiRequest.mockResolvedValue(null);
    await deleteEntry(event, 'e1');
    expect(apiRequest.mock.calls[0][0]).toBe('/time-entries/e1/');
    expect(apiRequest.mock.calls[0][1].method).toBe('DELETE');

    apiRequest.mockReset();
    await expect(deleteEntry(event, '')).rejects.toThrow(/entry/i);
    await expect(listTicketTime(event, '')).rejects.toThrow(/ticket/i);
    await expect(startTicketTimer(event, '')).rejects.toThrow(/ticket/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('the time report', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ rows: [], totals: {}, currencies: [] });
  });

  /** The query string the helper sent, as a plain object. */
  function sentParams() {
    const [endpoint] = apiRequest.mock.calls[0];
    return Object.fromEntries(new URLSearchParams(endpoint.split('?')[1]));
  }

  it('passes the window and the grouping through', async () => {
    await getTimeReport(event, { start: '2026-08-01', end: '2026-08-31', group_by: 'account' });

    expect(sentParams()).toEqual({
      start: '2026-08-01',
      end: '2026-08-31',
      group_by: 'account'
    });
  });

  it('falls back to grouping by agent rather than forwarding a bad value', async () => {
    // Straight off the query string, so this is a hand-edited URL, not a bug.
    // Forwarded, the API answers 400 and the page becomes an error screen.
    await getTimeReport(event, { group_by: 'planet' });

    expect(sentParams().group_by).toBe('agent');
  });

  it('sends the billable filter only when it is one of the two the API takes', async () => {
    await getTimeReport(event, { billable: 'false' });
    expect(sentParams().billable).toBe('false');

    apiRequest.mockClear();
    await getTimeReport(event, { billable: 'maybe' });
    expect('billable' in sentParams()).toBe(false);
  });

  it('leaves the window off entirely when it is not given, so the API picks it', async () => {
    await getTimeReport(event, {});

    expect(sentParams()).toEqual({ group_by: 'agent' });
  });
});
