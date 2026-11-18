import axios from 'axios';
import { fail, redirect } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { Actions, PageServerLoad } from './$types';

type AuthResponse = { access_token: string; refresh_token: string };
type RecoveryResponse = { challenge_id: string; error?: string };

export const load: PageServerLoad = ({ cookies }) => {
  if (!cookies.get('recovery_email') || !cookies.get('recovery_challenge')) {
    throw redirect(303, '/login/forgot-password');
  }
  return { email: cookies.get('recovery_email') };
};

export const actions: Actions = {
  default: async ({ request, cookies, getClientAddress }) => {
    const email = cookies.get('recovery_email');
    const code = String((await request.formData()).get('code') ?? '');
    if (!email) throw redirect(303, '/login/forgot-password');
    try {
      const response = await axios.post<AuthResponse>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/login/`,
        { email, code },
        {
          headers: { 'Content-Type': 'application/json', ...relayHeaders({ getClientAddress, request }) },
          timeout: 15000
        }
      );
      const secure = env.NODE_ENV === 'production';
      cookies.set('jwt_access', response.data.access_token, {
        path: '/', httpOnly: true, sameSite: 'lax', secure, maxAge: 86400
      });
      cookies.set('jwt_refresh', response.data.refresh_token, {
        path: '/', httpOnly: true, sameSite: 'lax', secure, maxAge: 31536000
      });
      cookies.delete('recovery_email', { path: '/login' });
      cookies.delete('recovery_challenge', { path: '/login' });
      throw redirect(303, '/org');
    } catch (error: unknown) {
      if (error && typeof error === 'object' && 'status' in error) throw error;
      const failure = axios.isAxiosError<{ error?: string }>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, {
        error: typeof message === 'string' ? message : 'Could not verify the code. Please try again.'
      });
    }
  },
  resend: async ({ cookies, getClientAddress, request }) => {
    const challengeId = cookies.get('recovery_challenge');
    if (!challengeId) throw redirect(303, '/login/forgot-password');
    try {
      const response = await axios.post<RecoveryResponse>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/resend-code/`,
        { challenge_id: challengeId },
        {
          headers: { 'Content-Type': 'application/json', ...relayHeaders({ getClientAddress, request }) },
          timeout: 15000
        }
      );
      cookies.set('recovery_challenge', response.data.challenge_id, {
        path: '/login', httpOnly: true, sameSite: 'lax', secure: env.NODE_ENV === 'production', maxAge: 600
      });
      return { message: 'A new code has been sent.' };
    } catch (error: unknown) {
      const failure = axios.isAxiosError<{ error?: string }>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, { error: typeof message === 'string' ? message : 'Could not resend the code.' });
    }
  }
};
