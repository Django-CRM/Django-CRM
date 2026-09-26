import { fail } from '@sveltejs/kit';
import { listInvoiceTemplates, setDefaultTemplate } from '$lib/server/v2/templates.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { isOrgAdmin } from '$lib/admin.js';

/**
 * The template catalogue. `load` returns `{ templates, totals }`. The names the
 * page reads, plus `can_manage`, a UX hint derived from the JWT role. The hint
 * only decides whether to draw the write buttons; the API admin-gates every
 * template write regardless, so a member who forges the POST still gets a 403.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, locals }) {
  const data = await listInvoiceTemplates({ cookies });
  return {
    ...data,
    can_manage: isOrgAdmin(/** @type {any} */ (locals).profile)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Make a template the org default. The id comes from the row; the API decides
   * whether this caller may change templates (admins only) and refuses the rest.
   */
  setDefault: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('id')?.toString();
    if (!id) return fail(400, { error: 'Which template? None was given.' });

    try {
      await setDefaultTemplate({ cookies }, id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'Only an administrator can change invoice templates.'
            : readableError(err, 'Could not change the default template.')
      });
    }
    return { defaulted: true };
  }
};
