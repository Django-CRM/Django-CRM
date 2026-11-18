import axios from 'axios';
import { fail, redirect } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { Actions, PageServerLoad } from './$types';

type RecoveryResponse = { challenge_id?: string; message?: string; error?: string };

export const load: PageServerLoad = ({ cookies }) => {
  if (!cookies.get('recovery_challenge')) throw redirect(303, '/login/forgot-password');
  return { email: cookies.get('recovery_email') };
};

export const actions: Actions = {
  validate: async ({ request, cookies, getClientAddress }) => {
    const challengeId = cookies.get('recovery_challenge');
    const code = String((await request.formData()).get('code') ?? '');
    if (!challengeId) return fail(400, { valid: false, error: 'Your recovery session expired.' });
    try {
      const response = await axios.post<{ message: string }>(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/validate-code/`,
        { challenge_id: challengeId, code },
        {
          headers: { 'Content-Type': 'application/json', ...relayHeaders({ getClientAddress, request }) },
          timeout: 10000
        }
      );
      return { valid: true, message: response.data.message };
    } catch (error: unknown) {
      const failure = axios.isAxiosError<RecoveryResponse>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, {
        valid: false,
        error: typeof message === 'string' ? message : 'Code validation failed.'
      });
    }
  },
  default: async ({ request, cookies, getClientAddress }) => {
    const form = await request.formData();
    const challengeId = cookies.get('recovery_challenge');
    const code = String(form.get('code') ?? '');
    const password = String(form.get('password') ?? '');
    const confirmPassword = String(form.get('confirm_password') ?? '');
    if (!challengeId) throw redirect(303, '/login/forgot-password');
    try {
      await axios.post(
        `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/reset-password/`,
        { challenge_id: challengeId, code, password, confirm_password: confirmPassword },
        {
          headers: { 'Content-Type': 'application/json', ...relayHeaders({ getClientAddress, request }) },
          timeout: 15000
        }
      );
      cookies.delete('recovery_email', { path: '/login' });
      cookies.delete('recovery_challenge', { path: '/login' });
      throw redirect(303, '/login?reset=success');
    } catch (error: unknown) {
      if (error && typeof error === 'object' && 'status' in error) throw error;
      const failure = axios.isAxiosError<RecoveryResponse>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, { error: typeof message === 'string' ? message : 'Could not reset your password.' });
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
      if (response.data.challenge_id) {
        cookies.set('recovery_challenge', response.data.challenge_id, {
          path: '/login', httpOnly: true, sameSite: 'lax', secure: env.NODE_ENV === 'production', maxAge: 600
        });
      }
      return { message: 'A new code has been sent.' };
    } catch (error: unknown) {
      const failure = axios.isAxiosError<RecoveryResponse>(error) ? error : null;
      const message = failure?.response?.data?.error;
      return fail(400, { error: typeof message === 'string' ? message : 'Could not resend the code.' });
    }
  }
};
