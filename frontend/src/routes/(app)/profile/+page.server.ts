import { fail, redirect } from '@sveltejs/kit';
import { apiRequest } from '#lib/api-helpers.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { getProfile } from '#lib/server/v2/profile.js';
import { apiStatus } from '#lib/api-errors.js';
import type { Actions, PageServerLoad } from './$types';
import type { components } from '#lib/api-schema.js';
import type { FormBody } from '#lib/api-helpers.js';

type Schemas = components['schemas'];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ cookies }) => {
  return await getProfile({ cookies });
};

export const actions: Actions = {
  /**
   * Edit your own name and phone. Only those two are read out of the form. The
   * page shows role, org and access, but they are never posted, and the API
   * would refuse them anyway (ProfileSelfUpdateSerializer names only name and
   * phone). A field appended to the body by hand is not forwarded.
   */
  edit: async ({ cookies, request }) => {
    const form = await request.formData();
    const body: components['schemas']['PatchedProfileSelfUpdateRequest'] = {};
    for (const field of ['name', 'phone'] as const) {
      if (form.has(field)) body[field] = form.get(field)?.toString().trim() ?? '';
    }

    try {
      await apiRequest('/profile/', { method: 'PATCH', body }, { cookies });
    } catch (err) {
      return fail(apiStatus(err) === 400 ? 400 : 500, {
        values: body,
        message: readableError(err, 'Could not save your profile.')
      });
    }

    return { saved: true };
  },

  /**
   * The two lead alert switches. An unchecked box submits nothing, so an
   * absent field is sent as an explicit false and both are always sent.
   */
  alerts: async ({ cookies, request }) => {
    const form = await request.formData();
    const body: FormBody<components['schemas']['PatchedProfileSelfUpdateRequest']> = {
      lead_alerts_email: form.has('lead_alerts_email'),
      lead_alerts_push: form.has('lead_alerts_push')
    };

    try {
      await apiRequest('/profile/', { method: 'PATCH', body }, { cookies });
    } catch (err) {
      return fail(apiStatus(err) === 400 ? 400 : 500, {
        scope: 'alerts',
        message: readableError(err, 'Could not save your alert settings.')
      });
    }

    return { scope: 'alerts', saved: true };
  },

  /**
   * Switch to another organisation you belong to. This is not a field edit: the
   * backend re-issues the JWT with the new org claim (and only if you have an
   * active profile there, else 403), and we swap the httpOnly cookies the shell
   * reads so every later request is scoped to the new org. Mirrors the v1 /org
   * picker so the two behave the same.
   */
  switchOrg: async ({ cookies, request }) => {
    const form = await request.formData();
    const orgId = form.get('org_id')?.toString() ?? '';

    if (!UUID_RE.test(orgId)) {
      return fail(400, { scope: 'switch', message: 'Invalid organisation.' });
    }

    // Sent so the backend retires the token we are replacing; it belongs to the
    // caller (the API checks) or is ignored.
    const outgoingRefresh = cookies.get('jwt_refresh');
    const payload = outgoingRefresh
      ? { org_id: orgId, refresh: outgoingRefresh }
      : { org_id: orgId };

    let result: Schemas['OrgSwitchResponse'];
    try {
      result = await apiRequest(
        '/auth/switch-org/',
        { method: 'POST', body: payload },
        { cookies }
      );
    } catch (err) {
      // 403 is the honest one: you asked for an org you are not a member of.
      const message =
        apiStatus(err) === 403
          ? 'You are not a member of that organisation.'
          : readableError(err, 'Could not switch organisation.');
      return fail(apiStatus(err) === 403 ? 403 : 500, { scope: 'switch', message });
    }

    const secure = process.env.NODE_ENV === 'production';
    cookies.set('jwt_access', result.access_token, {
      path: '/',
      httpOnly: true,
      sameSite: 'lax',
      secure,
      maxAge: 60 * 60 * 24
    });
    cookies.set('jwt_refresh', result.refresh_token, {
      path: '/',
      httpOnly: true,
      sameSite: 'lax',
      secure,
      maxAge: 60 * 60 * 24 * 365
    });
    cookies.set('org', orgId, {
      path: '/',
      sameSite: 'strict',
      maxAge: 60 * 60 * 24 * 365
    });

    throw redirect(303, '/profile');
  }
};
