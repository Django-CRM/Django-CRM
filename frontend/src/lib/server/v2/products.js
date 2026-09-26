/**
 * Products: the product catalogue line items are priced from, wired to the
 * real API. First of the invoices sub-pages to move off fixtures.
 *
 * Lives under `$lib/server` so the httpOnly access token stays out of the
 * client bundle. Org is a JWT claim the backend re-derives; it is never a
 * parameter here.
 *
 * WHY THIS MODULE
 * `/invoices/products` sat under a migrated prefix (`/invoices`) but was
 * excluded in `migrated.js`: it listed mock rows and its "New product" button
 * wrote nowhere. `Product` (`invoices/models.py`) has had a full CRUD API
 * (`ProductListView`/`ProductDetailView`) that no v2 page drew, so its write
 * authorisation had only ever been exercised by tests, all of which used an
 * admin client.
 *
 * THE FINDING THE WIRING EXPOSED
 * The catalogue is org-wide config, its list prices flow onto every rep's
 * invoices and estimates, yet `POST`/`PUT`/`DELETE` gated on
 * `(IsAuthenticated, HasOrgContext)` only, so any member (role `USER`) could
 * rewrite a price, retire a product, or hard-delete one for the whole org. The
 * backend fix gates all three writes to an admin (the same posture Organization
 * and Team settings take); reading stays open to every member because a rep
 * needs the catalogue to build line items. This file offers the New/Edit
 * affordances only to an admin, but that is a UX hint. The view is the trust
 * boundary and refuses a non-admin write regardless.
 *
 * WHAT THE MOCK ASSUMED THAT THE API SHAPES DIFFERENTLY
 * - The list endpoint returns a plain `{ count, results }` page with no totals
 *   envelope, so `listProducts` recomputes the header totals here.
 * - `used_on` (how many invoices a product is on) is not a stored column; the
 *   mock hard-coded it. `ProductSerializer` now returns it, annotated across
 *   the list to avoid an N+1, because it is the whole reason a retired product
 *   is still listed rather than deleted.
 */
import { apiRequest } from '$lib/api-helpers.js';

/**
 * The currency codes the backend accepts (`common/utils.CURRENCY_CODES`).
 * A short, fixed list, so the form offers a select rather than a free text box
 * that could 400 on an unknown code. Kept in this order (org default first).
 */
export const CURRENCY_CHOICES = [
  { code: 'USD', label: 'USD, Dollar' },
  { code: 'EUR', label: 'EUR, Euro' },
  { code: 'GBP', label: 'GBP, Pound' },
  { code: 'INR', label: 'INR, Rupee' },
  { code: 'CAD', label: 'CAD, Dollar' },
  { code: 'AUD', label: 'AUD, Dollar' },
  { code: 'JPY', label: 'JPY, Yen' },
  { code: 'CNY', label: 'CNY, Yuan' },
  { code: 'CHF', label: 'CHF, Franc' },
  { code: 'SGD', label: 'SGD, Dollar' },
  { code: 'AED', label: 'AED, Dirham' },
  { code: 'BRL', label: 'BRL, Real' },
  { code: 'MXN', label: 'MXN, Peso' }
];

const CURRENCY_CODES = new Set(CURRENCY_CHOICES.map((c) => c.code));

/**
 * The signed-in role, decoded from the JWT. A display hint only: it decides
 * whether the page offers New/Edit. The backend re-derives the role and is the
 * thing that actually refuses a non-admin write, so it is not trusted here.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @returns {string | null}
 */
function viewerRole(cookies) {
  const token = cookies.get('jwt_access');
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf-8'));
    return claims.role ?? null;
  } catch {
    return null;
  }
}

/**
 * One product as the pages read it, from a `ProductSerializer` row. `category`
 * is coalesced to a heading the grouped list can key on (a real product may
 * carry none); `price` is a number for the money formatter; `used_on` is the
 * count the API now returns.
 *
 * @param {any} p
 */
function toProduct(p) {
  return {
    id: p.id,
    name: p.name ?? '',
    sku: p.sku ?? '',
    description: p.description ?? '',
    price: Number(p.price) || 0,
    currency: p.currency ?? null,
    category: (p.category && String(p.category).trim()) || 'Uncategorised',
    is_active: !!p.is_active,
    used_on: typeof p.used_on === 'number' ? p.used_on : 0
  };
}

