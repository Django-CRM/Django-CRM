/**
 * Private apps (`/api/private-apps/`): apps an admin uploads as a file, previews
 * on a consent screen and installs. Admins only, and only where the server
 * offers them: an install without them answers 404, so the section is absent.
 *
 * A key in a reply is handed straight to the page that shows it once. It is
 * never stored or logged here.
 */
import { error, type Cookies } from '@sveltejs/kit';
import { apiStatus } from '#lib/api-errors.js';
import { apiRequest } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';

type Event = { cookies: Cookies };
type Schemas = components['schemas'];

export type PrivateApp = Schemas['PrivateApp'];
export type UploadReply = Schemas['PrivateAppUploadResponse'];
export type ApplyReply = Schemas['PrivateAppAppliedResponse'];
export type PrivateAppReply = Schemas['PrivateAppResponse'];
export type KeyReply = Schemas['PrivateAppKeyResponse'];

const ABSENT_FOR_MS = 5 * 60 * 1000;
const ID = /^[A-Za-z0-9_-]{1,64}$/;

/** Set when the API answers 404, so a self-hosted install is asked once per 5 minutes. */
let absentUntil = 0;

/** Forget the absent memo; for tests. */
export function resetPrivateAppsAbsentMemo() {
  absentUntil = 0;
}

export const isUploadId = (id: unknown): id is string => typeof id === 'string' && ID.test(id);
export const isPrivateAppId = (id: unknown): id is string => typeof id === 'string' && ID.test(id);

function checked(id: string, ok: (id: unknown) => boolean): string {
  if (!ok(id)) error(404, 'Not found.');
  return encodeURIComponent(id);
}

export async function readPrivateApps({
  cookies
}: Event): Promise<{ offered: false } | { offered: true; forbidden: boolean; apps: PrivateApp[] }> {
  if (Date.now() < absentUntil) return { offered: false };
  try {
    const body: Schemas['PrivateAppListResponse'] = await apiRequest(
      '/private-apps/',
      { quietErrors: true, expectedStatuses: [403, 404] },
      { cookies }
    );
    absentUntil = 0;
    return { offered: true, forbidden: false, apps: Array.isArray(body?.apps) ? body.apps : [] };
  } catch (err) {
    if (apiStatus(err) === 403) return { offered: true, forbidden: true, apps: [] };
    if (apiStatus(err) === 404) {
      absentUntil = Date.now() + ABSENT_FOR_MS;
      return { offered: false };
    }
    throw err;
  }
}

export async function uploadManifest({ cookies }: Event, text: string): Promise<UploadReply> {
  return await apiRequest<UploadReply>(
    '/private-apps/uploads/',
    { method: 'POST', body: { manifest: text }, quietErrors: true },
    { cookies }
  );
}

export async function readUpload({ cookies }: Event, id: string): Promise<UploadReply> {
  const path = checked(id, isUploadId);
  return await apiRequest<UploadReply>(
    `/private-apps/uploads/${path}/`,
    { quietErrors: true, expectedStatuses: [404, 409] },
    { cookies }
  );
}

export async function applyUpload(
  { cookies }: Event,
  id: string,
  trust: boolean
): Promise<ApplyReply> {
  const path = checked(id, isUploadId);
  return await apiRequest<ApplyReply>(
    `/private-apps/uploads/${path}/apply/`,
    { method: 'POST', body: { trust }, quietErrors: true },
    { cookies }
  );
}

export async function removePrivateApp(
  { cookies }: Event,
  appId: string
): Promise<PrivateAppReply> {
  const path = checked(appId, isPrivateAppId);
  return await apiRequest<PrivateAppReply>(
    `/private-apps/${path}/remove/`,
    { method: 'POST', quietErrors: true },
    { cookies }
  );
}

export async function replacePrivateKey({ cookies }: Event, appId: string): Promise<KeyReply> {
  const path = checked(appId, isPrivateAppId);
  return await apiRequest<KeyReply>(
    `/private-apps/${path}/replace-key/`,
    { method: 'POST', quietErrors: true },
    { cookies }
  );
}
