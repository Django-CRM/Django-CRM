/**
 * Saved views (G29): a person's named filters for one list, put back with one
 * tap. The API is `/api/saved-views/`; the list pages reach it through the
 * form actions in `$lib/server/v2/saved-views.js`.
 *
 * Client-safe on purpose: `SavedViews.svelte` imports these to build its links.
 *
 * A view stores the list's own API query as `{param: [value, ...]}`, with the
 * API's meaning, so a view saved on the phone and one saved here are the same
 * thing. Each page describes how its URL maps onto that with a `spec`:
 *
 * - `keys`: the URL parameters it forwards to the list under the same name.
 *   Only those are saved from it or put back into its URL.
 * - `multi`: the keys it forwards every value of. Any other key forwards its
 *   first value only, so a view holding more is shown as losing the rest.
 * - `implicit` (tickets only): the page's default for a key, and the URL flag
 *   that turns the default off. With no `status` the queue shows open tickets
 *   and `?all=1` shows all of them, while to the API no `status` is every
 *   status. The helpers translate both ways, so an open-queue view is saved as
 *   its three statuses and a view with no status opens as All.
 * - `keep`: URL parameters that are not filters and survive opening a view,
 *   such as the pipeline's `view=board`.
 *
 * @typedef {{ keys: string[], multi?: string[], implicit?: { key: string, values: string[], allParam: string } | null, keep?: string[] }} Spec
 */

/**
 * The page's filters as a view stores them, read off its query string.
 *
 * @param {URLSearchParams} params
 * @param {Spec} spec
 * @returns {Record<string, string[]>}
 */
export function pickFilters(params, spec) {
  /** @type {Record<string, string[]>} */
  const out = {};
  for (const key of spec.keys) {
    const values = params
      .getAll(key)
      .map((v) => v.trim())
      .filter(Boolean);
    const kept = spec.multi?.includes(key) ? values : values.slice(0, 1);
    if (kept.length > 0) out[key] = kept;
  }
  const implicit = spec.implicit;
  if (implicit && !out[implicit.key] && params.get(implicit.allParam) !== '1') {
    out[implicit.key] = [...implicit.values];
  }
  return out;
}

/**
 * The page URL that shows a view: its path, the view's filters this page can
 * apply, and the current URL's `keep` parameters. Every other filter on the
 * current URL is cleared, so opening a view is a fresh start rather than a
 * merge with the chips already up.
 *
 * @param {URL} url the current page
 * @param {Record<string, string[]>} filters
 * @param {Spec} spec
 */
export function viewHref(url, filters, spec) {
  const query = new URLSearchParams();
  for (const key of spec.keep ?? []) {
    for (const value of url.searchParams.getAll(key)) query.append(key, value);
  }
  for (const key of spec.keys) {
    const values = filters?.[key] ?? [];
    for (const value of spec.multi?.includes(key) ? values : values.slice(0, 1)) {
      query.append(key, value);
    }
  }
  const implicit = spec.implicit;
  if (implicit && !(filters?.[implicit.key]?.length > 0)) query.set(implicit.allParam, '1');
  const qs = query.toString();
  return qs ? `${url.pathname}?${qs}` : url.pathname;
}

/**
 * How many of a view's filter values this page cannot apply: every value of a
 * parameter it has no control for, and every value past the first of one it
 * reads singly.
 *
 * @param {Record<string, string[]>} filters
 * @param {Spec} spec
 */
export function droppedValues(filters, spec) {
  let dropped = 0;
  for (const [key, values] of Object.entries(filters ?? {})) {
    if (!spec.keys.includes(key)) dropped += values.length;
    else if (!spec.multi?.includes(key)) dropped += Math.max(0, values.length - 1);
  }
  return dropped;
}

/**
 * Whether the page is showing exactly this view. Never for a view that loses
 * values here: the list is then showing something else. Order of repeated
 * values does not matter.
 *
 * @param {URLSearchParams} params
 * @param {Record<string, string[]>} filters
 * @param {Spec} spec
 */
export function isShowing(params, filters, spec) {
  if (droppedValues(filters, spec) > 0) return false;
  const current = pickFilters(params, spec);
  const keys = new Set([...Object.keys(current), ...Object.keys(filters ?? {})]);
  return [...keys].every((key) => {
    const a = [...(current[key] ?? [])].sort();
    const b = [...(filters?.[key] ?? [])].sort();
    return a.length === b.length && a.every((v, i) => v === b[i]);
  });
}
