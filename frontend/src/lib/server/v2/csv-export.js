/**
 * Download half of the record CSV exports (leads, contacts, accounts, deals,
 * tickets and invoices).
 *
 * Each list page links its Export button to `/api/<module>/export/?<the page's
 * own query string>`. The `+server.js` there rebuilds the API query with the
 * very function the page's `load` used, so the file holds the rows the page
 * shows with the same filters, and nothing the page would not forward reaches
 * Django. This streams the answer through rather than buffering it.
 *
 * It exists because the access token lives in an httpOnly cookie that a plain
 * link cannot attach. It never gates: the export views decide what the caller
 * may see, the same way the list views do.
 */
import { env } from '$env/dynamic/public';

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies, request: Request }} event
 * @param {string} path Django endpoint, e.g. `/leads/export/`
 * @param {URLSearchParams} params the list page's API query
 */
export async function forwardCsvExport({ cookies, request }, path, params) {
  const accessToken = cookies.get('jwt_access');
  if (!accessToken) return new Response('Unauthorized', { status: 401 });

  // Every row, not a page of them: the export endpoint ignores paging, and a
  // stray `limit` would only suggest otherwise.
  const query = new URLSearchParams(params);
  query.delete('limit');
  query.delete('offset');
  const qs = query.toString();

  const upstream = await fetch(`${env.PUBLIC_DJANGO_API_URL}/api${path}${qs ? `?${qs}` : ''}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${accessToken}`, Accept: 'text/csv' },
    signal: request.signal
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(`Upstream error: ${upstream.status}`, {
      status: upstream.status || 502
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': upstream.headers.get('Content-Type') || 'text/csv',
      'Content-Disposition':
        upstream.headers.get('Content-Disposition') || 'attachment; filename="export.csv"'
    }
  });
}
