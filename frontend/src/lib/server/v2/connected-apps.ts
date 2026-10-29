/**
 * Connected apps (`/api/connected-apps/`): the apps an admin has let into the
 * org, each with its own key and approved access. Admins only, and only where
 * the server offers it: an install without it answers 404, so the page and its
 * navigation entry are absent there.
 *
 * A key is returned once, by connect and by replace, and is handed straight to
 * the page that shows it. It is never stored or logged here.
 */
import { error, type Cookies } from '@sveltejs/kit';
import { apiStatus } from '#lib/api-errors.js';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';
import { MAX_APP_NAME } from '#lib/v2/connected-apps.js';

type Event = { cookies: Cookies };
type Schemas = components['schemas'];

export type ConnectedApps = Schemas['ConnectedAppListResponse'];
export type ConnectedApp = Schemas['ConnectedApp'];
export type KeyResponse = Schemas['ConnectedAppKeyResponse'];

const ABSENT_FOR_MS = 5 * 60 * 1000;
const NOT_OFFERED = 'Connected apps are not available here.';
const ID = /^[A-Za-z0-9_-]{1,64}$/;

/** Set when the API answers 404, so a self-hosted install is asked once per 5 minutes. */
let absentUntil = 0;
const absent = () => Date.now() < absentUntil;

/** Forget the absent memo; for tests. */
export function resetConnectedAppsAbsentMemo() {
  absentUntil = 0;
}

export function isAppId(id: unknown): id is string {
  return typeof id === 'string' && ID.test(id);
}

/**
 * The page's read. A 404 is a page 404; a 403 (not an admin) is `forbidden`
 * so the page can say so; anything else goes to the error page.
 */
export async function readConnectedApps({
  cookies
}: Event): Promise<({ forbidden: false } & ConnectedApps) | { forbidden: true }> {
  if (absent()) error(404, NOT_OFFERED);
  try {
    const body: ConnectedApps = await apiRequest(
      '/connected-apps/',
      { expectedStatuses: [403, 404] },
      { cookies }
    );
    absentUntil = 0;
    return {
      forbidden: false,
      apps: Array.isArray(body?.apps) ? body.apps : [],
      catalogue: Array.isArray(body?.catalogue) ? body.catalogue : [],
      scopes: Array.isArray(body?.scopes) ? body.scopes : []
    };
  } catch (err) {
    if (apiStatus(err) === 403) return { forbidden: true };
    if (apiStatus(err) === 404) {
      absentUntil = Date.now() + ABSENT_FOR_MS;
      error(404, NOT_OFFERED);
    }
    throw err;
  }
}

/** The name an admin typed, trimmed, or null when it is not 1–60 characters. */
export function cleanName(raw: unknown): string | null {
  const name = typeof raw === 'string' ? raw.trim() : '';
  return name.length >= 1 && name.length <= MAX_APP_NAME ? name : null;
}

export async function connectApp(
  { cookies }: Event,
  body: FormBody<Schemas['ConnectAppRequest']>
): Promise<KeyResponse> {
  return await apiRequest<KeyResponse>('/connected-apps/', { method: 'POST', body }, { cookies });
}

export async function replaceKey({ cookies }: Event, id: string): Promise<KeyResponse> {
  return await apiRequest<KeyResponse>(
    `/connected-apps/${encodeURIComponent(id)}/replace-key/`,
    { method: 'POST' },
    { cookies }
  );
}

export async function disconnectApp({ cookies }: Event, id: string): Promise<void> {
  await apiRequest(`/connected-apps/${encodeURIComponent(id)}/`, { method: 'DELETE' }, { cookies });
}
