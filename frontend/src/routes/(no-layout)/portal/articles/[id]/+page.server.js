/**
 * One help article.
 *
 * A 404 from the API means "no such article, or not one you may read", and the
 * two are deliberately indistinguishable there. This page keeps them
 * indistinguishable too: both become the same 404, so guessing ids tells a
 * visitor nothing about which drafts exist.
 */

import { error, redirect } from '@sveltejs/kit';
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  PortalError,
  clearSession,
  getArticle,
  loginPath
} from '$lib/server/portal';

/** Send an expired or missing session back to the right org's sign-in page. */
function toLogin(cookies) {
  const org = cookies.get(ORG_COOKIE);
  clearSession(cookies);
  throw redirect(303, loginPath(org));
}

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, params }) {
  const token = cookies.get(ACCESS_COOKIE);
  if (!token) toLogin(cookies);

  try {
    const data = await getArticle(token, params.id);
    // `related` carries ids and titles only. The API computes it from the
    // agents' tags and deliberately does not send the tag names, which read
    // like "At Risk" and "VIP" and are not written for customers.
    return { article: data.article, related: data.related ?? [] };
  } catch (err) {
    if (err instanceof PortalError) {
      if (err.status === 401 || err.status === 403) toLogin(cookies);
      if (err.status === 404) throw error(404, 'Article not found');
    }
    throw err;
  }
}
