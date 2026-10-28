import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const { loadMatches, dismissMatch, undismissMatch } = await import('#lib/server/v2/real-estate.js');

const cookies = { get: () => 'token' } as never;
const ID = '0b9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6b';
const OTHER = '1c9f4c6e-3f0a-4a5b-9a77-1c2d3e4f5a6c';
const status = (n: number) => Object.assign(new Error('failed'), { status: n });

const ANSWER = {
  side: 'property',
  object: { key: 'requirement', label: 'Requirement', label_plural: 'Requirements' },
  matches: [
    { id: OTHER, name: 'Family home', facts: [{ label: 'Budget', value: 'up to 250,000' }] }
  ],
  dismissed_count: 2
};

beforeEach(() => {
  apiRequest.mockReset();
});

describe('loadMatches', () => {
  it('maps the answer for the page', async () => {
    apiRequest.mockResolvedValue(ANSWER);
    expect(await loadMatches({ cookies }, ID, false)).toEqual({
      state: 'ok',
      heading: 'Matching requirements',
      objectKey: 'requirement',
      rows: ANSWER.matches,
      dismissedCount: 2,
      dismissed: null
    });
    expect(apiRequest.mock.calls[0][0]).toBe(`/real-estate/matches/${ID}/`);
  });

  it('also fetches the dismissed list when asked', async () => {
    apiRequest.mockResolvedValueOnce(ANSWER).mockResolvedValueOnce({
      ...ANSWER,
      matches: [{ id: ID, name: 'Old', facts: [] }]
    });
    const view = await loadMatches({ cookies }, ID, true);
    expect(apiRequest.mock.calls.map((c) => c[0])).toContain(
      `/real-estate/matches/${ID}/?dismissed=true`
    );
    expect(view).toMatchObject({ dismissed: [{ id: ID, name: 'Old', facts: [] }] });
  });

  it('turns any failure into a quiet failed state', async () => {
    apiRequest.mockImplementation(async () => {
      throw status(500);
    });
    expect(await loadMatches({ cookies }, ID, false)).toEqual({ state: 'failed' });
    apiRequest.mockImplementation(async () => {
      throw status(404);
    });
    expect(await loadMatches({ cookies }, ID, false)).toEqual({ state: 'failed' });
  });
});

function event(fields: Record<string, string>, id = ID) {
  const body = new FormData();
  for (const [k, v] of Object.entries(fields)) body.set(k, v);
  return {
    cookies,
    params: { id },
    request: new Request('http://test/x', { method: 'POST', body })
  };
}

describe('dismiss and undismiss', () => {
  it('PUTs the dismissal', async () => {
    apiRequest.mockResolvedValue(null);
    expect(await dismissMatch(event({ other: OTHER }))).toEqual({ matchChanged: true });
    expect(apiRequest).toHaveBeenCalledWith(
      `/real-estate/matches/${ID}/dismissed/${OTHER}/`,
      { method: 'PUT' },
      { cookies }
    );
  });

  it('DELETEs it on undo', async () => {
    apiRequest.mockResolvedValue(null);
    expect(await undismissMatch(event({ other: OTHER }))).toEqual({ matchChanged: true });
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'DELETE' });
  });

  it('answers 404 when the API does', async () => {
    apiRequest.mockImplementation(async () => {
      throw status(404);
    });
    const result = (await dismissMatch(event({ other: OTHER }))) as { status: number };
    expect(result.status).toBe(404);
  });

  it('answers 400 on another failure, without leaking a 500', async () => {
    apiRequest.mockImplementation(async () => {
      throw status(500);
    });
    const result = (await undismissMatch(event({ other: OTHER }))) as {
      status: number;
      data: { matchError: string };
    };
    expect(result.status).toBe(400);
    expect(typeof result.data.matchError).toBe('string');
  });

  it('refuses a malformed id before any API call', async () => {
    const result = (await dismissMatch(event({ other: 'nope' }))) as { status: number };
    expect(result.status).toBe(404);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
