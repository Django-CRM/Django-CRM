/**
 * The timeline of a custom record: its notes and a "created" entry, newest
 * first. Browser-safe, so the page and vitest can both use it.
 */
import type { components } from '#lib/enterprise-api-schema.js';

type Record = components['schemas']['CustomRecordDetail'];

export type RecordTimelineEvent = {
  id: string;
  type: 'note' | 'created';
  body: string;
  at: string;
  by: string | null;
  note?: { id: string; canEdit: boolean; canDelete: boolean };
};

/**
 * Edit is the author's alone; delete is the author's or an admin's. Both
 * are display hints, since the API refuses the rest.
 */
export function buildRecordTimeline(
  record: Pick<Record, 'id' | 'created_at' | 'created_by' | 'object' | 'comments'>,
  viewer: { email?: string | null; isAdmin: boolean }
): RecordTimelineEvent[] {
  const me = (viewer.email ?? '').toLowerCase();
  const notes: RecordTimelineEvent[] = record.comments.map((c) => {
    const email = c.commented_by?.user_details?.email ?? '';
    const mine = me !== '' && email.toLowerCase() === me;
    return {
      id: c.id,
      type: 'note',
      body: c.comment,
      at: c.commented_on,
      by: email || null,
      note: { id: c.id, canEdit: mine, canDelete: mine || viewer.isAdmin }
    };
  });
  const created: RecordTimelineEvent = {
    id: `created-${record.id}`,
    type: 'created',
    body: `${record.object.label || 'Record'} created`,
    at: record.created_at,
    by: record.created_by?.email || record.created_by?.name || null
  };
  return [...notes, created].sort((a, b) => {
    const diff = new Date(b.at).getTime() - new Date(a.at).getTime();
    if (diff !== 0) return diff;
    return a.type === 'created' ? 1 : b.type === 'created' ? -1 : 0;
  });
}
