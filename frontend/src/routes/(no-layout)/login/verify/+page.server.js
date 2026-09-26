/**
 * Magic Link Verification Page
 *
 * Handles the magic link callback:
 * 1. Extract token from URL query params
 * 2. POST to backend to verify token
 * 3. Set JWT cookies on success
 * 4. Redirect to /org
 *
 * Security: Token is consumed server-side before any HTML is rendered.
 */

import axios from 'axios';
import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';
import { relayHeaders } from '$lib/server/relay.js';

/** @type {import('@sveltejs/kit').ServerLoad} */
export async function load({ url, cookies, getClientAddress }) {
  const token = url.searchParams.get('token');

  if (!token) {
    return { error: 'Missing verification token.' };
  }

  try {
    const apiUrl = publicEnv.PUBLIC_DJANGO_API_URL;
    const response = await axios.post(
      `${apiUrl}/api/auth/magic-link/verify/`,
      { token },
      {
        // The sign-in audit row records who signed in; see `$lib/server/relay.js`.
        headers: { 'Content-Type': 'application/json', ...relayHeaders({ getClientAddress }) },
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
  } catch (error) {
    const errorMessage = error.response?.data?.error || 'Verification failed';
    return { error: errorMessage };
  }

  // Success - redirect to org selection (same as Google OAuth)
  throw redirect(307, '/org');
}
