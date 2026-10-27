/** Server calls for a custom object's board and the setting that turns it on. */
import { error, fail, type Cookies } from '@sveltejs/kit';
import { apiRequest } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';
import { apiStatus } from '#lib/api-errors.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { checkKey, OBJECT_GONE } from '#lib/server/v2/custom-objects.js';
import { UUID } from '#lib/v2/custom-objects.js';

type Event = { cookies: Cookies };

type Enterprise = components['schemas'];
export type BoardRecord = Enterprise['CustomRecordBoardCard'];
export type Board = Enterprise['CustomRecordBoard'];
export type MoveBody = Enterprise['CustomRecordBoardMoveRequest'];
type Moved = Enterprise['CustomRecordMoved'];
type ObjectSummary = Enterprise['CustomObjectSummary'];

/** The board, grouped by the object's board field; filters are the list's. */
export async function getBoard(
  { cookies }: Event,
  key: string,
  filters?: URLSearchParams
): Promise<Board> {
  checkKey(key);
  const params = new URLSearchParams();
  for (const [name, value] of filters ?? []) {
    if (name === 'limit' || name === 'offset' || name === 'page') continue;
    params.append(name, value);
  }
  const qs = params.toString().replaceAll('+', '%20');
  try {
    return await apiRequest(
      `/objects/${key}/board/${qs ? `?${qs}` : ''}`,
      { expectedStatuses: [404] },
      { cookies }
    );
  } catch (err) {
    if (apiStatus(err) === 404) error(404, OBJECT_GONE);
    throw err;
  }
}

export async function moveBoardCard(
  { cookies }: Event,
  key: string,
  body: MoveBody
): Promise<Moved> {
  checkKey(key);
  return await apiRequest(`/objects/${key}/board/move/`, { method: 'POST', body }, { cookies });
}

/** Sets the board field, or "" to turn the board off. Admins only; the API decides. */
export async function setBoardField(
  { cookies }: Event,
  key: string,
  field: string
): Promise<ObjectSummary> {
  checkKey(key);
  return await apiRequest(
    `/objects/${key}/`,
    { method: 'PATCH', body: { board_field: field } },
    { cookies }
  );
}

/** The contract body for a drop, or a sentence saying why there is none. */
export function readMove(form: FormData): { body: MoveBody } | { error: string } {
  const record = String(form.get('record') ?? '');
  const kind = String(form.get('kind') ?? '');
  if (!UUID.test(record)) return { error: 'Missing record.' };
  let value: string | null;
  if (kind === 'none') value = null;
  else if (kind === 'option') {
    value = String(form.get('value') ?? '');
    if (!value) return { error: 'Missing column.' };
  } else return { error: 'Cards cannot be moved into that column.' };
  const neighbour = (name: string) => {
    const id = String(form.get(name) ?? '');
    return UUID.test(id) ? id : null;
  };
  return {
    body: { record, value, above_id: neighbour('above_id'), below_id: neighbour('below_id') }
  };
}

/** A single-field 400 reads as the sentence alone; the key means nothing to the person moving a card. */
function withoutFieldKey(message: string): string {
  return message.replace(/^[a-z][a-z0-9_]*: (?=\S)/, '');
}

/**
 * The board page's `move` action. A refusal is shown while the card snaps
 * back; a success carries the moved record, whose `can_edit` is current.
 */
export async function moveAction(event: Event & { request: Request; params: { key: string } }) {
  const key = checkKey(event.params.key);
  const read = readMove(await event.request.formData());
  if ('error' in read) return fail(400, { error: read.error });
  try {
    const moved = await moveBoardCard(event, key, read.body);
    return { success: true, record: moved.record };
  } catch (err) {
    if (apiStatus(err) === 404) return fail(404, { error: 'That record is not one you can move.' });
    const status = apiStatus(err);
    const code = status !== undefined && status >= 400 && status < 500 ? status : 400;
    const message = readableError(err, 'Could not move the record.');
    return fail(code, { error: code === 400 ? withoutFieldKey(message) : message });
  }
}
