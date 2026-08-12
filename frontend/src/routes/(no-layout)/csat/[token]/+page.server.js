/**
 * Public CSAT survey page.
 *
 * Anonymous: the token in the URL is the only auth. Server-side fetch
 * fronts the public Django endpoint so the survey context renders on first
 * paint without exposing the API base URL to the browser.
 */

import { fail } from '@sveltejs/kit';
import { env } from '$env/dynamic/public';

import { preselectedRating } from '$lib/server/v2/csat.js';

const API_BASE_URL = `${env.PUBLIC_DJANGO_API_URL}/api`;

/** @type {import('./$types').PageServerLoad} */
export async function load({ params, fetch, url }) {
  const res = await fetch(`${API_BASE_URL}/public/csat/${params.token}/`);
  if (res.status === 410) {
    return { gone: true, token: params.token };
  }
  if (res.status === 400) {
    return { invalid: true, token: params.token };
  }
  if (!res.ok) {
    return { error: `Server returned ${res.status}`, token: params.token };
  }
  const data = await res.json();
  return {
    token: params.token,
    // The star clicked in the email, if any. Only meaningful on this branch:
    // the gone/invalid/error states report themselves instead of offering the
    // scale, so a rating would have nothing to pre-select.
    clickedRating: preselectedRating(url),
    survey: {
      ticketSubject: data.case_subject,
      closedAt: data.case_closed_on,
      orgName: data.org_name,
      agentName: data.agent_name,
      rating: data.rating,
      comment: data.comment || '',
      respondedAt: data.responded_at,
      editableUntil: data.edit_window_closes_at
    }
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  submit: async ({ request, params, fetch }) => {
    const form = await request.formData();
    const rating = Number(form.get('rating'));
    const comment = (form.get('comment')?.toString() || '').trim();
    if (!Number.isFinite(rating) || rating < 1 || rating > 5) {
      return fail(400, { error: 'Please pick a rating between 1 and 5.' });
    }
    const res = await fetch(`${API_BASE_URL}/public/csat/${params.token}/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, comment })
    });
    if (res.status === 410) {
      return fail(410, { error: 'This survey link has expired.' });
    }
    if (res.status === 409) {
      return fail(409, { error: 'This survey is locked. The edit window has closed.' });
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return fail(res.status, {
        error: body?.error || `Server returned ${res.status}`
      });
    }
    return { success: true, rating, comment };
  }
};
