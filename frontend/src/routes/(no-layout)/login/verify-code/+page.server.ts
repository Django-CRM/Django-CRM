import axios from 'axios';
import { fail, redirect } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { Actions, PageServerLoad } from './$types';

type PasswordVerifyResponse = { access_token: string; refresh_token: string };
type PasswordResendResponse = { challenge_id: string };
type ApiErrorResponse = { error?: string };

const COOKIE_PATH = '/login';
const COOKIE_MAX_AGE = 60 * 10;

function cookieOptions(secure: boolean) {
  return {
    path: COOKIE_PATH,
    httpOnly: true,
    sameSite: 'lax' as const,
    secure,
    maxAge: COOKIE_MAX_AGE
  };
}

function maskEmail(email: string) {
  const [name, domain] = email.split('@');
  if (!name || !domain) return email;
  const visible = name.length > 2 ? name.slice(0, 2) : name.slice(0, 1);
  return `${visible}${'•'.repeat(Math.max(1, Math.min(6, name.length - visible.length)))}@${domain}`;
}

export const load: PageServerLoad = async ({ cookies }) => {
  const email = cookies.get('login_otp_email');
  const challengeId = cookies.get('login_otp_challenge');
  if (!email || !challengeId) {
    throw redirect(303, '/login');
  }
  return { maskedEmail: maskEmail(email) };
};

export const actions: Actions = {
  default: async ({ request, cookies, getClientAddress }) => {
    const email = cookies.get('login_otp_email');
    const code = String((await request.formData()).get('code') ?? '').trim();
    if (!email) throw redirect(303, '/login');
    if (!/^\d{6}$/.test(code)) {
      return fail(400, { error: 'Enter the six-digit code from your email.' });
    }

    let response;
    try {
      response = await axios.post<PasswordVerifyResponse>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/password/verify-code/`,
        { email, code },
        {
          headers: {
            'Content-Type': 'application/json',
            ...relayHeaders({ getClientAddress, request })
          },
          timeout: 15000
        }
      );
    } catch (error) {
      const failure = axios.isAxiosError<ApiErrorResponse>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, {
        error: typeof message === 'string' ? message : 'We could not verify that code. Please try again.'
      });
    }

    const secure = env.NODE_ENV === 'production';
    cookies.set('jwt_access', response.data.access_token, {
      path: '/', httpOnly: true, sameSite: 'lax', secure, maxAge: 60 * 60 * 24
    });
    cookies.set('jwt_refresh', response.data.refresh_token, {
      path: '/', httpOnly: true, sameSite: 'lax', secure, maxAge: 60 * 60 * 24 * 365
    });
    cookies.delete('login_otp_email', { path: COOKIE_PATH });
    cookies.delete('login_otp_challenge', { path: COOKIE_PATH });
    throw redirect(303, '/org');
  },

  resend: async ({ cookies, getClientAddress, request }) => {
    const challengeId = cookies.get('login_otp_challenge');
    if (!challengeId) throw redirect(303, '/login');

    try {
      const response = await axios.post<PasswordResendResponse>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/password/resend-code/`,
        { challenge_id: challengeId },
        {
          headers: {
            'Content-Type': 'application/json',
            ...relayHeaders({ getClientAddress, request })
          },
          timeout: 15000
        }
      );
      cookies.set('login_otp_challenge', response.data.challenge_id, cookieOptions(env.NODE_ENV === 'production'));
      return { resent: true, message: 'A new code has been sent.' };
    } catch (error) {
      const failure = axios.isAxiosError<ApiErrorResponse>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, {
        error: typeof message === 'string' ? message : 'We could not send another code. Please try again.'
      });
    }
  }
};
