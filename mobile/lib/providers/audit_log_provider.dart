import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/audit_entry.dart';
import '../services/api_service.dart';

/// The security audit log, mirroring `/settings/audit-log` on the web.
///
/// Admin-only server-side (`common/views/audit_log_views.py`). The screen
/// gates on `isOrgAdminProvider` so a member never asks; that is a courtesy,
/// not the boundary. The server validates every filter and answers 400 on a
/// malformed one, whose sentence the screen shows.

/// Entries per page, the same as the web.
const int auditLogPageSize = 25;

/// What the screen is asking for. A record, so two equal queries share one
/// cached page.
typedef AuditLogQuery = ({
  String? eventType,
  String? actor,
  DateTime? from,
  DateTime? to,
  int offset,
});

const AuditLogQuery auditLogFirstPage = (
  eventType: null,
  actor: null,
  from: null,
  to: null,
  offset: 0,
);

String _day(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

/// The query parameters for [q]. Only set filters are sent.
Map<String, String> auditLogParams(AuditLogQuery q) => {
  'limit': '$auditLogPageSize',
  'offset': '${q.offset}',
  if (q.eventType != null && q.eventType!.isNotEmpty)
    'event_type': q.eventType!,
  if (q.actor != null && q.actor!.isNotEmpty) 'actor': q.actor!,
  if (q.from != null) 'from': _day(q.from!),
  if (q.to != null) 'to': _day(q.to!),
};

class AuditLogPage {
  const AuditLogPage({
    this.entries = const [],
    this.count = 0,
    this.eventTypes = const [],
  });

  final List<AuditEntry> entries;
  final int count;
  final List<AuditEventType> eventTypes;
}

final auditLogProvider = FutureProvider.autoDispose
    .family<AuditLogPage, AuditLogQuery>((ref, q) async {
      final response = await ApiService().get(
        ApiConfig.auditLog,
        queryParams: auditLogParams(q),
      );
      if (!response.success || response.data == null) {
        throw Exception(response.message ?? 'Could not load the audit log');
      }
      final body = response.data!;
      final count = body['count'];
      final entries = listFromEnvelope(body, const [
        'results',
      ]).map(AuditEntry.fromJson).toList(growable: false);
      return AuditLogPage(
        entries: entries,
        count: count is int ? count : entries.length,
        eventTypes: listFromEnvelope(body, const [
          'event_types',
        ]).map(AuditEventType.fromJson).toList(growable: false),
      );
    });
