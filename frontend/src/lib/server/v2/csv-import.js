/**
 * Form-action half of the CSV importers (contacts, tickets and leads).
 *
 * `ContactImportDrawer`, `TicketImportDrawer` and `LeadImportDrawer` post the
 * chosen file to `?/importPreview` and `?/importCommit` on their list page and
 * read the result under those same keys, `importError` and `importErrors` on failure.
 * This forwards the file to Django as multipart and maps the reply onto that
 * shape. It never gates: the import views decide who may import (admin or
 * sales access), cap the size and rows, and scope every write to the caller's
 * org.
 */
import { fail } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';

/**
 * @param {{ request: Request, cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} path Django endpoint, e.g. `/contacts/import/preview/`
 * @param {'importPreview' | 'importCommit'} key where the drawer reads the success body
 */
export async function forwardCsvImport({ request, cookies }, path, key) {
  const file = (await request.formData()).get('file');
  if (!(file instanceof File) || file.size === 0) {
    return fail(400, { importError: 'Choose a CSV file.' });
  }
  const body = new FormData();
  body.set('file', file, file.name || 'upload.csv');
  try {
    return { [key]: await apiRequest(path, { method: 'POST', body }, { cookies }) };
  } catch (err) {
    const failure = /** @type {Error & { status?: number, body?: any }} */ (err);
    if (!failure.status) throw err;
    const data = failure.body ?? {};
    return fail(failure.status, {
      importError:
        failure.status === 403
          ? 'Importing needs an admin, or a member with sales access.'
          : data.message || data.header_error || 'Import failed',
      importErrors: Array.isArray(data.errors) ? data.errors : []
    });
  }
}
