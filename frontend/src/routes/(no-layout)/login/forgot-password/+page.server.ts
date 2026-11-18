import axios from 'axios';
import { fail, redirect, type Cookies, type RequestEvent } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { Actions } from './$types';

type RecoveryStartEvent = Pick<RequestEvent, 'request' | 'cookies' | 'getClientAddress'>;
type RecoveryStartResponse = { challenge_id?: string; message?: string; error?: string };

export const actions: Actions = {
  loginOtp: async ({ request, cookies, getClientAddress }) =>
    startRecovery('login', { request, cookies, getClientAddress }),
  reset: async ({ request, cookies, getClientAddress }) =>
    startRecovery('reset', { request, cookies, getClientAddress })
};

async function startRecovery(flow: 'login' | 'reset', event: RecoveryStartEvent) {
  const { request, cookies, getClientAddress } = event;
  const email = String((await request.formData()).get('email') ?? '').trim().toLowerCase();
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return fail(400, { error: 'Enter a valid email address.' });
  }

  try {
    const response = await axios.post<RecoveryStartResponse>(
      `${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/request-code/`,
      { email, flow },
      {
        headers: {
          'Content-Type': 'application/json',
          ...relayHeaders({ getClientAddress, request })
        },
        timeout: 15000
      }
    );
    if (response.data.challenge_id) {
      const options = {
        path: '/login',
        httpOnly: true,
        sameSite: 'lax' as const,
        secure: env.NODE_ENV === 'production',
        maxAge: 600
      };
      cookies.set('recovery_email', email, options);
      cookies.set('recovery_challenge', response.data.challenge_id, options);
      throw redirect(303, flow === 'login' ? '/login/recovery-code' : '/login/reset-password');
    }
    return { message: response.data.message ?? 'If an active account matches that email, a code will be sent.' };
  } catch (error: unknown) {
    if (error && typeof error === 'object' && 'status' in error) throw error;
    const failure = axios.isAxiosError<RecoveryStartResponse>(error) ? error : null;
    const message = failure?.response?.data?.error;
    return fail(failure?.response?.status === 429 ? 429 : 400, {
      error: typeof message === 'string' ? message : 'We could not start recovery. Please try again.'
    });
  }
}
