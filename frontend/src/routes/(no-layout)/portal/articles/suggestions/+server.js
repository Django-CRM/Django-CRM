/**
 * Typeahead deflection for the new-request form.
 *
 * This exists because `portal_access` is httpOnly, so the page's own script
 * cannot call the API with it. Rather than relax the cookie, the browser asks
 * this same-origin route and the server attaches the token. A cookie a script
 * can read is a cookie an injected script can steal, and that trade is not
 * worth a suggestions panel.
 *
 * A static segment, so it is ranked ahead of the sibling `[id]` article page
 * and never resolves as an article whose id happens to be "suggestions".
 */

import { json } from '@sveltejs/kit';
import { ACCESS_COOKIE, PortalError, suggestArticles } from '$lib/server/portal';

// Below this a query matches most of the knowledge base, which is noise rather
// than a suggestion.
const MIN_QUERY = 3;

const EMPTY = { articles: [] };

/** @type {import('./$types').RequestHandler} */
export async function GET({ cookies, url }) {
  const token = cookies.get(ACCESS_COOKIE);
  const q = (url.searchParams.get('q') || '').trim();

  // No session and no match both answer the same way. This route is a hint
  // beside a form: it must never redirect the page or surface an error banner
  // over what the customer is in the middle of typing.
  if (!token || q.length < MIN_QUERY) return json(EMPTY);

  try {
    return json(await suggestArticles(token, q));
  } catch (err) {
    if (err instanceof PortalError) return json(EMPTY);
    throw err;
  }
}
