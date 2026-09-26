import { fail, redirect } from '@sveltejs/kit';
import {
  listEstimates,
  convertEstimate,
  sendEstimate,
  FILTER_FIELDS
} from '$lib/server/v2/estimates.js';
import { listAccountsPicker } from '$lib/server/v2/accounts.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * The estimates worklist. `load` returns `{ estimates, totals, accounts }`.
 * `estimates`/`totals` are the exact names the page reads; the data layer's
 * own field is `estimates`, so there is no rename to get wrong here.
 *
 * The account picker is a separate fetch, same reasoning as every other v2
 * list: folding it into `listEstimates` would cost every other caller of that
 * function a request it never renders.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'estimates'));

  const [{ estimates, totals }, accountList] = await Promise.all([
    listEstimates({ cookies }, params),
    // A failed account fetch should cost the Account dropdown in the filter
    // bar, not the whole worklist. Same pattern as the Tag fetch in leads.js.
    listAccountsPicker({ cookies }).catch(() => ({ accounts: [] }))
  ]);

  return { estimates, totals, accounts: accountList.accounts ?? [] };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Raise an invoice from an accepted estimate and open the new draft. The id
   * comes from the row, and the API decides whether this caller may convert it
   *: a member who is neither creator nor assignee gets 403, and a second
   * conversion gets 400.
   */
  convert: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('id')?.toString();
    if (!id) return fail(400, { error: 'Which estimate? None was given.' });

    let created;
    try {
      created = await convertEstimate({ cookies }, id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'This estimate is not yours to convert.'
            : readableError(err, 'Could not raise an invoice from this estimate.')
      });
    }

    const newId = created?.invoice?.id;
    if (newId) redirect(303, `/invoices/${newId}`);
    return { converted: true };
  },

  /**
   * Mail an estimate to its client. The API decides who may send it and
   * refuses a settled or lapsed one; its sentence is what the banner shows.
   */
  send: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('id')?.toString();
    if (!id) return fail(400, { error: 'Which estimate? None was given.' });

    try {
      await sendEstimate({ cookies }, id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'This estimate is not yours to send.'
            : readableError(err, 'Could not send this estimate.')
      });
    }
    return { sent: true };
  }
};
