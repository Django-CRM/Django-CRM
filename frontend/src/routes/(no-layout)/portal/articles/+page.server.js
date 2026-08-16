/**
 * The customer's view of the knowledge base.
 *
 * Which articles exist is decided entirely by the API from the token's org:
 * published, approved, and in the caller's own org. There is no id or org in
 * anything this page sends, so there is no parameter here worth tampering with.
 */

import { redirect } from '@sveltejs/kit';
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  PortalError,
  clearSession,
  listArticles,
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

  const search = url.searchParams.get('search') || '';

  try {
    const data = await listArticles(token, search);
    return { articles: data.articles ?? [], count: data.articles_count ?? 0, search };
  } catch (err) {
    if (err instanceof PortalError && (err.status === 401 || err.status === 403)) {
      toLogin(cookies);
    }
    throw err;
  }
}
