import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getCalendarFeed, issueCalendarFeed, disableCalendarFeed } =
  await import('$lib/server/v2/calendar-feed.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

describe('calendar feed', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads the self-scoped endpoint and keeps only the state', async () => {
    apiRequest.mockResolvedValue({
      error: false,
      enabled: true,
      created_at: '2026-09-27T10:00:00Z',
      last_used_at: null,
      url: 'should never be here'
    });
    const feed = await getCalendarFeed(event);
    expect(apiRequest.mock.calls[0][0]).toBe('/profile/calendar-feed/');
    expect(feed).toEqual({
      enabled: true,
      created_at: '2026-09-27T10:00:00Z',
      last_used_at: null
    });
  });

  it('reads a disabled feed as off', async () => {
    apiRequest.mockResolvedValue({ enabled: false, created_at: null, last_used_at: null });
    expect((await getCalendarFeed(event)).enabled).toBe(false);
  });

  it('issues with a bodiless POST and returns the one-time URL', async () => {
    apiRequest.mockResolvedValue({ enabled: true, url: 'https://api.example.com/x.ics' });
    const url = await issueCalendarFeed(event);
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/profile/calendar-feed/');
    expect(options).toEqual({ method: 'POST' });
    expect(url).toBe('https://api.example.com/x.ics');
  });

  it('disables with a DELETE', async () => {
    apiRequest.mockResolvedValue({ enabled: false });
    await disableCalendarFeed(event);
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/profile/calendar-feed/',
      { method: 'DELETE' }
    ]);
  });
});
