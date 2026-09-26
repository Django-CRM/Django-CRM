/// Entries from `GET /api/org/audit-log/`, mirroring `/settings/audit-log`
/// on the web.
///
/// The API sends only allow-listed `details` keys (ids, counts, and sentences
/// the server wrote) and never the stored description, so everything shown is
/// built from those.
library;

/// One option in the event filter.
class AuditEventType {
  const AuditEventType({required this.value, required this.label});

  final String value;
  final String label;

  factory AuditEventType.fromJson(Map<String, dynamic> json) => AuditEventType(
    value: json['value']?.toString() ?? '',
    label: json['label']?.toString() ?? '',
  );
}

class AuditEntry {
  const AuditEntry({
    required this.id,
    required this.eventType,
    required this.eventLabel,
    this.createdAt,
    this.success = true,
    this.actorId,
    this.actorName = '',
    this.actorEmail = '',
    this.ipAddress = '',
    this.details = const {},
  });

  final String id;
  final String eventType;
  final String eventLabel;
  final DateTime? createdAt;
  final bool success;

  /// Null for a system event, or once the user has been deleted.
  final String? actorId;
  final String actorName;
  final String actorEmail;
  final String ipAddress;
  final Map<String, dynamic> details;

  String get actorLabel {
    if (actorId == null) return 'No user';
    if (actorName.isNotEmpty) return actorName;
    if (actorEmail.isNotEmpty) return actorEmail;
    return 'No user';
  }

  /// One line on what happened, or '' when the label says it all. The same
  /// rules as `auditDetail` in `frontend/src/lib/v2/audit-log.js`.
  String get detail {
    final reason = details['pause_reason'];
    if (reason != null && reason.toString().isNotEmpty) return '$reason';
    if (eventType == 'RECORD_MERGED' && details['merged_name'] != null) {
      final entity = details['entity']?.toString() ?? '';
      // Duplicates usually share a name, and "X into X" says nothing, so
      // equal names each carry the start of their id.
      final same = details['merged_name'] == details['kept_name'];
      String tag(Object? id) {
        final text = id?.toString() ?? '';
        if (!same || text.isEmpty) return '';
        return ' (${text.length > 8 ? text.substring(0, 8) : text})';
      }

      return 'Merged ${entity.isEmpty ? 'record' : entity} '
          '"${details['merged_name']}"${tag(details['merged_id'])} '
          'into "${details['kept_name']}"${tag(details['kept_id'])}.';
    }
    if (eventType == 'WEBHOOK_REENABLED') {
      return 'Turned back on, and now answers for the webhook.';
    }
    final changed = details['changed'];
    if (eventType == 'WEBHOOK_CHANGED' && changed is List) {
      return 'Changed ${changed.join(', ')}, and now answers for the webhook.';
    }
    final action = details['action'];
    final resource = details['resource'];
    if (action != null && resource != null) return '$action on $resource';
    final deleted = details['deleted_count'];
    if (deleted != null) return '$deleted sample leads removed';
    return '';
  }

  /// The webhook the entry is about, for a link, or null.
  String? get webhookId {
    final id = details['endpoint_id'];
    return id is String && id.isNotEmpty ? id : null;
  }

  factory AuditEntry.fromJson(Map<String, dynamic> json) {
    final actor = json['actor'];
    final details = json['details'];
    return AuditEntry(
      id: json['id']?.toString() ?? '',
      eventType: json['event_type']?.toString() ?? '',
      eventLabel:
          json['event_label']?.toString() ??
          json['event_type']?.toString() ??
          '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
      success: json['success'] != false,
      actorId: actor is Map ? actor['id']?.toString() : null,
      actorName: actor is Map ? actor['name']?.toString() ?? '' : '',
      actorEmail: actor is Map ? actor['email']?.toString() ?? '' : '',
      ipAddress: json['ip_address']?.toString() ?? '',
      details: details is Map<String, dynamic> ? details : const {},
    );
  }
}
