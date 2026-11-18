/**
 * Login Page - Secure OAuth Implementation
 *
 * Security features:
 * - PKCE (Proof Key for Code Exchange) for authorization code protection
 * - Cryptographic state parameter for CSRF protection
 * - Server-side token exchange via Django backend (no client secret in frontend)
 * - Secure httpOnly cookies for sensitive data
 *
 * Django endpoint: POST /api/auth/google/callback/
 */

import axios from 'axios';
import { fail, redirect, type Actions, type Cookies } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { generateCodeVerifier, generateCodeChallenge, generateState } from '#lib/utils/pkce.js';
import { describeError } from '#lib/server/log-safe.js';
import { relayHeaders } from '#lib/server/relay.js';
import { rememberSignupSource } from '#lib/server/signup-source.js';
import type { components } from '#lib/api-schema.js';
import type { PageServerLoad } from './$types';

type Schemas = components['schemas'];

const GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth';
const GOOGLE_SCOPES = ['openid', 'email', 'profile'].join(' ');

// Cookie configuration
const COOKIE_OPTIONS = {
  path: '/',
  httpOnly: true,
  sameSite: 'lax'
};

/**
 * Get secure cookie options based on environment
 * @param maxAge Cookie max age in seconds
 * @returns Cookie options with secure flag for production
 */
function getCookieOptions(maxAge: number): object {
  return {
    ...COOKIE_OPTIONS,
    secure: env.NODE_ENV === 'production',
    maxAge
  };
}

export const load: PageServerLoad = async ({ url, cookies, getClientAddress, request }) => {
  const code = url.searchParams.get('code');
  const returnedState = url.searchParams.get('state');
  const error = url.searchParams.get('error');
  const errorDescription = url.searchParams.get('error_description');
  const resetSuccess = url.searchParams.get('reset') === 'success';

  // Handle OAuth error returned from Google
  if (error) {
    console.error('Google OAuth error:', error, errorDescription);
    return {
      google_url: null,
      error: errorDescription || `OAuth error: ${error}`
    };
  }

  // Handle OAuth callback with authorization code
  if (code) {
    return handleOAuthCallback(code, returnedState, cookies, { getClientAddress, request });
  }

  // After the error and callback returns: Google's redirect back is not where
  // the person came from.
  rememberSignupSource({ url, request, cookies }, env.NODE_ENV === 'production');

  // Check if user is already authenticated
  const jwtAccess = cookies.get('jwt_access');
  if (jwtAccess) {
    throw redirect(307, '/org');
  }

  // Generate OAuth parameters and return login URL
  return { ...(await generateOAuthUrl(cookies)), resetSuccess };
};

/**
 * Handle the OAuth callback when Google redirects back with an authorization code
 * @param code Authorization code from Google
 * @param returnedState State parameter returned from Google
 * @param cookies SvelteKit cookies
 * @param visitor Who the API's audit row records
 * @returns Always redirects, to `/org` or back to `/login` with an error
 */
