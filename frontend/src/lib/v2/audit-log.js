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
 * @param {{ event_type?: string, actor?: { id?: string } | null, details?: Record<string, any> }} entry
 */
export function auditDetail(entry) {
  const d = entry?.details ?? {};
  if (d.pause_reason) return String(d.pause_reason);
  if (entry?.event_type === 'RECORD_MERGED' && d.merged_name !== undefined) {
    // Duplicates usually share a name, and "X into X" says nothing, so equal
    // names each carry the start of their id.
    const same = d.merged_name === d.kept_name;
    const tag = (/** @type {any} */ id) => (same && id ? ` (${String(id).slice(0, 8)})` : '');
    return `Merged ${d.entity || 'record'} "${d.merged_name}"${tag(d.merged_id)} into "${d.kept_name}"${tag(d.kept_id)}.`;
  }
  if (
    (entry?.event_type === 'API_TOKEN_CREATED' || entry?.event_type === 'API_TOKEN_REVOKED') &&
    d.token_prefix
  ) {
    // An empty scope list is a token with the owner's full access. The owner
    // is named only when someone else acted: an admin revoking their token.
    const scopes =
      Array.isArray(d.scopes) && d.scopes.length ? `scopes ${d.scopes.join(', ')}` : 'full access';
    const owner = d.owner_id && d.owner_id !== entry?.actor?.id ? `, owned by ${d.owner_name}` : '';
    return `Token "${d.token_name}" (${d.token_prefix}), ${scopes}${owner}.`;
  }
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
