/**
 * Sign out.
 *
 * Clearing the cookies is the visible half and was, until now, the only half.
 * The refresh token in `jwt_refresh` stayed valid for its full fourteen days,
 * so a copy taken from a shared machine or a proxy log kept minting access
 * tokens long after the user believed the session was over. This tells the
 * server to blacklist it first, then clears the cookies either way.
 */

import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/public';
import { relayHeaders } from '$lib/server/relay.js';

const AUTH_COOKIES = ['jwt_access', 'jwt_refresh', 'org', 'oauth_state', 'oauth_code_verifier'];

/** @type {import('./$types').PageServerLoad} */
export async function load({ locals, cookies, fetch, getClientAddress }) {
  await revokeRefreshToken(cookies.get('jwt_refresh'), fetch, { getClientAddress });

  for (const cookieName of AUTH_COOKIES) {
    if (cookies.get(cookieName)) {
      cookies.delete(cookieName, { path: '/' });
    }
  }

  if (locals.user) delete locals.user;
  if (locals.org) delete locals.org;

  throw redirect(303, '/login');
}

/**
 * Ask the API to blacklist the refresh token.
 *
 * `POST /api/auth/logout/` needs no access token: it authorises on possession
 * of the refresh token itself, which is what makes it work when the access
 * token has already expired, the common case for a tab left open overnight.
 *
 * A failure here must never block the sign-out. If the API is unreachable, the
 * user still expects to be signed out of this browser, and leaving them on a
 * logged-in page because the network blipped would be worse than the token
 * outliving the click. So this swallows errors, and the cookie clearing that
 * follows is unconditional.
 *
 * @param {string | undefined} refresh
 * The logout audit row records who signed out, so the visitor's signed
 * address goes along (`$lib/server/relay.js`).
 *
 * @param {typeof globalThis.fetch} fetch
 * @param {{ getClientAddress: () => string }} event
 */
async function revokeRefreshToken(refresh, fetch, event) {
  if (!refresh) return;
  try {
    await fetch(`${env.PUBLIC_DJANGO_API_URL}/api/auth/logout/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...relayHeaders(event) },
      body: JSON.stringify({ refresh })
    });
  } catch (error) {
    console.error('logout: could not revoke the refresh token', error);
  }
}
