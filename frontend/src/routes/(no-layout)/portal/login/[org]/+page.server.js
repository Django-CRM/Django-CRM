/**
 * Customer portal sign-in.
 *
 * Per-org by design: the org is in the URL because the customer arrived from an
 * email that org sent them, and a sign-in attempt only ever resolves contacts in
 * that one org. There is deliberately no org picker. A person who is a contact
 * at three companies has three separate sign-ins, and showing them a list would
 * disclose one tenant's customer relationships to another.
 */

import { fail, redirect } from '@sveltejs/kit';
import {
  ACCESS_COOKIE,
  ORG_COOKIE,
  PortalError,
  clearSession,
  requestLogin,
  setSession,
  verifyLogin
} from '$lib/server/portal';

/** @type {import('./$types').PageServerLoad} */
export async function load({ params, cookies }) {
  // A session is only good for the org it was issued for. Somebody who is a
  // contact at two companies holds one portal session at a time, and following
  // company B's email while signed in to company A must sign them in to B
  // rather than silently show them A's requests. The old session is dropped
  // here rather than kept, because there is one cookie and it is about to be
  // overwritten anyway.
  if (cookies.get(ACCESS_COOKIE)) {
    if (cookies.get(ORG_COOKIE) === params.org) throw redirect(303, '/portal/cases');
    clearSession(cookies);
  }
}

/** @type {import('./$types').Actions} */
export const actions = {
  request: async ({ request, params }) => {
    const form = await request.formData();
    const email = String(form.get('email') || '').trim();
    if (!email) return fail(400, { error: 'Enter your email address.' });

    // The API answers identically whether or not this address is a contact
    // here, and so must this page. Anything conditional on the result would
    // reintroduce the enumeration the backend is careful to avoid.
    try {
      await requestLogin(params.org, email);
    } catch {
      // Even a transport failure must not distinguish itself.
    }
    return { stage: 'code', email };
  },

  verify: async ({ request, params, cookies }) => {
    const form = await request.formData();
    const email = String(form.get('email') || '').trim();
    const code = String(form.get('code') || '').trim();
    if (!code) return fail(400, { error: 'Enter the code from your email.', stage: 'code', email });

    try {
      const result = await verifyLogin(params.org, email, code);
      setSession(cookies, result.access_token, params.org);
    } catch (err) {
      if (err instanceof PortalError) {
        return fail(400, {
          error: err.data?.error || 'That code is not valid. Request a new one.',
          stage: 'code',
          email
        });
      }
      throw err;
    }
    throw redirect(303, '/portal/cases');
  }
};
