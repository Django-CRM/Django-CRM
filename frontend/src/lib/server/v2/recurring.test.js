import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({
  apiRequest: (/** @type {any[]} */ ...args) => apiRequest(...args)
}));

const { createRecurringInvoice } = await import('./recurring.js');

const cookies = /** @type {any} */ ({});

describe('createRecurringInvoice', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ id: 'r1' });
  });

  it('posts to the recurring collection', async () => {
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'Monthly retainer' }
    );
    const [url, options] = apiRequest.mock.calls[0];
    expect(url).toBe('/invoices/recurring/');
    expect(options.method).toBe('POST');
  });

  it('drops server-derived keys a caller tries to set', async () => {
    await createRecurringInvoice(
      { cookies },
      {
        account_id: 'a1',
        contact_id: 'c1',
        title: 'Retainer',
        org: 'attacker-org',
        created_by: 'someone-else',
        total_amount: '999999',
        invoices_generated: 42,
        id: 'forged'
      }
    );
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.org).toBeUndefined();
    expect(options.body.created_by).toBeUndefined();
    expect(options.body.total_amount).toBeUndefined();
    expect(options.body.invoices_generated).toBeUndefined();
    expect(options.body.id).toBeUndefined();
  });

  it('requires an account and a contact', async () => {
    await expect(
      createRecurringInvoice({ cookies }, { contact_id: 'c1', title: 'x' })
    ).rejects.toThrow();
    await expect(
      createRecurringInvoice({ cookies }, { account_id: 'a1', title: 'x' })
    ).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('requires a title', async () => {
    await expect(
      createRecurringInvoice({ cookies }, { account_id: 'a1', contact_id: 'c1', title: '   ' })
    ).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('requires custom_days when the frequency is CUSTOM, as the server does', async () => {
    await expect(
      createRecurringInvoice(
        { cookies },
        { account_id: 'a1', contact_id: 'c1', title: 'x', frequency: 'CUSTOM' }
      )
    ).rejects.toThrow();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('refuses auto-send with no lines, as the server does', async () => {
    await expect(
      createRecurringInvoice(
        { cookies },
        { account_id: 'a1', contact_id: 'c1', title: 'x', auto_send: true }
      )
    ).rejects.toThrow('Add at least one line before turning on auto-send.');
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('accepts auto-send with a line, and no lines without auto-send', async () => {
    const line = { name: 'Hosting', quantity: 1, unit_price: 50 };
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'x', auto_send: true, line_items: [line] }
    );
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'x', auto_send: false }
    );
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  it('accepts CUSTOM with custom_days', async () => {
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'x', frequency: 'CUSTOM', custom_days: 45 }
    );
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.custom_days).toBe(45);
  });

  it('omits line_items entirely when none are given, rather than sending an empty array', async () => {
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'x', line_items: [] }
    );
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.line_items).toBeUndefined();
  });

  it('keeps an explicit empty string, so a field can be cleared', async () => {
    await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'x', notes: '' }
    );
    const [, options] = apiRequest.mock.calls[0];
    expect(options.body.notes).toBe('');
  });

  it('unwraps the created schedule from the envelope', async () => {
    apiRequest.mockResolvedValue({
      error: false,
      message: 'Recurring invoice created',
      recurring_invoice: { id: 'r1', title: 'Monthly retainer' }
    });
    const result = await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'Monthly retainer' }
    );
    expect(result).toEqual({ id: 'r1', title: 'Monthly retainer' });
  });

  it('falls back to the raw response if it has no recurring_invoice envelope', async () => {
    apiRequest.mockResolvedValue({ id: 'r1', title: 'Monthly retainer' });
    const result = await createRecurringInvoice(
      { cookies },
      { account_id: 'a1', contact_id: 'c1', title: 'Monthly retainer' }
    );
    expect(result).toEqual({ id: 'r1', title: 'Monthly retainer' });
  });
});
