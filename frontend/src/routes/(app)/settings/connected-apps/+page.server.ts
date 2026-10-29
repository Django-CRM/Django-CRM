import { fail } from '@sveltejs/kit';
import { apiStatus } from '#lib/api-errors.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import {
  cleanName,
  connectApp,
  disconnectApp,
  isAppId,
  readConnectedApps,
  replaceKey
} from '#lib/server/v2/connected-apps.js';
import { MAX_APP_NAME } from '#lib/v2/connected-apps.js';
import type { Actions, PageServerLoad } from './$types';

/** Admins only; a non-admin gets `forbidden` from the read and a page that says so. */
export const load: PageServerLoad = async ({ cookies }) => readConnectedApps({ cookies });

const refused = (err: unknown, fallback: string) => {
  if (apiStatus(err) === 403) return 'Only an admin can manage connected apps.';
  if (apiStatus(err) === 404) return 'That app is not connected any more.';
  return readableError(err, fallback);
};

const statusOf = (err: unknown) => {
  const status = apiStatus(err);
  return status !== undefined && Number.isInteger(status) && status >= 400 && status < 600
    ? status
    : 400;
};

export const actions: Actions = {
  /** The key in the reply is shown once by the page and is not kept anywhere else. */
  connect: async ({ cookies, request }) => {
    const form = await request.formData();
    const kind = form.get('kind')?.toString().trim() ?? '';
    const scopes = form.getAll('scopes').map(String);
    if (!kind) return fail(400, { error: 'Choose an app.' });
    const body: { kind: string; name?: string; scopes: string[] } = { kind, scopes };
    if (form.has('name')) {
      const name = cleanName(form.get('name'));
      if (!name) {
        return fail(400, { error: `Give the app a name of 1–${MAX_APP_NAME} characters.` });
      }
      body.name = name;
    }
    try {
      const res = await connectApp({ cookies }, body);
      return { revealed: { how: 'connected' as const, name: res.app.name, key: res.key } };
    } catch (err) {
      return fail(statusOf(err), { error: refused(err, 'Could not connect that app.') });
    }
  },

  replace: async ({ cookies, request }) => {
    const id = (await request.formData()).get('id');
    if (!isAppId(id)) return fail(400, { error: 'Which app?' });
    try {
      const res = await replaceKey({ cookies }, id);
      return { revealed: { how: 'replaced' as const, name: res.app.name, key: res.key } };
    } catch (err) {
      return fail(statusOf(err), { error: refused(err, 'Could not replace that key.') });
    }
  },

  disconnect: async ({ cookies, request }) => {
    const id = (await request.formData()).get('id');
    if (!isAppId(id)) return fail(400, { error: 'Which app?' });
    try {
      await disconnectApp({ cookies }, id);
    } catch (err) {
      return fail(statusOf(err), { error: refused(err, 'Could not disconnect that app.') });
    }
    return { disconnected: id };
  }
};
