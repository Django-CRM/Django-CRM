import { error, fail } from '@sveltejs/kit';
import { apiErrorBody, apiStatus } from '#lib/api-errors.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import {
  applyUpload,
  isUploadId,
  readPrivateApps,
  readUpload
} from '#lib/server/v2/private-apps.js';
import { groupPreview } from '#lib/server/v2/apps.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * The consent screen for one stored upload. A 409 (the file is no longer a
 * higher version than the installed one) is shown as a message with a link
 * back, not as an error page.
 */
const reason = (err: unknown, fallback: string) => {
  const detail = apiErrorBody(err)?.detail;
  return typeof detail === 'string' && detail ? detail : readableError(err, fallback);
};

export const load: PageServerLoad = async ({ cookies, params }) => {
  if (!isUploadId(params.id)) error(404, 'Not found.');
  let reply;
  try {
    reply = await readUpload({ cookies }, params.id);
  } catch (err) {
    const status = apiStatus(err);
    if (status === 409) {
      return { stale: reason(err, 'This version can no longer be installed.') };
    }
    if (status === 403) return { forbidden: true as const };
    if (status === 404) error(404, 'That upload was not found.');
    throw err;
  }
  const mine = await readPrivateApps({ cookies }).catch(() => null);
  const installed =
    mine && mine.offered ? (mine.apps.find((a) => a.id === reply.upload.app_id) ?? null) : null;
  return {
    upload: reply.upload,
    preview: reply.preview,
    groups: groupPreview(reply.preview.items),
    installedVersion: installed && installed.status !== 'removed' ? installed.version : null
  };
};

export const actions: Actions = {
  /** The key in the reply is shown once by the page and kept nowhere else. */
  async apply({ cookies, params, request }) {
    if (!isUploadId(params.id)) return fail(404, { error: 'That upload was not found.' });
    const form = await request.formData();
    // An unticked box submits nothing, so absent is an explicit no.
    const trust = form.get('trust') === 'on';
    if (form.get('asks_access') === '1' && !trust) {
      return fail(400, { error: 'Tick the box to say you trust whoever wrote this app.' });
    }
    try {
      const res = await applyUpload({ cookies }, params.id, trust);
      return {
        applied: {
          name: res.app.name,
          version: res.app.version,
          key: res.key,
          mode: form.get('mode')?.toString() ?? 'install',
          asksAccess: form.get('asks_access') === '1',
          hadAccess: form.get('had_access') === '1'
        }
      };
    } catch (err) {
      const status = apiStatus(err);
      return fail(status === 403 || status === 404 || status === 409 ? status : 400, {
        error:
          status === 403
            ? 'Only an administrator can install apps.'
            : reason(err, 'Could not install that app.')
      });
    }
  }
};
