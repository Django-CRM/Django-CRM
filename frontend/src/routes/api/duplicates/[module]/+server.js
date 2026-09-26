import { json } from '@sveltejs/kit';
import { checkDuplicates } from '$lib/server/v2/duplicates.js';

/**
 * The create forms' "possible duplicates" check. The form runs in the browser
 * and the access token is an httpOnly cookie, so the check is proxied through
 * here. A POST with a JSON body, like the API it calls, so the email and
 * phone being typed never appear in a URL or an access log.
 * `checkDuplicates` forwards only the module's own fields and answers 404 for
 * any module but leads, contacts and accounts.
 *
 * A failed check answers an empty list: it is a hint beside a form, and the
 * form must still save.
 *
 * @type {import('./$types').RequestHandler}
 */
export async function POST({ params, request, cookies }) {
  /** @type {any} */
  let criteria;
  try {
    criteria = await request.json();
  } catch {
    return json({ duplicates: [] }, { status: 400 });
  }
  try {
    return json({ duplicates: await checkDuplicates({ cookies }, params.module, criteria) });
  } catch (/** @type {any} */ err) {
    if (err?.status === 404) throw err;
    return json({ duplicates: [] });
  }
}
