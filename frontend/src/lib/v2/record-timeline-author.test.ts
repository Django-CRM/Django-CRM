import { describe, it, expect } from 'vitest';
import { buildRecordTimeline } from './record-timeline.js';

const note = (user_details: any) => ({
  id: 'c1',
  comment: 'Hello',
  commented_on: '2026-10-01T00:00:00Z',
  commented_by: { id: 'p1', user_details }
});

const byOf = (details: any) =>
  buildRecordTimeline(
    {
      id: 'r1',
      created_at: '2026-09-01T00:00:00Z',
      created_by: null,
      object: {},
      comments: [note(details)]
    } as any,
    { email: 'me@example.com', isAdmin: false }
  ).find((e) => e.type === 'note')?.by;

describe('timeline note author', () => {
  it('shows the name of a connected app', () => {
    expect(byOf({ email: 'app@example.invalid', name: 'Example app', profile_pic: null })).toBe(
      'Example app'
    );
  });
  it('shows the email for a person, as before', () => {
    expect(byOf({ email: 'ada@example.com', name: null, profile_pic: null })).toBe(
      'ada@example.com'
    );
  });
});
