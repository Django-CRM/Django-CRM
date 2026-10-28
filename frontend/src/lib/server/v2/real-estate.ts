/** Property and requirement matches (`/api/real-estate/matches/`). */
import { fail, type Cookies } from '@sveltejs/kit';
import { apiRequest } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';
import { apiStatus } from '#lib/api-errors.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { checkId } from '#lib/server/v2/custom-objects.js';

type Event = { cookies: Cookies };

export type MatchRow = components['schemas']['RealEstateMatch'];
type MatchesAnswer = components['schemas']['RealEstateMatches'];

export type MatchesView =
  | { state: 'failed' }
  | {
      state: 'ok';
      heading: string;
      objectKey: string;
      rows: MatchRow[];
      dismissedCount: number;
      dismissed: MatchRow[] | null;
    };

const asRows = (value: MatchRow[] | undefined): MatchRow[] =>
  (Array.isArray(value) ? value : []).map((row) => ({
    id: String(row?.id ?? ''),
    name: String(row?.name ?? ''),
    facts: (Array.isArray(row?.facts) ? row.facts : []).map((fact) => ({
      label: String(fact?.label ?? ''),
      value: String(fact?.value ?? '')
    }))
  }));

async function fetchMatches(event: Event, id: string, dismissed: boolean): Promise<MatchesAnswer> {
  checkId(id);
  const qs = dismissed ? '?dismissed=true' : '';
  return await apiRequest(
    `/real-estate/matches/${id}/${qs}`,
    { quietErrors: true, expectedStatuses: [404] },
    { cookies: event.cookies }
  );
}

/** Never throws: a failed call becomes `{ state: 'failed' }` so the record page still loads. */
export async function loadMatches(
  event: Event,
  id: string,
  showDismissed: boolean
): Promise<MatchesView> {
  try {
    const [answer, other] = await Promise.all([
      fetchMatches(event, id, false),
      showDismissed ? fetchMatches(event, id, true) : Promise.resolve(null)
    ]);
    const plural = String(answer.object?.label_plural ?? '').toLowerCase();
    return {
      state: 'ok',
      heading: plural ? `Matching ${plural}` : 'Matches',
      objectKey: String(answer.object?.key ?? ''),
      rows: asRows(answer.matches),
      dismissedCount: Number.isInteger(answer.dismissed_count) ? answer.dismissed_count : 0,
      dismissed: other ? asRows(other.matches) : null
    };
  } catch {
    return { state: 'failed' };
  }
}

function dismissAction(method: 'PUT' | 'DELETE', fallback: string) {
  return async ({
    cookies,
    params,
    request
  }: {
    cookies: Cookies;
    params: { id?: string };
    request: Request;
  }) => {
    const other = String((await request.formData()).get('other') ?? '');
    try {
      const id = checkId(params.id);
      checkId(other);
      await apiRequest(`/real-estate/matches/${id}/dismissed/${other}/`, { method }, { cookies });
    } catch (err) {
      const status = apiStatus(err);
      const code = status !== undefined && [400, 403, 404].includes(status) ? status : 400;
      return fail(code, { matchError: readableError(err, fallback) });
    }
    return { matchChanged: true };
  };
}

export const dismissMatch = dismissAction('PUT', 'Could not dismiss that match.');
export const undismissMatch = dismissAction('DELETE', 'Could not undo that.');
