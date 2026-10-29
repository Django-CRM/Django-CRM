import { describe, it, expect } from 'vitest';
import {
  disconnectSentence,
  KEY_NOTICE,
  kindLabel,
  lastUsedLabel,
  replaceSentence,
  scopeLabel
} from './connected-apps.js';

describe('scopeLabel', () => {
  it('reads a scope as a resource and an action', () => {
    expect(scopeLabel('leads:read')).toBe('Leads: read');
    expect(scopeLabel('opportunities:write')).toBe('Deals: write');
    expect(scopeLabel('cases:read')).toBe('Tickets: read');
    expect(scopeLabel('objects:write')).toBe('Custom records: write');
    expect(scopeLabel('hooks')).toBe('Subscriptions');
  });

  it('makes a readable label for a resource it has no word for', () => {
    expect(scopeLabel('time_entries:read')).toBe('Time entries: read');
  });
});

describe('kindLabel', () => {
  it('shows the name the API gave, or Another app when it gave none', () => {
    expect(kindLabel({ kind: 'example', name: 'Example app' })).toBe('Example app');
    expect(kindLabel({ kind: 'other', name: null })).toBe('Another app');
  });
});

describe('the page copy', () => {
  it('says each sentence as the brief words it', () => {
    expect(KEY_NOTICE).toBe('Copy this key now. It will not be shown again.');
    expect(disconnectSentence('Example app')).toBe(
      'Disconnect Example app? Its key stops working and its subscriptions are deleted. Records it created stay.'
    );
    expect(replaceSentence('Example app')).toBe(
      'Replace the key for Example app? The old key stops working at once.'
    );
  });

  it('says Never for an app that has not been used', () => {
    expect(lastUsedLabel(null, () => 'x')).toBe('Never');
    expect(lastUsedLabel('2026-10-01T00:00:00Z', () => '3 days ago')).toBe('3 days ago');
  });
});

describe('authorText', () => {
  it('shows a connected app by its name and a person by email as before', async () => {
    const { authorText } = await import('./author.js');
    expect(authorText({ name: 'Example app', email: 'app@example.invalid' })).toBe('Example app');
    expect(authorText({ name: null, email: 'ada@example.com' })).toBe('ada@example.com');
    expect(authorText(null)).toBe('');
  });
});
