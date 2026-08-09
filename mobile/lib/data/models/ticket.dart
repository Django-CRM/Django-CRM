import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/app_colors.dart';
import 'comment.dart';

/// Ticket status enumeration matching backend STATUS_CHOICE
enum TicketStatus {
  newStatus('New', 'New', AppColors.primary500),
  assigned('Assigned', 'Assigned', AppColors.primary400),
  pending('Pending', 'Pending', AppColors.warning500),
  closed('Closed', 'Closed', AppColors.success600),
  rejected('Rejected', 'Rejected', AppColors.danger500),
  duplicate('Duplicate', 'Duplicate', AppColors.gray500);

  final String value;
  final String label;
  final Color color;

  const TicketStatus(this.value, this.label, this.color);

  String get displayName => label;

  static TicketStatus fromString(String? value) {
    if (value == null) return TicketStatus.newStatus;
    return TicketStatus.values.firstWhere(
      (s) => s.value == value,
      orElse: () => TicketStatus.newStatus,
    );
  }
}

/// Ticket priority enumeration matching backend PRIORITY_CHOICE
enum TicketPriority {
  low('Low', 'Low', AppColors.success500),
  normal('Normal', 'Normal', AppColors.primary400),
  high('High', 'High', AppColors.warning500),
  urgent('Urgent', 'Urgent', AppColors.danger500);

  final String value;
  final String label;
  final Color color;

  const TicketPriority(this.value, this.label, this.color);

  String get displayName => label;

  static TicketPriority fromString(String? value) {
    if (value == null) return TicketPriority.normal;
    return TicketPriority.values.firstWhere(
      (p) => p.value == value,
      orElse: () => TicketPriority.normal,
    );
  }
}

/// Ticket type enumeration matching backend CASE_TYPE
enum TicketType {
  question('Question', 'Question', LucideIcons.circleHelp),
  incident('Incident', 'Incident', LucideIcons.circleAlert),
  problem('Problem', 'Problem', LucideIcons.triangleAlert);

  final String value;
  final String label;
  final IconData icon;

  const TicketType(this.value, this.label, this.icon);

  String get displayName => label;

  static TicketType fromString(String? value) {
    if (value == null) return TicketType.question;
    return TicketType.values.firstWhere(
      (t) => t.value == value,
      orElse: () => TicketType.question,
    );
  }
}

/// Ticket model for BottleCRM
/// Statuses where somebody still owes the customer something.
///
/// Mirrors `cases.views.OPEN_STATUSES` and the web's `OPEN_STATUSES` in
/// `lib/server/v2/tickets.js`. Three lists, one definition, so a rail that
/// says "also open" means the same thing everywhere.
const List<TicketStatus> openTicketStatuses = [
  TicketStatus.newStatus,
  TicketStatus.assigned,
  TicketStatus.pending,
];

class Ticket {
  final String id;
  final String name;
  final TicketStatus status;
  final TicketPriority priority;
  final TicketType ticketType;
  final String? description;
  final String? accountId;
  final String? accountName;
  final List<Map<String, dynamic>> assignedTo;
  final List<String> assignedToIds;
  final List<String> tags;
  final DateTime createdAt;
  final DateTime? closedOn;
  final int? slaFirstResponseHours;
  final int? slaResolutionHours;
  final DateTime? firstResponseAt;
  final DateTime? resolvedAt;
  final bool isFirstResponseSlaBreachedFromApi;
  final bool isResolutionSlaBreachedFromApi;
  final List<Comment> comments;
  final List<String> tagIds;

  /// The people on this ticket, from the `contacts` M2M.
  ///
  /// Read AND sent. `CaseDetailView.put` clears `contacts` unconditionally and
  /// re-adds whatever the body carries, so a client that edits with PUT and
  /// omits this field unlinks every contact on the ticket. This app now edits
  /// with PATCH, which only touches the key when it is present, and sends the
  /// list either way.
  final List<String> contactIds;

  /// Display names for the same rows, so the detail screen can name them
  /// without a second request.
  final List<String> contactNames;
  final Map<String, dynamic> customFields;
  final DateTime? firstResponseSlaDeadline;
  final DateTime? resolutionSlaDeadline;
  final DateTime? slaPausedAt;
  final int escalationCount;
  final DateTime? lastEscalationFiredAt;
  final TicketParentSummary? parentSummary;
  final int childCount;
  final bool isProblem;

