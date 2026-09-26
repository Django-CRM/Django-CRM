import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/public';

/**
 * Stream an invoice PDF from the API.
 *
 * The PDF endpoint is authenticated with the bearer token, which lives in an
 * httpOnly cookie the browser never sends to the API host, so a plain
 * `<a href>` straight to `:8000/api/.../pdf/` would arrive unauthenticated and
 * 401. This server route carries the token for the browser and pipes the bytes
 * back. Authorization is still the API's call: `get_invoice_or_error` runs on
 * the far side, so a member who may not see the invoice gets a 404 here too.
 *
 * @type {import('./$types').RequestHandler}
 */
export async function GET({ params, cookies }) {
  const token = cookies.get('jwt_access');
  if (!token) error(401, 'Not signed in.');

  const upstream = await fetch(`${env.PUBLIC_DJANGO_API_URL}/api/invoices/${params.id}/pdf/`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!upstream.ok) {
    // 403/404 collapse to 404: do not confirm an invoice exists to someone who
    // may not read it. 503 means WeasyPrint is missing on the server.
    if (upstream.status === 403 || upstream.status === 404) {
      error(404, 'That invoice does not exist, or you do not have access to it.');
    }
    error(502, 'Could not generate the PDF.');
  }

  const body = await upstream.arrayBuffer();
  return new Response(body, {
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition':
        upstream.headers.get('content-disposition') ?? `inline; filename="invoice-${params.id}.pdf"`
    }
  });
}
