/**
 * Magic Link Verification Page
 *
 * Opening the emailed link only renders a "Continue" button. The token is
 * spent by the form POST that button submits, never by the GET (or HEAD) that
 * opens the page: mail scanners such as Microsoft Defender Safe Links fetch
 * every link in an incoming message, and when the GET spent the token the
 * scanner signed in and took the session, leaving the person's own click with
 * "Link expired or invalid". Scanners fetch links; they do not press buttons.
 * The button is a plain form submit, not a script that submits on load,
 * because some scanners run the page's JavaScript.
 *
 * On success the action sets the JWT cookies and redirects to /org.
 */

import axios from 'axios';
import { fail, redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';
import { relayHeaders } from '$lib/server/relay.js';

const MISSING = 'Missing verification token.';

/** @type {import('./$types').PageServerLoad} */
export async function load({ url }) {
  if (!url.searchParams.get('token')) {
    return { error: MISSING };
  }
  return {};
}

/** @type {import('./$types').Actions} */
export const actions = {
  // The form has no `action` attribute, so it posts back to the link itself,
  // query string included, and the token is read from the same place the GET saw it.
  default: async ({ url, cookies, getClientAddress, request }) => {
    const token = url.searchParams.get('token');
    if (!token) {
      return fail(400, { error: MISSING });
    }

    try {
      const response = await axios.post(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/magic-link/verify/`,
        { token },
        {
          // The sign-in audit row records who signed in; see `$lib/server/relay.js`.
          headers: {
            'Content-Type': 'application/json',
            ...relayHeaders({ getClientAddress, request })
          },
          timeout: 10000
        }
      );

      const { access_token, refresh_token } = response.data;

      // Store JWT tokens in secure httpOnly cookies (same as Google OAuth flow)
      const secure = env.NODE_ENV === 'production';
      cookies.set('jwt_access', access_token, {
        path: '/',
        httpOnly: true,
        sameSite: 'lax',
        secure,
        maxAge: 60 * 60 * 24 // 1 day
      });
      cookies.set('jwt_refresh', refresh_token, {
        path: '/',
        httpOnly: true,
        sameSite: 'lax',
        secure,
        maxAge: 60 * 60 * 24 * 365 // 1 year
      });
    } catch (/** @type {any} */ error) {
      return fail(400, { error: error.response?.data?.error || 'Verification failed' });
    }

    // Success - redirect to org selection (same as Google OAuth)
    redirect(303, '/org');
  }
};