  const Ticket({
    required this.id,
    required this.name,
    required this.status,
    required this.priority,
    required this.ticketType,
    this.description,
    this.accountId,
    this.accountName,
    this.assignedTo = const [],
    this.assignedToIds = const [],
    this.tags = const [],
    this.tagIds = const [],
    this.contactIds = const [],
    this.contactNames = const [],
    this.customFields = const {},
    this.firstResponseSlaDeadline,
    this.resolutionSlaDeadline,
    this.slaPausedAt,
    this.escalationCount = 0,
    this.lastEscalationFiredAt,
    this.parentSummary,
    this.childCount = 0,
    this.isProblem = false,
    required this.createdAt,
    this.closedOn,
    this.slaFirstResponseHours,
    this.slaResolutionHours,
    this.firstResponseAt,
    this.resolvedAt,
    this.isFirstResponseSlaBreachedFromApi = false,
    this.isResolutionSlaBreachedFromApi = false,
    this.comments = const [],
  });

  /// First-response SLA breach.
  ///
  /// Mirrors `Case.is_sla_first_response_breached` on the backend:
  /// breached when no `first_response_at` AND `now > created_at + sla_first_response_hours`.
  /// Both web and mobile compute from the same `created_at + sla_first_response_hours`
  /// so the badge stays consistent across clients.
  bool get isFirstResponseSlaBreached {
    if (isFirstResponseSlaBreachedFromApi) return true;
    if (firstResponseAt != null) return false;
    if (slaFirstResponseHours == null) return false;
    final deadline = createdAt.add(Duration(hours: slaFirstResponseHours!));
    return DateTime.now().isAfter(deadline);
  }

  /// Resolution SLA breach, same wall-clock approximation as
  /// [isFirstResponseSlaBreached] (no business-hours adjustment) so the chip
  /// matches what the user reads on the card.
  bool get isResolutionSlaBreached {
    if (isResolutionSlaBreachedFromApi) return true;
    if (resolvedAt != null) return false;
    if (slaResolutionHours == null) return false;
    final deadline = createdAt.add(Duration(hours: slaResolutionHours!));
    return DateTime.now().isAfter(deadline);
  }

  /// Assigned-to display name (first assignee or "Unassigned").
  String get assignedToName {
    if (assignedTo.isEmpty) return 'Unassigned';
    final first = assignedTo.first;
    final email =
        (first['user_details']?['email'] as String?) ??
        (first['email'] as String?) ??
        '';
    if (email.isEmpty) return 'Unassigned';
    return email.split('@').first;
  }

