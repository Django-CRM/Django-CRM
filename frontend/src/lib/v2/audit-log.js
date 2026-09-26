/**
 * How an entry from `GET /api/org/audit-log/` reads on the page.
 *
 * The API sends only allow-listed `details` keys (ids, counts, and sentences
 * the server wrote), never the stored description, so every string built here
 * comes from those.
 */

/**
 * Who the entry is about: a name, else an email. An entry with no person is a
 * system event, or one whose user has since been deleted.
 * @param {{ actor?: { name?: string, email?: string } | null }} entry
 */
export function auditActor(entry) {
  const a = entry?.actor;
  if (!a) return 'No user';
  return a.name || a.email || 'No user';
}

/**
 * One line on what happened, or '' when the event label says it all.
 * @param {{ event_type?: string, details?: Record<string, any> }} entry
 */
export function auditDetail(entry) {
  const d = entry?.details ?? {};
  if (d.pause_reason) return String(d.pause_reason);
  if (entry?.event_type === 'WEBHOOK_REENABLED') {
    return 'Turned back on, and now answers for the webhook.';
  }
  if (entry?.event_type === 'WEBHOOK_CHANGED' && Array.isArray(d.changed)) {
    return `Changed ${d.changed.join(', ')}, and now answers for the webhook.`;
  }
  if (d.action && d.resource) return `${d.action} on ${d.resource}`;
  if (d.deleted_count !== undefined && d.deleted_count !== null) {
    return `${d.deleted_count} sample leads removed`;
  }
  return '';
}

/**
 * The webhook an entry is about, for a link, or null.
 * @param {{ details?: Record<string, any> }} entry
 * @returns {string | null}
 */
export function auditWebhookId(entry) {
  const id = entry?.details?.endpoint_id;
  return typeof id === 'string' && id ? id : null;
}
