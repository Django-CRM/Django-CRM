import { fail } from '@sveltejs/kit';
import { listInvoices, sendInvoice } from '$lib/server/v2/invoices.js';
import { invoiceListQuery } from '$lib/server/v2/list-queries.js';
import { listAccountsPicker } from '$lib/server/v2/accounts.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * The invoice list.
 *
 * Server load, so the JWT cookie stays server-side. Rows arrive from one API
 * call, filtered by whatever `FILTER_FIELDS` params are on the URL. The
 * header pills do NOT narrow with them, see the note on `totals` in
 * `listInvoices` (`$lib/server/v2/invoices.js`): the API computes them over
 * the role-scoped queryset before filtering, by design, so this page has no
 * "these numbers describe the filtered list" line the way accounts, estimates,
 * tasks, tickets and pipeline do. Adding one here would describe something
 * that is not true for this endpoint.
 *
 * The owner and account pickers are separate fetches, same reasoning as
 * leads.js and pipeline's deals.js: folding them into `listInvoices` would
 * cost every other caller of that function (the shell's nav badge, for one) a
 * request it never renders.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url, locals }) {
  const params = invoiceListQuery(url);

  const [{ invoices, totals }, orgPeople, accountList] = await Promise.all([
    listInvoices({ cookies }, params),
    getOrgPeopleAndTeams(cookies),
    // A failed account fetch should cost the Account dropdown in the filter
    // bar, not the whole list. Same pattern as the Tag fetch in leads.js.
    listAccountsPicker({ cookies }).catch(() => ({ accounts: [] }))
  ]);

  return {
    invoices,
    totals,
    people: orgPeople.people,
    accounts: accountList.accounts ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Send a draft, or re-send an overdue invoice as a reminder, the same POST
   * either way. The mock's inline "Send" / "Send a reminder" buttons did
   * nothing; this is that button doing the thing. The API refuses to send a
   * Paid or Cancelled invoice with a 400, and those rows never show the button.
   */
  send: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('id')?.toString();
    if (!id) return fail(400, { error: 'Which invoice?' });

    try {
      await sendInvoice({ cookies }, id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'That invoice is not yours to send.'
            : readableError(err, 'Could not send that invoice.')
      });
    }
    return { sent: id };
  }
};
