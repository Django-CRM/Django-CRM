import { describe, it, expect } from 'vitest';
import {
  accessSentences,
  appliedTitle,
  keepsKey,
  modeLine,
  noNewKeyLine,
  objectsLine,
  previousScopes,
  removeSentence
} from './private-apps.js';

describe('accessSentences', () => {
  it('is empty for no access', () => expect(accessSentences([])).toEqual([]));

  it('says reads, writes, object reach, events and the key', () => {
    const out = accessSentences(['leads:read', 'leads:write', 'objects:read', 'hooks:write']);
    expect(out).toContain('It may read: Leads, Custom records.');
    expect(out).toContain('It may write: Leads.');
    expect(out.join(' ')).toContain('every custom object');
    expect(out.join(' ')).toContain('receives events');
    expect(out.at(-1)).toContain('never settings');
  });

  it('says the existing key keeps working only when it is kept', () => {
    expect(accessSentences(['leads:read'], true).at(-1)).toContain('existing key keeps working');
    expect(accessSentences(['leads:read'], false).at(-1)).toContain('its own key, shown once');
    expect(accessSentences(['leads:read']).at(-1)).toContain('shown once');
  });
});

describe('modeLine', () => {
  it('names the mode', () => {
    expect(modeLine('upgrade', 3, 2)).toBe('Update from version 2 to version 3');
    expect(modeLine('install_again', 2, null)).toBe('Install again, version 2');
    expect(modeLine('install', 1, null)).toBe('Install, version 1');
  });
});

describe('the key on an update', () => {
  const access = (scopes: string[], added: string[], removed: string[] = []) => ({
    scopes,
    added,
    removed
  });

  it('reads the scopes held before from the preview', () => {
    expect(previousScopes(access(['leads:read', 'tags:read'], ['tags:read']))).toEqual([
      'leads:read'
    ]);
    expect(previousScopes(access(['leads:read'], [], ['tags:read']))).toEqual([
      'leads:read',
      'tags:read'
    ]);
    expect(previousScopes(access(['leads:read'], ['leads:read']))).toEqual([]);
  });

  it('keeps the key on an update of an app that held access', () => {
    expect(keepsKey('upgrade', access(['leads:read', 'tags:read'], ['tags:read']))).toBe(true);
    expect(keepsKey('upgrade', access([], [], ['leads:read']))).toBe(true);
  });

  it('makes a new key when an update adds the first access', () => {
    const first = access(['leads:read'], ['leads:read']);
    expect(keepsKey('upgrade', first)).toBe(false);
    expect(accessSentences(first.scopes, keepsKey('upgrade', first)).at(-1)).toBe(
      'It gets its own key, shown once. The key can write records but never settings.'
    );
  });

  it('never keeps a key on an install', () => {
    for (const mode of ['install', 'install_again']) {
      expect(keepsKey(mode, access(['leads:read'], ['leads:read']))).toBe(false);
    }
  });
});

describe('noNewKeyLine', () => {
  it('says what happened when no key came back', () => {
    expect(noNewKeyLine('upgrade', true, true)).toContain('keeps working');
    expect(noNewKeyLine('upgrade', false, true)).toContain('stopped working');
    expect(noNewKeyLine('install', false, false)).toBe(
      'It asked for no access, so no key was made.'
    );
    expect(noNewKeyLine('install_again', false, false)).toBe(
      'It asked for no access, so no key was made.'
    );
  });

  it('says nothing about a key after an update with no access before or after', () => {
    expect(noNewKeyLine('upgrade', false, false)).toBe('');
  });
});

describe('removeSentence', () => {
  const objects = 'Its objects are archived';
  const key = 'Its key stops working';

  it('names the objects and the key when the app has both', () => {
    const out = removeSentence('Solar', { objects: true, key: true });
    expect(out.startsWith('Remove Solar?')).toBe(true);
    expect(out).toContain(objects);
    expect(out).toContain(key);
  });

  it('leaves out what the app does not have', () => {
    expect(removeSentence('Solar', { objects: true, key: false })).not.toContain(key);
    expect(removeSentence('Solar', { objects: false, key: true })).not.toContain(objects);
    expect(removeSentence('Solar', { objects: false, key: false })).toBe('Remove Solar?');
  });
});

describe('appliedTitle', () => {
  it('reads Updated after an update and Installed otherwise', () => {
    expect(appliedTitle('upgrade')).toBe('Updated');
    expect(appliedTitle('install')).toBe('Installed');
    expect(appliedTitle('install_again')).toBe('Installed');
  });
});

describe('objectsLine', () => {
  it('is left out when the app needs no new place', () => {
    expect(objectsLine({ needed: 0, included: 3, used: 1 })).toBeNull();
  });

  it('agrees with the count', () => {
    expect(objectsLine({ needed: 1, included: 3, used: 1 })).toBe(
      'Needs 1. The plan includes 3, and 1 is used.'
    );
    expect(objectsLine({ needed: 2, included: 3, used: 0 })).toBe(
      'Needs 2. The plan includes 3, and 0 are used.'
    );
  });
});
