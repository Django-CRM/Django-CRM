import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { listInvoices, getInvoice, FILTER_FIELDS } = await import('$lib/server/v2/invoices.js');
const { readFilters, buildFilterQuery } = await import('$lib/server/v2/filter-params.js');

// Cast rather than shaping a full Cookies mock: listInvoices only ever calls
// `cookies.get` (through the mocked apiRequest), so nothing here touches
// `getAll`/`set`/`delete`/`serialize`. See the same note in leads.test.js.
const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/** The querystring `listInvoices` actually sent to `apiRequest`. */
function sentQuery() {
  const [endpoint] = apiRequest.mock.calls[0];
  return new URL(`http://x${endpoint}`).searchParams;
}

describe('listInvoices', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('defaults limit to 100 and sorts by due_date when no params are given', async () => {
    apiRequest.mockResolvedValue({ results: [], totals: {} });
    await listInvoices(event, undefined);

    expect(apiRequest).toHaveBeenCalledOnce();
    const query = sentQuery();
    expect(query.get('limit')).toBe('100');
    expect(query.get('sort')).toBe('due_date');
  });

  it('forwards a caller-supplied due_date_gte/due_date_lte through to the API querystring', async () => {
    // Pinned against backend/invoices/api_views.py:149-152
    // (InvoiceListView.filter_queryset), which reads the SINGLE-underscore
    // due_date_gte / due_date_lte. Every other v2 module's date-range field
    // uses two underscores (due_date__gte). If this regresses to the
    // double-underscore spelling, the endpoint silently ignores the filter
    // and returns every invoice rather than erroring, which is why this is
    // pinned at both the readFilters layer (filter-params.test.js) and here,
    // end to end through the URL this module actually sends.
    apiRequest.mockResolvedValue({ results: [], totals: {} });
    const url = new URL('http://x/invoices?due_date_gte=2026-01-01&due_date_lte=2026-01-31');
    const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'invoices'));

    await listInvoices(event, params);

    const query = sentQuery();
    expect(query.get('due_date_gte')).toBe('2026-01-01');
    expect(query.get('due_date_lte')).toBe('2026-01-31');
    expect(query.has('due_date__gte')).toBe(false);
    expect(query.has('due_date__lte')).toBe(false);
  });

  it('forwards status, account and assigned_to unmodified', async () => {
    apiRequest.mockResolvedValue({ results: [], totals: {} });
    const acc = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
    const who = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';
    const url = new URL(`http://x/invoices?status=Overdue&account=${acc}&assigned_to=${who}`);
    const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'invoices'));

    await listInvoices(event, params);

    const query = sentQuery();
    expect(query.get('status')).toBe('Overdue');
    expect(query.get('account')).toBe(acc);
    expect(query.get('assigned_to')).toBe(who);
  });

  it('drops a param the descriptor does not declare, e.g. a hand-typed contact filter', async () => {
    apiRequest.mockResolvedValue({ results: [], totals: {} });
    const url = new URL('http://x/invoices?contact=con-1');
    const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'invoices'));

    await listInvoices(event, params);

    expect(sentQuery().has('contact')).toBe(false);
  });

  it('falls back to response.count when totals.count is absent', async () => {
    apiRequest.mockResolvedValue({ results: [{ id: '1' }], count: 7, totals: {} });
    const { totals } = await listInvoices(event, new URLSearchParams());
    expect(totals.count).toBe(7);
  });

  it('keeps the money per currency, coerced to numbers', async () => {
    apiRequest.mockResolvedValue({
      results: [],
      totals: {
        count: 2,
        action_needed: 1,
        outstanding: '150.50',
        by_currency: [
          { currency: 'EUR', count: 2, outstanding: '150.50', overdue: '0', draft: null }
        ]
      }
    });
    const { totals } = await listInvoices(event, new URLSearchParams());
    expect(totals.currencies).toEqual(['EUR']);
    expect(totals.byCurrency.EUR.outstanding).toBe(150.5);
    expect(totals.byCurrency.EUR.overdue).toBe(0);
    expect(totals.byCurrency.EUR.draft).toBe(0);
    expect(totals.action_needed).toBe(1);
  });

  it('never adds two currencies together', async () => {
    apiRequest.mockResolvedValue({
      results: [],
      totals: {
        count: 2,
        outstanding: null,
        by_currency: [
          { currency: 'EUR', count: 1, outstanding: '3.00' },
          { currency: 'USD', count: 1, outstanding: '40.00' }
        ]
      }
    });
    const { totals } = await listInvoices(event, new URLSearchParams());
    expect(totals.currencies).toEqual(['EUR', 'USD']);
    expect(totals.byCurrency.EUR.outstanding).toBe(3);
    expect(totals.byCurrency.USD.outstanding).toBe(40);
    expect(totals).not.toHaveProperty('outstanding');
  });

  it('has an all-zero view when nothing is visible', async () => {
    apiRequest.mockResolvedValue({ results: [], totals: { count: 0, by_currency: [] } });
    const { totals } = await listInvoices(event, new URLSearchParams());
    expect(totals.currencies).toEqual([]);
    expect(totals.blank).toEqual({
      outstanding: 0,
      overdue: 0,
      due_this_month: 0,
      paid_this_quarter: 0,
      draft: 0
    });
  });

  it('maps a result row through toRow, rebuilding the nested account shape', async () => {
    apiRequest.mockResolvedValue({
      results: [
        {
          id: 'inv-1',
          invoice_number: 'INV-0001',
          status: 'Draft',
          account: 'acc-1',
          account_name: 'Northwind',
          total_amount: '500.00'
        }
      ],
      totals: {}
    });
    const { invoices } = await listInvoices(event, new URLSearchParams());
    expect(invoices[0].account).toEqual({ id: 'acc-1', name: 'Northwind' });
    expect(invoices[0].total_amount).toBe(500);
  });
});

