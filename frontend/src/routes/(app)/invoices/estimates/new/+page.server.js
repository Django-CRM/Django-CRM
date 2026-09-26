import { fail, redirect } from '@sveltejs/kit';
import { listAccounts } from '$lib/server/v2/accounts.js';
import { listContacts } from '$lib/server/v2/contacts.js';
import { listDeals } from '$lib/server/v2/deals.js';
import { listProducts } from '$lib/server/v2/products.js';
import { createEstimate, estimateFromDeal } from '$lib/server/v2/estimates.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * The estimate builder's pickers, the same list layers the invoice builder
 * reads, plus the deals for the optional deal link. Every list is what the API
 * lets this caller see, and the API checks each id again on save.
 *
 * `?opportunity=<id>` (the "Create estimate" action on a deal) prefills the
 * form from that deal; a deal the caller cannot open prefills nothing.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  const dealId = url.searchParams.get('opportunity');
  const [accounts, contacts, products, deals, prefill] = await Promise.all([
    listAccounts({ cookies }, new URLSearchParams({ limit: '1000' })),
    listContacts({ cookies }, new URLSearchParams({ limit: '1000' })),
    listProducts({ cookies }),
    // A failed deal list costs the optional deal picker, not the form.
    listDeals({ cookies }, new URLSearchParams({ limit: '1000' })).catch(() => ({ results: [] })),
    dealId ? estimateFromDeal({ cookies }, dealId) : null
  ]);

  return {
    accounts: accounts.results.map((a) => ({ id: a.id, name: a.name || 'Unnamed account' })),
    contacts: contacts.results.map((c) => ({
      id: c.id,
      name: c.name || c.email || 'Unnamed contact',
      account_id: c.account?.id ?? null,
      account_name: c.account?.name ?? ''
    })),
    products: products.results
      .filter((p) => p.is_active)
      .map((p) => ({ id: p.id, name: p.name, sku: p.sku, price: p.price })),
    deals: deals.results.map((d) => ({ id: d.id, name: d.name, account_id: d.account.id })),
    prefill
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Create the draft from the builder's one JSON `payload` field. The server
   * owns org, creator, number, token, totals and status, and decides whether
   * this caller may bill the account and cite the deal.
   */
  create: async ({ cookies, request }) => {
    const form = await request.formData();
    let body;
    try {
      body = JSON.parse(form.get('payload')?.toString() || '{}');
    } catch {
      return fail(400, { error: 'The estimate form could not be read. Please try again.' });
    }

    if (!body.account_id) return fail(400, { error: 'Choose an account.' });
    if (!body.contact_id) return fail(400, { error: 'Choose a contact.' });
    if (!body.title) return fail(400, { error: 'Give the estimate a title.' });
    if (!Array.isArray(body.line_items) || body.line_items.length === 0) {
      return fail(400, { error: 'Add at least one line with a description and an amount.' });
    }

    try {
      await createEstimate({ cookies }, body);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error: readableError(err, 'Could not create the estimate.')
      });
    }

    // There is no estimate detail page on the web; the worklist is where a
    // new draft is sent from.
    redirect(303, '/invoices/estimates');
  }
};
