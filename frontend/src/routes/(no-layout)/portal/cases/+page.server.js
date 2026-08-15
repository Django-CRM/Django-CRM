/**
 * The customer's list of their own support requests.
 *
 * "Their own" is decided by the API from the token's contact_id, never from
 * anything this page sends, so there is no filter here that could be widened by
 * tampering with a query string.
 */

import { fail, redirect } from '@sveltejs/kit';
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  PortalError,
  clearSession,
  createCase,
  listCases,
  loginPath
} from '$lib/server/portal';

/** Send an expired or missing session back to the right org's sign-in page. */
function toLogin(cookies) {
  const org = cookies.get(ORG_COOKIE);
  clearSession(cookies);
  throw redirect(303, loginPath(org));
}

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, url }) {
  const token = cookies.get(ACCESS_COOKIE);
  if (!token) toLogin(cookies);

  const status = url.searchParams.get('status') || '';

  try {
    const data = await listCases(token, status);
    return { cases: data.cases ?? [], count: data.cases_count ?? 0, status };
  } catch (err) {
    if (err instanceof PortalError && (err.status === 401 || err.status === 403)) {
      toLogin(cookies);
    }
    throw err;
  }
}

/** @type {import('./$types').Actions} */
export const actions = {
  create: async ({ request, cookies }) => {
    const token = cookies.get(ACCESS_COOKIE);
    if (!token) toLogin(cookies);

    const form = await request.formData();
    const name = String(form.get('name') || '').trim();
    const description = String(form.get('description') || '').trim();
    const priority = String(form.get('priority') || 'Normal');

    if (!name) return fail(400, { error: 'Give your request a short summary.' });

    try {
      const data = await createCase(token, { name, description, priority });
      throw redirect(303, `/portal/cases/${data.case.id}`);
    } catch (err) {
      if (err?.status === 303) throw err;
      if (err instanceof PortalError) {
        if (err.status === 401 || err.status === 403) toLogin(cookies);
        return fail(err.status, { error: err.data?.name?.[0] || 'Could not send that request.' });
      }
      throw err;
    }
  }
};
