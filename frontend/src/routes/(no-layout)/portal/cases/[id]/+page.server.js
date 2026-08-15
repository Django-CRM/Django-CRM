/**
 * One request, its public thread, and the reply box.
 *
 * The API only ever returns comments with is_internal=False, and it filters them
 * out in the query rather than in the serializer, so an agent's private note is
 * never loaded here at all. Nothing on this page needs to hide anything.
 */

import { error, fail, redirect } from '@sveltejs/kit';
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  PortalError,
  clearSession,
  getCase,
  loginPath,
  postReply
} from '$lib/server/portal';

/**
 * This page is what the "support has replied" email links to, so it is reached
 * more often than any other by somebody with no cookie at all: a different
 * phone, a cleared browser. The email carries `?org=` for exactly that case.
 * Without it the fallback is the staff login, which is a dead end for them.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {URL} [url]
 */
function toLogin(cookies, url) {
  const org = cookies.get(ORG_COOKIE) || url?.searchParams.get('org');
  clearSession(cookies);
  throw redirect(303, loginPath(org));
}

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, params, url }) {
  const token = cookies.get(ACCESS_COOKIE);
  if (!token) toLogin(cookies, url);

  try {
    const data = await getCase(token, params.id);
    return { case: data.case, comments: data.comments ?? [] };
  } catch (err) {
    if (err instanceof PortalError) {
      if (err.status === 401 || err.status === 403) toLogin(cookies, url);
      // The API answers 404 both for "no such case" and "not yours", so that
      // an id cannot be probed to learn whether it belongs to a colleague.
      if (err.status === 404) throw error(404, 'Request not found');
    }
    throw err;
  }
}

/** @type {import('./$types').Actions} */
export const actions = {
  reply: async ({ request, cookies, params, url }) => {
    const token = cookies.get(ACCESS_COOKIE);
    if (!token) toLogin(cookies, url);

    const form = await request.formData();
    const comment = String(form.get('comment') || '').trim();
    if (!comment) return fail(400, { error: 'Write a message before sending.' });

    try {
      await postReply(token, params.id, comment);
    } catch (err) {
      if (err instanceof PortalError) {
        if (err.status === 401 || err.status === 403) toLogin(cookies, url);
        return fail(err.status, {
          error: err.data?.comment?.[0] || 'Could not send that reply.'
        });
      }
      throw err;
    }
    return { sent: true };
  }
};
