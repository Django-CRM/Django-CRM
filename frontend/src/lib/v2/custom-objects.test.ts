import { describe, it, expect } from 'vitest';
import {
  isPhoto,
  uploadTooLarge,
  recordHref,
  objectNavItems,
  showObjectsSetting,
  relatedView,
  suggestKey,
  objectLimit,
  canWriteObject
} from './custom-objects.js';

describe('isPhoto', () => {
  it.each([
    ['house.JPG', true, true],
    ['plan.svg', true, false],
    ['plan.svg', false, false],
    ['notes.pdf', false, false],
    ['photo.webp', false, false]
  ])('%s with is_image %s is a photo: %s', (file_name, is_image, expected) =>
    expect(isPhoto({ file_name, is_image })).toBe(expected)
  );
});

describe('uploadTooLarge', () => {
  it('refuses a file over 6 MiB', () =>
    expect(uploadTooLarge({ name: 'walkthrough.mov', size: 7 * 1024 * 1024 })).toBe(
      'walkthrough.mov is 7.3 MB. Files must be 6 MB or smaller here.'
    ));
  it('refuses a file of exactly 6 MiB, which the multipart body would push over', () =>
    expect(uploadTooLarge({ name: 'a.pdf', size: 6 * 1024 * 1024 })).toBe(
      'a.pdf is 6.3 MB. Files must be 6 MB or smaller here.'
    ));
  it('accepts a file 64 KiB under 6 MiB and refuses one byte more', () => {
    const limit = 6 * 1024 * 1024 - 64 * 1024;
    expect(uploadTooLarge({ name: 'a.pdf', size: limit })).toBeNull();
    expect(uploadTooLarge({ name: 'a.pdf', size: limit + 1 })).not.toBeNull();
  });
});

const X = '11111111-1111-4111-8111-111111111111';

describe('recordHref', () => {
  it.each([
    ['lead', '/leads/' + X],
    ['contact', '/contacts/' + X],
    ['account', '/accounts/' + X],
    ['deal', '/pipeline/' + X],
    ['ticket', '/tickets/' + X],
    ['task', '/tasks/' + X],
    ['obj:property', '/objects/property/' + X],
    ['invoice', null]
  ])('links %s to %s', (target, expected) => expect(recordHref(target, X)).toBe(expected));

  it.each(['constructor', 'toString', '__proto__', 'obj:../x', 'obj:', 'obj:a/b'])(
    'returns null for %s',
    (target) => expect(recordHref(target, X)).toBeNull()
  );
  it('returns null for an id that is not a UUID', () => {
    expect(recordHref('lead', 'X')).toBeNull();
    expect(recordHref('obj:property', '../x')).toBeNull();
  });
});

describe('navigation', () => {
  const answer = {
    has_custom_objects: true,
    objects: [{ key: 'property', label: 'Property', label_plural: 'Properties' }],
    link_targets: []
  };
  const empty = { ...answer, objects: [] };

  it('lists nothing when the server does not offer custom objects', () => {
    expect(objectNavItems(null)).toEqual([]);
    expect(showObjectsSetting(null, true)).toBe(false);
  });

  it('drops an object whose key is not a valid key', () => {
    const bad = { ...answer, objects: [{ key: '../x', label_plural: 'Bad' }, ...answer.objects] };
    expect(objectNavItems(bad)).toEqual([{ href: '/objects/property', label: 'Properties' }]);
  });

  it('lists one item per object by plural label', () => {
    expect(objectNavItems(answer)).toEqual([{ href: '/objects/property', label: 'Properties' }]);
  });

  it('shows the setting to admins with no objects, and to members only once one exists', () => {
    expect(showObjectsSetting(empty, true)).toBe(true);
    expect(showObjectsSetting(empty, false)).toBe(false);
    expect(showObjectsSetting(answer, false)).toBe(true);
  });
});

describe('relatedView', () => {
  const group = {
    object: { key: 'property', label: 'Property', label_plural: 'Properties' },
    field: { key: 'enquiries', label: 'Enquiries' },
    records: [{ id: 'A', name: 'Flat 1' }]
  };
  const other = {
    object: { key: 'viewing', label: 'Viewing', label_plural: 'Viewings' },
    field: { key: 'lead', label: 'Lead' },
    records: []
  };

  it('offers removal only on fields the answer lets this person edit', () => {
    const view = relatedView({
      groups: [group, other],
      can_edit: true,
      link_fields: [{ object: group.object, field: group.field }]
    });
    expect(view?.groups.map((g) => g.removable)).toEqual([true, false]);
    expect(view?.addTargets).toEqual([{ object: group.object, field: group.field }]);
  });

  it('offers no removal when link_fields is empty', () => {
    const view = relatedView({ groups: [group], can_edit: false, link_fields: [] });
    expect(view?.groups[0].removable).toBe(false);
  });

  it('hides a Related section with nothing to show or offer', () => {
    expect(relatedView(null)).toBeNull();
    expect(relatedView({ groups: [], can_edit: false, link_fields: [] })).toBeNull();
  });

  it('shows the section when only adding is possible', () => {
    const view = relatedView({
      groups: [],
      can_edit: true,
      link_fields: [{ object: group.object, field: group.field }]
    });
    expect(view?.groups).toEqual([]);
    expect(view?.addTargets).toHaveLength(1);
  });
});

describe('suggestKey', () => {
  it.each([
    ['Property', 28, 'property'],
    ['Site visits', 64, 'site_visits'],
    ['3D Prints!', 28, 'd_prints'],
    ['Ünits', 28, 'units'],
    ['x'.repeat(40), 28, 'x'.repeat(28)]
  ])('suggests a key for %s', (label, max, expected) =>
    expect(suggestKey(label, max)).toBe(expected)
  );
  it('drops a trailing underscore left by the cut', () => {
    expect(suggestKey('ab cd', 3)).toBe('ab');
  });
});

describe('objectLimit', () => {
  it.each([
    [{ limit: 3 }, 3],
    [{ limit: 0 }, 0],
    [{ limit: 20 }, 20]
  ])('reads %j', (answer, expected) => expect(objectLimit(answer)).toBe(expected));
  it.each([[{}], [null], [undefined], [{ limit: '3' }], [{ limit: -1 }], [{ limit: 2.5 }]])(
    "keeps today's 20 for %j",
    (answer) => expect(objectLimit(answer)).toBe(20)
  );
});

describe('canWriteObject', () => {
  it('follows the object, not the org, when the API says', () => {
    expect(canWriteObject({ writable: true }, false)).toBe(true);
    expect(canWriteObject({ writable: false }, true)).toBe(false);
  });

  it('falls back to the org flag for an API that sends no writable', () => {
    expect(canWriteObject({}, true)).toBe(true);
    expect(canWriteObject({}, undefined)).toBe(false);
  });
});
