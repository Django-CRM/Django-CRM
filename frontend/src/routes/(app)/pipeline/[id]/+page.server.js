import { fail, redirect } from '@sveltejs/kit';
import { getDeal } from '$lib/server/v2/deals.js';
import { invoiceFromDeal } from '$lib/server/v2/invoices.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/** @type {import('./$types').PageServerLoad} */
export async function load(event) {
  return await getDeal(event, event.params.id);
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Raise a Draft invoice from this deal and open it. The deal id is the
   * route's, never the body's, and the API decides the rest: a deal this
   * caller may not open is a 404, one not in a won stage or with no line items
   * is a 400 whose sentence is shown as written.
   */
  invoice: async ({ cookies, params }) => {
    let created;
    try {
      created = await invoiceFromDeal({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 404 ? 404 : 400, {
        error:
          err?.body?.message || readableError(err, 'Could not raise an invoice from this deal.')
      });
    }
    redirect(303, created?.id ? `/invoices/${created.id}` : '/invoices');
  }
};