  factory Ticket.fromJson(Map<String, dynamic> json) {
    String? accountId;
    String? accountName;
    final accountJson = json['account'];
    if (accountJson is Map<String, dynamic>) {
      accountId = accountJson['id']?.toString();
      accountName = accountJson['name'] as String?;
    } else if (accountJson != null) {
      accountId = accountJson.toString();
    }

    List<Map<String, dynamic>> parsedAssignedTo = [];
    List<String> parsedAssignedToIds = [];
    if (json['assigned_to'] is List) {
      for (final a in (json['assigned_to'] as List)) {
        if (a is Map<String, dynamic>) {
          parsedAssignedTo.add(a);
          final id = a['id']?.toString() ?? '';
          if (id.isNotEmpty) parsedAssignedToIds.add(id);
        }
      }
    }

    final List<String> parsedTags = [];
    final List<String> parsedTagIds = [];
    if (json['tags'] is List) {
      for (final t in (json['tags'] as List)) {
        if (t is Map<String, dynamic>) {
          final n = t['name'] as String?;
          if (n != null && n.isNotEmpty) parsedTags.add(n);
          final tid = t['id']?.toString();
          if (tid != null && tid.isNotEmpty) parsedTagIds.add(tid);
        } else if (t is String) {
          parsedTags.add(t);
        }
      }
    }

    final List<String> parsedContactIds = [];
    final List<String> parsedContactNames = [];
    if (json['contacts'] is List) {
      for (final c in (json['contacts'] as List)) {
        if (c is Map<String, dynamic>) {
          final cid = c['id']?.toString();
          if (cid != null && cid.isNotEmpty) parsedContactIds.add(cid);
          final name = [c['first_name'], c['last_name']]
              .whereType<String>()
              .where((part) => part.trim().isNotEmpty)
              .join(' ');
          parsedContactNames.add(
            name.trim().isNotEmpty
                ? name.trim()
                : (c['email'] as String? ?? 'Contact'),
          );
        }
      }
    }

    final Map<String, dynamic> parsedCustomFields = {};
    final cfRaw = json['custom_fields'];
    if (cfRaw is Map) {
      cfRaw.forEach((k, v) {
        if (k is String) parsedCustomFields[k] = v;
      });
    }

    return Ticket(
      id: json['id']?.toString() ?? '',
      name: json['name'] as String? ?? '',
      status: TicketStatus.fromString(json['status'] as String?),
      priority: TicketPriority.fromString(json['priority'] as String?),
      ticketType: TicketType.fromString(json['case_type'] as String?),
      description: json['description'] as String?,
      accountId: accountId,
      accountName: accountName,
      assignedTo: parsedAssignedTo,
      assignedToIds: parsedAssignedToIds,
      tags: parsedTags,
      tagIds: parsedTagIds,
      contactIds: parsedContactIds,
      contactNames: parsedContactNames,
      customFields: parsedCustomFields,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      closedOn: json['closed_on'] != null
          ? DateTime.tryParse(json['closed_on'] as String)
          : null,
      slaFirstResponseHours: json['sla_first_response_hours'] as int?,
      slaResolutionHours: json['sla_resolution_hours'] as int?,
      firstResponseAt: json['first_response_at'] != null
          ? DateTime.tryParse(json['first_response_at'] as String)
          : null,
      resolvedAt: json['resolved_at'] != null
          ? DateTime.tryParse(json['resolved_at'] as String)
          : null,
      isFirstResponseSlaBreachedFromApi:
          json['is_sla_first_response_breached'] as bool? ?? false,
      isResolutionSlaBreachedFromApi:
          json['is_sla_resolution_breached'] as bool? ?? false,
      firstResponseSlaDeadline: json['first_response_sla_deadline'] != null
          ? DateTime.tryParse(json['first_response_sla_deadline'] as String)
          : null,
      resolutionSlaDeadline: json['resolution_sla_deadline'] != null
          ? DateTime.tryParse(json['resolution_sla_deadline'] as String)
          : null,
      slaPausedAt: json['sla_paused_at'] != null
          ? DateTime.tryParse(json['sla_paused_at'] as String)
          : null,
      escalationCount: json['escalation_count'] as int? ?? 0,
      lastEscalationFiredAt: json['last_escalation_fired_at'] != null
          ? DateTime.tryParse(json['last_escalation_fired_at'] as String)
          : null,
      parentSummary: json['parent_summary'] is Map<String, dynamic>
          ? TicketParentSummary.fromJson(
              json['parent_summary'] as Map<String, dynamic>,
            )
          : null,
      childCount: json['child_count'] as int? ?? 0,
      isProblem: json['is_problem'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'status': status.value,
      'priority': priority.value,
      'case_type': ticketType.value,
      'description': description,
      'account': accountId,
      'assigned_to': assignedToIds,
      'tags': tagIds,
      'contacts': contactIds,
      'custom_fields': customFields,
      if (closedOn != null)
        'closed_on':
            '${closedOn!.year.toString().padLeft(4, '0')}-'
            '${closedOn!.month.toString().padLeft(2, '0')}-'
            '${closedOn!.day.toString().padLeft(2, '0')}',
    };
  }

  Ticket copyWith({
    String? id,
    String? name,
    TicketStatus? status,
    TicketPriority? priority,
    TicketType? ticketType,
    String? description,
    String? accountId,
    String? accountName,
    List<Map<String, dynamic>>? assignedTo,
    List<String>? assignedToIds,
    List<String>? tags,
    List<String>? tagIds,
    List<String>? contactIds,
    List<String>? contactNames,
    Map<String, dynamic>? customFields,
    DateTime? firstResponseSlaDeadline,
    DateTime? resolutionSlaDeadline,
    DateTime? slaPausedAt,
    int? escalationCount,
    DateTime? lastEscalationFiredAt,
    TicketParentSummary? parentSummary,
    int? childCount,
    bool? isProblem,
    DateTime? createdAt,
    DateTime? closedOn,
    int? slaFirstResponseHours,
    int? slaResolutionHours,
    DateTime? firstResponseAt,
    DateTime? resolvedAt,
    bool? isFirstResponseSlaBreachedFromApi,
    bool? isResolutionSlaBreachedFromApi,
    List<Comment>? comments,
  }) {
    return Ticket(
      id: id ?? this.id,
      name: name ?? this.name,
      status: status ?? this.status,
      priority: priority ?? this.priority,
      ticketType: ticketType ?? this.ticketType,
      description: description ?? this.description,
      accountId: accountId ?? this.accountId,
      accountName: accountName ?? this.accountName,
      assignedTo: assignedTo ?? this.assignedTo,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      tags: tags ?? this.tags,
      tagIds: tagIds ?? this.tagIds,
      contactIds: contactIds ?? this.contactIds,
      contactNames: contactNames ?? this.contactNames,
      customFields: customFields ?? this.customFields,
      firstResponseSlaDeadline:
          firstResponseSlaDeadline ?? this.firstResponseSlaDeadline,
      resolutionSlaDeadline:
          resolutionSlaDeadline ?? this.resolutionSlaDeadline,
      slaPausedAt: slaPausedAt ?? this.slaPausedAt,
      escalationCount: escalationCount ?? this.escalationCount,
      lastEscalationFiredAt:
          lastEscalationFiredAt ?? this.lastEscalationFiredAt,
      parentSummary: parentSummary ?? this.parentSummary,
      childCount: childCount ?? this.childCount,
      isProblem: isProblem ?? this.isProblem,
      createdAt: createdAt ?? this.createdAt,
      closedOn: closedOn ?? this.closedOn,
      slaFirstResponseHours:
          slaFirstResponseHours ?? this.slaFirstResponseHours,
      slaResolutionHours: slaResolutionHours ?? this.slaResolutionHours,
      firstResponseAt: firstResponseAt ?? this.firstResponseAt,
      resolvedAt: resolvedAt ?? this.resolvedAt,
      isFirstResponseSlaBreachedFromApi:
          isFirstResponseSlaBreachedFromApi ??
          this.isFirstResponseSlaBreachedFromApi,
      isResolutionSlaBreachedFromApi:
          isResolutionSlaBreachedFromApi ?? this.isResolutionSlaBreachedFromApi,
      comments: comments ?? this.comments,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Ticket && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}

/// Lightweight ref to a parent ticket, used to render the "linked to" tile.
class TicketParentSummary {
  final String id;
  final String name;
  final String? status;

  const TicketParentSummary({
    required this.id,
    required this.name,
    this.status,
  });

  factory TicketParentSummary.fromJson(Map<String, dynamic> json) {
    return TicketParentSummary(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      status: json['status'] as String?,
    );
  }
}

/// What to say BEFORE closing a parent ticket: the line above the checkbox.
///
/// [count] is the open descendants the close would really touch, never the
/// ticket's `child_count`. See [cascadeCloseMessage] for why those differ.
///
/// [truncated] means the tree endpoint stopped at its depth cap, so the list
/// shown alongside is a floor. The close itself has no cap, so more can close
/// than the prompt is able to name, and saying so beats implying a complete
/// list.
///
/// `frontend/src/routes/(app)/tickets/[id]/close.js` carries the same rules.
String cascadeSummary({required int count, bool truncated = false}) {
  if (count == 0) {
    return 'Nothing linked to this ticket is still open, so closing it '
        'changes nothing else.';
  }
  final noun = count == 1 ? 'ticket' : 'tickets';
  final verb = count == 1 ? 'is' : 'are';
  final tail = truncated
      ? ' There may be more further down than are listed here, and those '
            'close too.'
      : '';
  return '$count linked $noun $verb still open and will be closed with it.'
      '$tail';
}

/// What to say after closing a parent ticket.
///
/// **Counted from the response, not from `child_count`.** The server closes the
/// open *descendants*, the whole subtree, and returns their ids in
/// `cascaded_case_ids`. `child_count` is neither of those: it counts direct
/// children whether they were already closed or not. Reporting it claimed
/// "and 3 linked" when three children were already closed and nothing happened,
/// and understated a deep tree where more than the direct children closed.
String cascadeCloseMessage({required bool cascade, required int cascaded}) {
  if (!cascade) return 'Ticket closed';
  if (cascaded == 0) {
    return 'Ticket closed. Nothing linked was open, so nothing else changed.';
  }
  return 'Ticket closed, and $cascaded linked '
      '${cascaded == 1 ? 'ticket' : 'tickets'} with it';
}

/// The ids the close endpoint reports as cascaded, counted defensively: a body
/// without the key means nothing was cascaded, not an unknown number.
int cascadedCount(Map<String, dynamic>? body) =>
    (body?['cascaded_case_ids'] as List?)?.length ?? 0;
