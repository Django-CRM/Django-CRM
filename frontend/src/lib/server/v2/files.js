/**
 * Reaching an uploaded file, without handing the browser a `/media/` URL.
 *
 * The four modules that render attachments used to resolve `file_path`
 * against the Django origin and put that straight in an `href`. Two things
 * were wrong with it. A cross-origin `<a href>` carries neither the JWT nor
 * the SvelteKit session cookie, so the link 403s for the person who is
 * allowed to open the file; and where it does resolve, `/media/` is guarded
 * only by "has an org context", which is every signed-in user of every
 * tenant. The link was broken for its owner and open to everybody else.
 *
 * So the client never sees a storage path. It gets a route on this origin,
 * which streams the file from an authenticated backend endpoint that checks
 * the record's own read predicate first.
 */
import { env } from '$env/dynamic/public';

const API_BASE_URL = `${env.PUBLIC_DJANGO_API_URL}/api`;

/**
 * Where a page links to download one attachment.
 *
 * @param {string|null|undefined} id
 * @returns {string|null} null when there is nothing to link to, so callers
 *   can keep rendering the name as plain text rather than a dead link.
 */
export function attachmentHref(id) {
  return id ? `/api/attachments/${id}/download` : null;
}

/**
 * Where a page links to download a document's file.
 *
 * @param {string|null|undefined} id
 * @returns {string|null}
 */
export function documentHref(id) {
  return id ? `/api/documents/${id}/download` : null;
}

/**
 * Stream one file down from the API with the caller's token attached.
 *
 * The body is piped rather than buffered so a large file does not sit in this
 * process's memory, and the upstream's own `Content-Disposition` is passed
 * through, because the backend is the one that knows what the file is called.
 *
 * Upstream status codes are passed through as they are: a 404 means no file
 * this caller may see (the attachment download answers it for a file on a
 * record they cannot open too, so it does not confirm the file exists), and a
 * 403 is a refusal the endpoint chose to state.
 * Flattening either into a 500 would turn a correct refusal into a bug report.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies, request: Request }} event
 * @param {string} path Upstream path under `/api`, e.g. `/attachments/<id>/download/`
 */
export async function streamDownload({ cookies, request }, path) {
  const accessToken = cookies.get('jwt_access');
  if (!accessToken) return new Response('Unauthorized', { status: 401 });

  const upstream = await fetch(`${API_BASE_URL}${path}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: request.signal
  });

  if (!upstream.ok || !upstream.body) {
    return new Response('Could not download that file.', {
      status: upstream.status || 502
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': upstream.headers.get('Content-Type') || 'application/octet-stream',
      'Content-Disposition': upstream.headers.get('Content-Disposition') || 'attachment',
      // The response is one tenant's private file. Nothing in front of this
      // may keep a copy of it.
      'Cache-Control': 'private, no-store'
    }
  });
}
