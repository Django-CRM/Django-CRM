import { fail, redirect } from '@sveltejs/kit';
import { createInvoiceTemplate } from '$lib/server/v2/templates.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { isOrgAdmin } from '$lib/admin.js';

/**
 * Creating a template.
 *
 * Admin-only: `POST /api/invoices/templates/` refuses a non-admin via
 * `_forbid_non_admin_template`, so this mirrors the products/new and
 * goals/new pattern: `load` computes `can_manage` with `isOrgAdmin(locals.profile)`
 * (same source the list page's `load` already uses) so a non-admin who
 * navigates here directly sees an "admins only" state instead of a form the
 * POST would refuse. The list page's own button is gated the same way.
 *
 * @type {import('./$types').PageServerLoad}
 */
export function load({ locals }) {
  return { can_manage: isOrgAdmin(/** @type {any} */ (locals).profile) };
}

/**
 * Read only the fields this form owns. Anything else in the submission is
 * ignored here and would be dropped by the allow-list in the data layer too.
 *
 * @param {FormData} form
 */
function readValues(form) {
  return {
    name: form.get('name')?.toString() ?? '',
    primary_color: form.get('primary_color')?.toString() ?? '',
    secondary_color: form.get('secondary_color')?.toString() ?? '',
    default_notes: form.get('default_notes')?.toString() ?? '',
    default_terms: form.get('default_terms')?.toString() ?? '',
    footer_text: form.get('footer_text')?.toString() ?? '',
    is_default: form.get('is_default') === 'on'
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  create: async ({ cookies, request }) => {
    const form = await request.formData();
    const logo = form.get('logo');
    const logoFile = logo instanceof File && logo.size > 0 ? logo : null;
    const values = readValues(form);

    try {
      await createInvoiceTemplate({ cookies }, values, logoFile);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        values,
        error:
          err?.status === 403
            ? 'Only an admin can create an invoice template.'
            : readableError(err, 'Could not create the template.')
      });
    }

    redirect(303, '/invoices/templates');
  }
};
