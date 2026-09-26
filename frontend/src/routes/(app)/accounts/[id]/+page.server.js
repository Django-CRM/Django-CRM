import { fail, redirect } from '@sveltejs/kit';
import { deleteAccount, getAccount } from '$lib/server/v2/accounts.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { recordDuplicates } from '$lib/server/v2/duplicates.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, params }) {
  const [account, duplicates] = await Promise.all([
    getAccount({ cookies }, params.id),
    recordDuplicates({ cookies }, 'accounts', params.id)
  ]);
  return { ...account, duplicates };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Delete this account. The API applies the delete rule and answers 403 or 404
   * when it refuses, and 409 while invoices still point at the account; the page shows its sentence. On success the account is
   * gone, so the list is where to land.
   */
  delete: async ({ cookies, params }) => {
    try {
      await deleteAccount({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      const code = err?.status >= 400 && err?.status < 500 ? err.status : 400;
      return fail(code, { deleteError: readableError(err, 'Could not delete this account.') });
    }
    redirect(303, '/accounts');
  }
};
