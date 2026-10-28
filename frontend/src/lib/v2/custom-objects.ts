/**
 * Browser-safe helpers for custom object pages. Imports nothing from `$app`
 * or `#lib/server`, so Svelte pages and vitest can both use it.
 */

import type { FieldRef, ObjectRef, RelatedRecords } from '#lib/server/v2/custom-objects.js';

export const PHOTO_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'gif'];

export function isPhoto(attachment: { file_name?: string; is_image?: boolean }): boolean {
  if (attachment.is_image !== true) return false;
  const ext = (attachment.file_name ?? '').split('.').pop()?.toLowerCase() ?? '';
  return PHOTO_EXTENSIONS.includes(ext);
}

/** The server limit is 6 MiB for the whole body; the multipart envelope takes a little. */
export const WEB_UPLOAD_MAX_BYTES = 6 * 1024 * 1024 - 64 * 1024;

export function uploadTooLarge(file: { name: string; size: number }): string | null {
  if (file.size <= WEB_UPLOAD_MAX_BYTES) return null;
  return `${file.name} is ${(file.size / 1_000_000).toFixed(1)} MB. Files must be 6 MB or smaller here.`;
}

const BUILT_IN_PATHS = {
  lead: '/leads',
  contact: '/contacts',
  account: '/accounts',
  deal: '/pipeline',
  ticket: '/tickets',
  task: '/tasks'
};

/** How many objects an org may have when the API names no limit of its own. */
export const DEFAULT_OBJECT_LIMIT = 20;

/**
 * The most objects the org may have, from the `/objects/` answer's `limit`.
 * An older or community API sends none, so today's number stands.
 */
export function objectLimit(answer: { limit?: unknown } | null | undefined): number {
  const limit = answer?.limit;
  return Number.isInteger(limit) && (limit as number) >= 0
    ? (limit as number)
    : DEFAULT_OBJECT_LIMIT;
}

export const OBJECT_KEY = /^[a-z][a-z0-9_]{1,27}$/;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param target Registry key: `lead`, `deal`, `obj:<key>`, ... */
export function recordHref(target: string, id: string | null | undefined): string | null {
  if (typeof target !== 'string' || typeof id !== 'string' || !UUID.test(id)) return null;
  if (target.startsWith('obj:')) {
    const key = target.slice(4);
    return OBJECT_KEY.test(key) ? `/objects/${key}/${id}` : null;
  }
  return Object.hasOwn(BUILT_IN_PATHS, target)
    ? `${(BUILT_IN_PATHS as Record<string, string>)[target]}/${id}`
    : null;
}

/** @param answer The `/objects/` answer, or null. */
export function objectNavItems(
  answer: { objects: { key: string; label_plural: string }[] } | null
): { href: string; label: string }[] {
  if (!answer) return [];
  return answer.objects
    .filter((o) => typeof o.key === 'string' && OBJECT_KEY.test(o.key))
    .map((o) => ({ href: `/objects/${o.key}`, label: o.label_plural }));
}

export function showObjectsSetting(
  answer: { objects: unknown[] } | null,
  isAdmin: boolean
): boolean {
  return answer !== null && (isAdmin || answer.objects.length > 0);
}

/** Shape a Related answer for the section on a built-in record's page. */
export function relatedView(related: RelatedRecords | null): {
  groups: Array<RelatedRecords['groups'][number] & { removable: boolean }>;
  addTargets: { object: ObjectRef; field: FieldRef }[];
} | null {
  if (!related) return null;
  const addTargets = related.link_fields ?? [];
  const groups = (related.groups ?? []).map((g) => ({
    object: g.object,
    field: g.field,
    records: g.records,
    removable: addTargets.some((t) => t.object.key === g.object.key && t.field.key === g.field.key)
  }));
  if (groups.length === 0 && addTargets.length === 0) return null;
  return { groups, addTargets: addTargets.map((t) => ({ object: t.object, field: t.field })) };
}

export function suggestKey(label: string, max: number): string {
  return label
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .slice(0, max)
    .replace(/_+$/, '');
}

/**
 * Whether the viewer's org may write records of this object. The API's own
 * `writable` decides; an older API sends none, so the org-wide flag stands.
 */
export function canWriteObject(
  definition: { writable?: boolean } | null | undefined,
  orgFlag: boolean | undefined
): boolean {
  return typeof definition?.writable === 'boolean' ? definition.writable : orgFlag === true;
}