/**
 * Header totals over the whole catalogue (not one page). `categories` counts
 * the distinct headings the grouped list will draw.
 *
 * @param {ReturnType<typeof toProduct>[]} items
 */
function computeTotals(items) {
  return {
    count: items.length,
    active: items.filter((p) => p.is_active).length,
    categories: new Set(items.map((p) => p.category)).size
  };
}

/**
 * The catalogue. `limit=1000` fetches the whole (small) set so the totals cover
 * every row. Active first, then by name. The order the settings-style list
 * reads best in. `can_manage` gates the write affordances; the API enforces it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function listProducts({ cookies }) {
  const resp = await apiRequest('/invoices/products/?limit=1000', {}, { cookies });
  const results = (resp?.results ?? [])
    .map(toProduct)
    .sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.name.localeCompare(b.name));

  return {
    results,
    totals: computeTotals(results),
    can_manage: viewerRole(cookies) === 'ADMIN'
  };
}

/**
 * Options for the new-product form. Nothing to fetch, just the currency list
 * and whether this viewer may create. A non-admin is turned away by the page
 * (and by the API) rather than shown a form the POST would refuse.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export function getNewProductOptions({ cookies }) {
  return { can_manage: viewerRole(cookies) === 'ADMIN', currencies: CURRENCY_CHOICES };
}

/**
 * The product plus form options, for the edit page. Refuses a non-admin up
 * front (`can_edit: false`) so the form is never drawn for someone the PUT
 * would refuse. The detail fetch is org-scoped, so a bad or foreign id 404s.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getProductForEdit({ cookies }, id) {
  if (viewerRole(cookies) !== 'ADMIN') return { can_edit: false };
  const raw = await apiRequest(`/invoices/products/${id}/`, {}, { cookies });
  const p = toProduct(raw);
  return {
    can_edit: true,
    currencies: CURRENCY_CHOICES,
    product: {
      id: p.id,
      name: p.name,
      sku: p.sku,
      description: p.description,
      price: p.price,
      currency: p.currency,
      category: p.category === 'Uncategorised' ? '' : p.category,
      is_active: p.is_active,
      used_on: p.used_on
    }
  };
}

/**
 * The write payload the serializer accepts, from raw form strings. `org` and
 * `currency`-defaulting are server-side. An empty SKU is sent as `null`, never
 * `""`: `sku` is `unique_together` with `org`, and two blank strings would
 * collide where two nulls do not. An unknown currency is dropped so the backend
 * can fall back to the org default instead of 400-ing.
 *
 * @param {{ name: string, sku?: string, description?: string, price: string,
 *   currency?: string, category?: string, is_active: boolean }} values
 */
function toPayload(values) {
  /** @type {Record<string, any>} */
  const body = {
    name: values.name,
    price: values.price,
    is_active: values.is_active,
    sku: values.sku ? values.sku : null,
    description: values.description ?? '',
    category: values.category ?? ''
  };
  if (values.currency && CURRENCY_CODES.has(values.currency)) body.currency = values.currency;
  return body;
}

/**
 * `POST /api/invoices/products/`. Admin-only server-side.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Parameters<typeof toPayload>[0]} values
 */
export function createProduct({ cookies }, values) {
  return apiRequest(
    '/invoices/products/',
    { method: 'POST', body: toPayload(values) },
    { cookies }
  );
}

/**
 * `PUT /api/invoices/products/<id>/` (the view treats it as partial). Admin-only.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Parameters<typeof toPayload>[0]} values
 */
export function updateProduct({ cookies }, id, values) {
  return apiRequest(
    `/invoices/products/${id}/`,
    { method: 'PUT', body: toPayload(values) },
    { cookies }
  );
}

/**
 * `DELETE /api/invoices/products/<id>/`. Admin-only. Prefer retiring
 * (`is_active=false`) over deleting for a product with history; both are safe,
 * since line items keep their own name and price.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export function deleteProduct({ cookies }, id) {
  return apiRequest(`/invoices/products/${id}/`, { method: 'DELETE' }, { cookies });
}
