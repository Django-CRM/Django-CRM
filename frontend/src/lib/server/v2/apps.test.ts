import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: unknown[]) => apiRequest(...a)
}));

const { previewApp } = await import('#lib/server/v2/apps.js');

const cookies = { get: () => 'token' };

beforeEach(() => {
  apiRequest.mockReset();
});

describe('previewApp objects', () => {
  it('groups objects after wording under Objects with their own effect wording', async () => {
    apiRequest.mockResolvedValue({
      action: 'install',
      items: [
        { type: 'field', target: 'lead', label: 'Budget', effect: 'add' },
        { type: 'object', target: '', label: 'Property', effect: 'add' },
        { type: 'object', target: '', label: 'Requirement', effect: 'conflict' },
        { type: 'object', target: '', label: 'Site visit', effect: 'restore' },
        { type: 'wording', target: 'lead', label: 'Lead', effect: 'add' }
      ]
    });
    const { groups } = await previewApp(cookies, 'real-estate', 'install');
    expect(groups.map((g) => g.label)).toEqual(['Wording', 'Objects', 'Fields']);
    expect(groups[1].items.map((i) => [i.label, i.effectLabel])).toEqual([
      ['Property', 'Will add'],
      ['Requirement', 'Not added'],
      ['Site visit', 'Comes back']
    ]);
  });
});
