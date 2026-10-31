/** Private apps on screen, browser-safe: the fixed copy and plain-word access. */
import { scopeLabel } from '#lib/v2/connected-apps.js';

export const NOT_REVIEWED =
  'BottleCRM has not reviewed this app. Install it only if you trust whoever sent you the file.';
export const TRUST_LABEL = 'I trust whoever wrote this app';

/** The consent screen's access block, as the preview sends it. */
export type Access = { scopes: string[]; added: string[]; removed: string[] };

/** The scopes the app held before: the new list, less what it adds, plus what it drops. */
export const previousScopes = (access: Access): string[] => [
  ...access.scopes.filter((s) => !access.added.includes(s)),
  ...access.removed
];

/** Whether applying keeps the app's key: an update of an app that already held access. */
export const keepsKey = (mode: string, access: Access): boolean =>
  mode === 'upgrade' && previousScopes(access).length > 0;

/** The removal confirmation, naming only what the app has. */
export function removeSentence(name: string, has: { objects: boolean; key: boolean }): string {
  const out = [`Remove ${name}?`];
  if (has.objects) {
    out.push(
      "Its objects are archived with every record kept, and they still count toward the plan's custom objects until you delete them."
    );
  }
  if (has.key) out.push('Its key stops working and its subscriptions are deleted.');
  return out.join(' ');
}

const names = (scopes: string[], action: string) =>
  scopes
    .filter((s) => s.endsWith(`:${action}`))
    .map((s) => scopeLabel(s).replace(/: [a-z]+$/, ''))
    .join(', ');

/**
 * What a set of approved scopes means, one sentence each. Empty for no access.
 * `keeps` is true when the app's existing key carries the new scopes.
 */
export function accessSentences(scopes: string[], keeps = false): string[] {
  if (!scopes.length) return [];
  const out: string[] = [];
  const reads = names(scopes, 'read');
  const writes = names(scopes, 'write')
    .split(', ')
    .filter((n) => n && n !== 'Subscriptions');
  if (reads) out.push(`It may read: ${reads}.`);
  if (writes.length) out.push(`It may write: ${writes.join(', ')}.`);
  if (scopes.some((s) => s.startsWith('objects:'))) {
    out.push('Object access covers every custom object in this organization, not only its own.');
  }
  if (scopes.includes('hooks:write')) {
    out.push('It receives events about the records it may read.');
  }
  out.push(
    keeps
      ? 'Its existing key keeps working, with the access listed here. The key can write records but never settings.'
      : 'It gets its own key, shown once. The key can write records but never settings.'
  );
  return out;
}

/** The custom objects line, or null when the app needs no new place. */
export function objectsLine(objects: { needed: number; included: number; used: number }) {
  if (!objects.needed) return null;
  const verb = objects.used === 1 ? 'is' : 'are';
  return `Needs ${objects.needed}. The plan includes ${objects.included}, and ${objects.used} ${verb} used.`;
}

/** The consent screen's first line: install, update or install again. */
export function modeLine(mode: string, version: number, installed: number | null): string {
  if (mode === 'upgrade') {
    return installed
      ? `Update from version ${installed} to version ${version}`
      : `Update to version ${version}`;
  }
  if (mode === 'install_again') return `Install again, version ${version}`;
  return `Install, version ${version}`;
}

/** The page title once the app is applied. */
export const appliedTitle = (mode: string) => (mode === 'upgrade' ? 'Updated' : 'Installed');

/**
 * What happened to the key when an apply returned none; empty when there is
 * nothing to say, as after an update of an app that never held access.
 */
export function noNewKeyLine(mode: string, asksAccess: boolean, hadAccess: boolean): string {
  if (mode !== 'upgrade') return 'It asked for no access, so no key was made.';
  if (!hadAccess) return '';
  return asksAccess
    ? 'Its existing key keeps working, with the access shown on the review screen.'
    : 'It no longer has access, so its key has stopped working.';
}
