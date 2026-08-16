/**
 * CSV export proxy for the time report.
 *
 * Streams the upstream `text/csv` straight through rather than buffering it,
 * so a year of entries starts downloading immediately, and the same for the
 * reason `cases/analytics/export` does it: this is the pattern, not a second
 * opinion about it.
 *
 * It exists because the access token lives in an httpOnly cookie. A plain
 * `<a download>` from the page cannot attach it, so the link points here and
 * this handler adds the Authorization header on the server. The query string
 * is forwarded as given; the API validates every parameter and decides what
 * this caller may see, which is where that decision belongs.
 */

import { env } from '$env/dynamic/public';

const API_BASE_URL = `${env.PUBLIC_DJANGO_API_URL}/api`;

/** @type {import('./$types').RequestHandler} */
export async function GET({ cookies, request, url }) {
  const accessToken = cookies.get('jwt_access');
  if (!accessToken) return new Response('Unauthorized', { status: 401 });

  const qs = url.searchParams.toString();
  const upstream = await fetch(`${API_BASE_URL}/time-entries/report/export/${qs ? `?${qs}` : ''}`, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'text/csv'
    },
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
        upstream.headers.get('Content-Disposition') || 'attachment; filename="time.csv"'
    }
  });
}
