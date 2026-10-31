import { fail, redirect, type RequestEvent } from '@sveltejs/kit';
import {
  getModules,
  moduleErrors,
  moveBody,
  putModules,
  switchBody
} from '#lib/server/v2/modules.js';
import {
  appRefusal,
  installApp,
  isAppAction,
  isAppId,
  listApps,
  previewApp,
  removeApp,
  upgradeApp
} from '#lib/server/v2/apps.js';
import {
  isPrivateAppId,
  readPrivateApps,
  removePrivateApp,
  replacePrivateKey,
  uploadManifest
} from '#lib/server/v2/private-apps.js';
import { clearSampleData } from '#lib/server/packs.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { viewerIsAdmin } from '#lib/server/v2/organization.js';
import { apiErrorBody, apiStatus } from '#lib/api-errors.js';
import type { ModuleRow } from '#lib/v2/modules.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * Settings › Apps: industry apps above the modules. `?app=<id>&do=<action>`
 * opens the confirmation for one app with the server's preview, and an action
 * that succeeds lands on `?done=<action>&app=<id>`, so the page reads the new
 * state rather than the old preview.
 */
export const load: PageServerLoad = async (event) => {
  const { cookies, url } = event;
  const [answer, apps, privateApps] = await Promise.all([
    getModules(event),
    listApps(cookies),
    // A failed read hides the section rather than the whole page.
    readPrivateApps({ cookies }).catch(() => ({ offered: false as const }))
  ]);
  // The modules answer is the server's word on who may edit; without it, the token's claim.
  const canEdit = answer ? answer.canEdit : viewerIsAdmin(cookies);
  if (!canEdit) return { forbidden: true };

  const known = (id: string | null) => apps.apps.find((a) => a.id === id) ?? null;

  let confirm: {
    app: string | null;
    action: string;
    preview: Awaited<ReturnType<typeof previewApp>> | null;
    error: string;
  } | null = null;
  const id = url.searchParams.get('app');
  const action = url.searchParams.get('do');
  if (apps.state === 'ok' && known(id) && isAppAction(action)) {
    try {
      const preview = await previewApp(cookies, id as string, String(action));
      confirm = { app: id, action, preview, error: '' };
    } catch (err) {
      confirm = { app: id, action, preview: null, error: appRefusal(err, String(action)) };
    }
  }

  const doneAction = url.searchParams.get('done');
  const done = isAppAction(doneAction) && known(id) ? { action: doneAction, app: id } : null;

  return {
    modules: answer?.modules ?? null,
    modulesFailed: !answer,
    apps,
    privateApps,
    confirm,
    done
  };
};

/** Re-reads the list, builds the body from it and writes it. */
async function writeModules(
  event: RequestEvent,
  key: string,
  build: (rows: ModuleRow[]) => { off: string[]; order: string[] } | null
) {
  const answer = await getModules(event);
  if (!answer) return fail(400, { errors: ['Modules could not be loaded.'], key });
  if (!answer.canEdit) {
    return fail(403, { errors: ['Only an organization admin can switch modules on or off.'], key });
  }
  const body = build(answer.modules);
  if (!body) return fail(400, { errors: ['That module could not be changed.'], key });
  try {
    await putModules(event, body);
  } catch (err) {
    return fail(apiStatus(err) === 403 ? 403 : 400, { errors: moduleErrors(err), key });
  }
  return { saved: true };
}

async function appAction(event: RequestEvent, action: 'install' | 'upgrade' | 'remove') {
  const form = await event.request.formData();
  const id = form.get('app')?.toString() ?? '';
  if (!isAppId(id)) return fail(400, { appError: 'That app is not in the catalogue.', app: id });
  try {
    if (action === 'install') {
      // An unticked box submits nothing, so absent is an explicit no.
      await installApp(event.cookies, id, form.get('sample_data') === 'on');
    } else if (action === 'upgrade') {
      await upgradeApp(event.cookies, id);
    } else {
      await removeApp(event.cookies, id);
    }
  } catch (err) {
    const refused = apiStatus(err);
    const status = refused === 403 || refused === 404 || refused === 409 ? refused : 400;
    return fail(status, { appError: appRefusal(err, action), app: id });
  }
  redirect(303, `/settings/apps?done=${action}&app=${encodeURIComponent(id)}`);
}

