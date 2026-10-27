import { describe, it, expect } from 'vitest';
import { buildRecordTimeline } from './record-timeline.js';

const note = (id: string, email: string, at: string, comment = 'hello') =>
  ({
    id,
    comment,
    commented_on: at,
    commented_by: { id: `p-${id}`, user_details: { email, profile_pic: null } }
  }) as never;

const record = (comments?: unknown) => ({
  id: 'r1',
  created_at: '2026-01-01T00:00:00Z',
  created_by: { id: 'u1', name: 'Ann', email: 'ann@x.io' },
  object: { key: 'property', label: 'Property', name_label: 'Name' },
  comments: comments as never
});

describe('buildRecordTimeline', () => {
  it('lists notes newest first with the created entry last', () => {
    const events = buildRecordTimeline(
      record([
        note('a', 'me@x.io', '2026-02-01T00:00:00Z'),
        note('b', 'me@x.io', '2026-03-01T00:00:00Z')
      ]),
      { email: 'me@x.io', isAdmin: false }
    );
    expect(events.map((e) => e.id)).toEqual(['b', 'a', 'created-r1']);
    expect(events[2]).toMatchObject({ type: 'created', body: 'Property created', by: 'ann@x.io' });
  });

  it('is just the created entry when there are no notes', () => {
    expect(buildRecordTimeline(record([]), { isAdmin: false })).toHaveLength(1);
  });

  it('offers edit on your own notes only, matching the email ignoring case', () => {
    const [mine, theirs] = buildRecordTimeline(
      record([
        note('a', 'ME@x.io', '2026-03-01T00:00:00Z'),
        note('b', 'other@x.io', '2026-02-01T00:00:00Z')
      ]),
      { email: 'me@x.io', isAdmin: false }
    );
    expect(mine.note).toEqual({ id: 'a', canEdit: true, canDelete: true });
    expect(theirs.note).toEqual({ id: 'b', canEdit: false, canDelete: false });
  });

  it('lets an admin delete any note but edit none but their own', () => {
    const [other] = buildRecordTimeline(record([note('b', 'other@x.io', '2026-02-01T00:00:00Z')]), {
      email: 'admin@x.io',
      isAdmin: true
    });
    expect(other.note).toEqual({ id: 'b', canEdit: false, canDelete: true });
  });

  it('offers nothing when the viewer is unknown', () => {
    const [n] = buildRecordTimeline(record([note('b', 'a@x.io', '2026-02-01T00:00:00Z')]), {
      email: undefined,
      isAdmin: false
    });
    expect(n.note?.canEdit).toBe(false);
  });
});