async function handleOAuthCallback(
  code: string,
  returnedState: string | null,
  cookies: Cookies,
  visitor: { getClientAddress: () => string; request: Request }
): Promise<never> {
  // Retrieve and immediately clear OAuth cookies (one-time use)
  const savedState = cookies.get('oauth_state');
  const codeVerifier = cookies.get('oauth_code_verifier');

  // Clear OAuth cookies regardless of outcome
  cookies.delete('oauth_state', { path: '/' });
  cookies.delete('oauth_code_verifier', { path: '/' });

  // Validate state parameter (CSRF protection)
  if (!savedState || savedState !== returnedState) {
    console.error('OAuth state mismatch - possible CSRF attack');
    console.error('Expected:', savedState?.substring(0, 10) + '...');
    console.error('Received:', returnedState?.substring(0, 10) + '...');
    throw redirect(307, '/login?error=state_mismatch');
  }

  // Validate code verifier exists
  if (!codeVerifier) {
    console.error('Missing PKCE code verifier - session may have expired');
    throw redirect(307, '/login?error=session_expired');
  }

  const redirect_uri = env.GOOGLE_LOGIN_DOMAIN + '/login';

  try {
    // Exchange code for tokens via Django backend
    // The backend handles the actual token exchange with Google using the client secret
    const apiUrl = publicEnv.PUBLIC_DJANGO_API_URL;
    console.log('Using API URL:', apiUrl);
    const response = await axios.post<Schemas['GoogleOAuthResponse']>(
      `${apiUrl}/api/auth/google/callback/`,
      {
        code,
        code_verifier: codeVerifier,
        redirect_uri
      } satisfies Schemas['GoogleOAuthRequestRequest'],
      {
        // The sign-in audit row, success or failure, records who signed in,
        // and failures are capped per address; see `#lib/server/relay.js`.
        headers: { 'Content-Type': 'application/json', ...relayHeaders(visitor) },
        timeout: 30000
      }
    );

    const { access_token, refresh_token } = response.data;

    // Store JWT tokens in secure httpOnly cookies
    cookies.set('jwt_access', access_token, getCookieOptions(60 * 60 * 24)); // 1 day
    cookies.set('jwt_refresh', refresh_token, getCookieOptions(60 * 60 * 24 * 365)); // 1 year
  } catch (error) {
    // Never log the raw error or the response body: the axios `config.data` for
    // this call is the authorization code plus the PKCE code_verifier, and a
    // token endpoint's response body can carry tokens.
    console.error('OAuth token exchange failed:', describeError(error));
    const failure = error as { response?: { data?: { error?: unknown } }; message?: unknown };
    const errorMessage = failure.response?.data?.error || failure.message || 'Unknown error';

    // Provide user-friendly error messages
    let userError = 'auth_failed';
    const errStr = String(errorMessage);
    if (errStr.includes('code_verifier')) {
      userError = 'pkce_failed';
    } else if (errStr.includes('expired')) {
      userError = 'code_expired';
    }
    // Log the classification, not errorMessage: that string comes from the
    // token endpoint's response body, which does not belong in logs.
    console.error('OAuth failure classified as:', userError);

    throw redirect(307, `/login?error=${userError}`);
  }

  // Success - redirect to organization selection
  throw redirect(307, '/org');
}

/**
 * Generate Google OAuth URL with PKCE parameters
 * @param cookies SvelteKit cookies
 * @returns Object containing the Google OAuth URL
 */
async function generateOAuthUrl(cookies: Cookies): Promise<{ google_url: string }> {
  // Generate PKCE parameters
  const codeVerifier = generateCodeVerifier();
  const codeChallenge = await generateCodeChallenge(codeVerifier);

  // Generate cryptographically random state for CSRF protection
  const state = generateState();

  // Store PKCE verifier and state in secure httpOnly cookies
  // These expire in 10 minutes - should be plenty for OAuth flow
  const oauthCookieMaxAge = 60 * 10; // 10 minutes

  cookies.set('oauth_code_verifier', codeVerifier, getCookieOptions(oauthCookieMaxAge));
  cookies.set('oauth_state', state, getCookieOptions(oauthCookieMaxAge));

  // Build Google OAuth URL with all required parameters
  const redirect_uri = env.GOOGLE_LOGIN_DOMAIN + '/login';

  const params = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID ?? '',
    redirect_uri,
    response_type: 'code',
    scope: GOOGLE_SCOPES,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
    access_type: 'offline', // Request refresh token
    prompt: 'consent' // Always show consent screen (required for refresh token)
  });

  const google_login_url = `${GOOGLE_AUTH_URL}?${params.toString()}`;

  return { google_url: google_login_url };
}

export const actions: Actions = {
  default: async ({ request, cookies, getClientAddress }) => {
    const formData = await request.formData();
    const email = String(formData.get('email') ?? '').trim().toLowerCase();
    const password = String(formData.get('password') ?? '');

    if (!email || !password) {
      return fail(400, { error: 'Enter your work email and password.' });
    }

    let challengeEmail = email;
    let challengeId = '';
    try {
      const response = await axios.post<{ email: string; challenge_id: string }>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/password/request-code/`,
        { email, password },
        {
          headers: {
            'Content-Type': 'application/json',
            ...relayHeaders({ getClientAddress, request })
          },
          timeout: 15000
        }
      );
      challengeEmail = response.data.email || email;
      challengeId = response.data.challenge_id;
    } catch (error) {
      const failure = error as {
        response?: { status?: number; data?: { error?: unknown } };
      };
      const message = failure.response?.data?.error;
      return fail(failure.response?.status === 429 ? 429 : 400, {
        error: typeof message === 'string'
          ? message
          : 'We could not sign you in. Check your details and try again.'
      });
    }

    const secure = env.NODE_ENV === 'production';
    const cookieOptions = {
      path: '/login',
      httpOnly: true,
      sameSite: 'lax' as const,
      secure,
      maxAge: 60 * 10
    };
    cookies.set('login_otp_email', challengeEmail, cookieOptions);
    cookies.set('login_otp_challenge', challengeId, cookieOptions);
    throw redirect(303, '/login/verify-code');
  }
};
