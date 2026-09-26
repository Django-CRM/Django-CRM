/**
 * Whether the signed-in user administers the current org.
 *
 * The one place the web app decides this. It reads `is_organization_admin`, a
 * fact the API derives with `common.permissions.is_org_admin` (the ADMIN role,
 * or a Django superuser's membership) and signs into the access token. It
 * never compares `role`: a superuser who holds the USER role is an admin
 * everywhere on the server, and a `role === 'ADMIN'` test showed them
 * read-only pages the API would have let them change.
 *
 * A display hint only. It hides or shows controls; every write is refused or
 * allowed by the backend, which re-derives the fact on each request.
 *
 * Only a literal `true` counts, so a missing claim (a token minted before the
 * claim existed, which the hourly refresh replaces) reads as "not an admin".
 *
 * @param {Record<string, unknown> | null | undefined} source
 *   JWT claims, `locals.profile`, or layout data: anything carrying the fact.
 * @returns {boolean}
 */
export function isOrgAdmin(source) {
  return source?.is_organization_admin === true;
}