describe('is_settled', () => {
  // The detail page gates both destructive actions on `!invoice.is_settled`
  // alone: the Cancel button in the header and the record-payment form in the
  // totals card. That is only correct while SETTLED covers BOTH terminal
  // statuses. The Cancel guard used to carry a second `status !== 'Cancelled'`
  // test, which was dead weight given SETTLED and has been removed, so this is
  // now the only thing standing between a narrowed SETTLED and a Cancel button
  // offered on an already-cancelled invoice. Both endpoints answer 400 there,
  // so the damage would be a dead button rather than a bad write, but a button
  // that cannot work should not be drawn.
  beforeEach(() => {
    apiRequest.mockReset();
  });

  /** @param {string} status */
  async function settledFor(status) {
    apiRequest.mockResolvedValue({ invoice: { id: 'inv-1', status } });
    const { invoice } = await getInvoice(event, 'inv-1');
    return invoice.is_settled;
  }

  it('is true for a paid invoice', async () => {
    expect(await settledFor('Paid')).toBe(true);
  });

  it('is true for a cancelled invoice', async () => {
    // InvoiceCancelView refuses to cancel one twice, so the button must go.
    expect(await settledFor('Cancelled')).toBe(true);
  });

  it('is false for every status that is still in play', async () => {
    for (const status of ['Draft', 'Sent', 'Viewed', 'Partially_Paid', 'Overdue', 'Pending']) {
      expect(await settledFor(status), status).toBe(false);
    }
  });
});

describe('a detail line amount', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("is the server's net_amount, after the line's own discount, not its total", async () => {
    // 2 x 100 less 10% is 180. `total` also carries the line's own 10% tax,
    // which the invoice does not charge, so it is not the figure to show.
    apiRequest.mockResolvedValue({
      invoice: {
        id: 'inv-1',
        status: 'Draft',
        line_items: [
          {
            id: 'l1',
            name: 'Design',
            quantity: '2.00',
            unit_price: '100.00',
            tax_rate: '10.00',
            net_amount: '180.00',
            total: '198.00'
          }
        ]
      }
    });

    const { lineItems } = await getInvoice(event, 'inv-1');

    expect(lineItems[0].amount).toBe(180);
  });
});
