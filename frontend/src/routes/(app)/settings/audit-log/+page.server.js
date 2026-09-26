import { auditFilters, listAuditLog } from '$lib/server/v2/audit-log.js';

/**
 * The security audit log, newest first. Read-only, admin-only server-side
 * (`common/views/audit_log_views.py`), so a member gets `forbidden` and an
 * explanation. Filters and the page offset live in the URL, so a filtered view
 * can be shared and the back button works.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const params = event.url.searchParams;
  const offset = Math.max(0, Number.parseInt(params.get('offset') ?? '0', 10) || 0);
  return await listAuditLog(event, auditFilters(params), offset);
}
