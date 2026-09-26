import { fail, redirect } from '@sveltejs/kit';
import {
  getInvoice,
  sendInvoice,
  markInvoicePaid,
  cancelInvoice,
  duplicateInvoice
} from '$lib/server/v2/invoices.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * One invoice, with its line items.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, params }) {
  return await getInvoice({ cookies }, params.id);
}

/** @type {import('./$types').Actions} */
export const actions = {
  /** Send it, or re-send an overdue one. 400 if it is Paid or Cancelled. */
  send: async ({ cookies, params }) => {
    try {
      await sendInvoice({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 404 ? 404 : 400, {
        error: readableError(err, 'Could not send this invoice.')
      });
    }
    return { sent: true };
  },

  /**
   * Record a payment. An empty amount settles the whole balance; a figure takes
   * a partial payment, which the API validates against what is still owed.
   */
  markPaid: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const amount = form.get('amount')?.toString().trim() ?? '';
    const reference = form.get('reference_number')?.toString().trim() ?? '';
    try {
      await markInvoicePaid(
        { cookies },
        params.id,
        amount ? { amount, reference_number: reference } : { reference_number: reference }
      );
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 404 ? 404 : 400, {
        error: readableError(err, 'Could not record that payment.')
      });
    }
    return { paid: true };
  },

  /** Cancel it. 400 if already Cancelled, or if Paid. Settled money stays. */
  cancel: async ({ cookies, params }) => {
    try {
      await cancelInvoice({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 404 ? 404 : 400, {
        error: readableError(err, 'Could not cancel this invoice.')
      });
    }
    return { cancelled: true };
  },

  /**
   * Copy to a fresh Draft and open it. The new invoice's id comes back from the
   * API, so the redirect lands on the copy rather than reloading the original.
   */
  duplicate: async ({ cookies, params }) => {
    let created;
    try {
      created = await duplicateInvoice({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 404 ? 404 : 400, {
        error: readableError(err, 'Could not duplicate this invoice.')
      });
    }
    const newId = created?.invoice?.id;
    if (newId) redirect(303, `/invoices/${newId}`);
    return { duplicated: true };
  }
};