const MAX_FILE_BYTES = 1_000_000;

const privateRefusal = (err: unknown, fallback: string) =>
  apiStatus(err) === 403
    ? 'Only an administrator can manage your own apps.'
    : readableError(err, fallback);

const privateStatus = (err: unknown) => {
  const status = apiStatus(err);
  return status === 403 || status === 404 || status === 409 ? status : 400;
};

export const actions: Actions = {
  /** Pasted text or a chosen file; the API validates and stores it, the consent screen follows. */
  async upload({ cookies, request }) {
    const form = await request.formData();
    const file = form.get('file');
    let text = form.get('manifest')?.toString() ?? '';
    if (file instanceof File && file.size > 0) {
      if (file.size > MAX_FILE_BYTES) {
        return fail(400, { uploadError: 'That file is too large to be an app.' });
      }
      text = await file.text();
    }
    if (!text.trim()) {
      return fail(400, { uploadError: 'Choose a .json file or paste the text of the file.' });
    }
    let id: string;
    try {
      id = (await uploadManifest({ cookies }, text)).upload.id;
    } catch (err) {
      const errors = (apiErrorBody(err)?.errors ?? null) as Record<string, string[]> | null;
      if (apiStatus(err) === 400 && errors && typeof errors === 'object') {
        return fail(400, { uploadErrors: errors });
      }
      return fail(privateStatus(err), {
        uploadError: privateRefusal(err, 'Could not read that file.')
      });
    }
    redirect(303, `/settings/apps/uploads/${encodeURIComponent(id)}`);
  },

  async removePrivate({ cookies, request }) {
    const id = (await request.formData()).get('id');
    if (!isPrivateAppId(id)) return fail(400, { privateError: 'Which app?' });
    try {
      const res = await removePrivateApp({ cookies }, id);
      return { removedPrivate: res.app.name };
    } catch (err) {
      return fail(privateStatus(err), {
        privateError: privateRefusal(err, 'Could not remove that app.')
      });
    }
  },

  /** The key in the reply is shown once by the page and kept nowhere else. */
  async replaceKey({ cookies, request }) {
    const id = (await request.formData()).get('id');
    if (!isPrivateAppId(id)) return fail(400, { privateError: 'Which app?' });
    try {
      const res = await replacePrivateKey({ cookies }, id);
      return { revealed: { name: res.app.name, key: res.key } };
    } catch (err) {
      return fail(privateStatus(err), {
        privateError: privateRefusal(err, 'Could not replace that key.')
      });
    }
  },

  install: (event) => appAction(event, 'install'),
  upgrade: (event) => appAction(event, 'upgrade'),
  remove: (event) => appAction(event, 'remove'),

  async switch(event) {
    const form = await event.request.formData();
    const key = form.get('key')?.toString() ?? '';
    const on = form.get('on')?.toString();
    if (on !== 'true' && on !== 'false') {
      return fail(400, { errors: ['That module could not be changed.'], key });
    }
    return writeModules(event, key, (rows) => switchBody(rows, key, on === 'true'));
  },

  async move(event) {
    const form = await event.request.formData();
    const key = form.get('key')?.toString() ?? '';
    const direction = form.get('direction')?.toString() ?? '';
    return writeModules(event, key, (rows) => moveBody(rows, key, direction));
  },

  clearSampleData: async ({ cookies }) => {
    try {
      const { deleted, retained_by_type } = await clearSampleData(cookies);
      // Kept records are sample rows someone has since attached real work to.
      const retained = Object.values(retained_by_type ?? {}).reduce((a, b) => a + b, 0);
      return { cleared: deleted ?? 0, retained };
    } catch (err) {
      if (apiStatus(err) === 403) {
        return fail(403, { sampleError: 'Only an administrator can clear sample data.' });
      }
      return fail(400, { sampleError: readableError(err, 'Could not clear sample data.') });
    }
  }
};
