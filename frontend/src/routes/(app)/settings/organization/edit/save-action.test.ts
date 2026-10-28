import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const { actions } = await import('./+page.server.js');

function saveEvent(fields: Record<string, string>) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return {
    request: new Request('http://test/settings/organization/edit?/save', { method: 'POST', body }),
    cookies: { get: () => 'token' }
  } as any;
}

describe('organization save action', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('sends the numbering settings (#802), trimmed', async () => {
    apiRequest.mockResolvedValue({});
    await expect(
      actions.save(
        saveEvent({
          invoice_number_prefix: ' INVNSD- ',
          estimate_number_prefix: 'ESTNSD-',
          number_date_part: 'NONE',
          number_padding: '5',
          invoice_number_start: '1042'
        })
      )
    ).rejects.toMatchObject({ status: 303, location: '/settings/organization' });

    expect(apiRequest.mock.calls[0][0]).toBe('/org/settings/');
    expect(apiRequest.mock.calls[0][1]).toMatchObject({ method: 'PATCH' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({
      invoice_number_prefix: 'INVNSD-',
      estimate_number_prefix: 'ESTNSD-',
      number_date_part: 'NONE',
      number_padding: '5',
      invoice_number_start: '1042'
    });
  });

  it('sends nothing the form does not offer', async () => {
    apiRequest.mockResolvedValue({});
    await expect(
      actions.save(saveEvent({ name: 'Acme', api_key: 'x', is_active: 'false' }))
    ).rejects.toMatchObject({ status: 303 });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ name: 'Acme' });
  });

  it('leaves the numbering and both switches alone when the form omits them', async () => {
    // The form with Invoices and Tickets switched off has none of those fields.
    apiRequest.mockResolvedValue({});
    await expect(
      actions.save(saveEvent({ name: 'Acme', default_currency: 'EUR', timezone: 'UTC' }))
    ).rejects.toMatchObject({ status: 303 });
    const body = apiRequest.mock.calls[0][1].body;
    expect(body).toEqual({ name: 'Acme', default_currency: 'EUR', timezone: 'UTC' });
  });

  it("keeps the server's reason when it refuses", async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('A starting number only applies to numbers without a date.'), {
        status: 400
      })
    );
    const result = (await actions.save(
      saveEvent({ number_date_part: 'YEAR', invoice_number_start: '1042' })
    )) as any;
    expect(result.status).toBe(400);
    expect(result.data.error).toContain('only applies to numbers without a date');
  });

  it('sends the reminder as a number, and Off as null', async () => {
    apiRequest.mockResolvedValue({});
    await expect(actions.save(saveEvent({ lead_reminder_minutes: '15' }))).rejects.toMatchObject({
      status: 303
    });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ lead_reminder_minutes: 15 });

    await expect(actions.save(saveEvent({ lead_reminder_minutes: '' }))).rejects.toMatchObject({
      status: 303
    });
    expect(apiRequest.mock.calls[1][1].body).toEqual({ lead_reminder_minutes: null });
  });
});
