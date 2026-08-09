import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../../core/theme/app_colors.dart';
import 'lead.dart';

/// Related entity type for polymorphic relations
enum RelatedEntityType {
  lead('lead', 'Lead', LucideIcons.user),
  account('account', 'Account', LucideIcons.building2),
  opportunity('opportunity', 'Opportunity', LucideIcons.trendingUp),
  ticket_('case', 'Ticket', LucideIcons.headset),
  contact('contact', 'Contact', LucideIcons.contact);

  final String value;
  final String label;
  final IconData icon;

  const RelatedEntityType(this.value, this.label, this.icon);

  static RelatedEntityType fromString(String? value) {
    if (value == null) return RelatedEntityType.lead;
    return RelatedEntityType.values.firstWhere(
      (t) => t.value == value.toLowerCase(),
      orElse: () => RelatedEntityType.lead,
    );
  }
}

/// Related entity reference
class RelatedEntity {
  final String id;
  final RelatedEntityType type;
  final String title;

  const RelatedEntity({
    required this.id,
    required this.type,
    required this.title,
  });

  String get displayLabel => '${type.label}: $title';

  factory RelatedEntity.fromJson(
    Map<String, dynamic> json,
    RelatedEntityType type,
  ) {
    String title = '';
    if (type == RelatedEntityType.lead) {
      final firstName = json['first_name'] as String? ?? '';
      final lastName = json['last_name'] as String? ?? '';
      title = '$firstName $lastName'.trim();
      if (title.isEmpty) title = json['email'] as String? ?? 'Unknown';
    } else {
      title = json['name'] as String? ?? 'Unknown';
    }

    return RelatedEntity(
      id: json['id']?.toString() ?? '',
      type: type,
      title: title,
    );
  }
}

/// Task status enumeration matching backend STATUS_CHOICES
enum TaskStatus {
  newTask('New', 'New', AppColors.primary500),
  inProgress('In Progress', 'In Progress', AppColors.warning500),
  completed('Completed', 'Completed', AppColors.success500);

  final String value;
  final String label;
  final Color color;

  const TaskStatus(this.value, this.label, this.color);

  static TaskStatus fromString(String? value) {
    if (value == null) return TaskStatus.newTask;
    return TaskStatus.values.firstWhere(
      (s) => s.value.toLowerCase() == value.toLowerCase(),
      orElse: () => TaskStatus.newTask,
    );
  }
}

/// Task model for BottleCRM
class Task {
  final String id;
  final String title;
  final String? description;
  final DateTime? dueDate;
  final TaskStatus status;
  final Priority priority;
  final List<Map<String, dynamic>>? assignedTo;
  // Profile UUIDs of assignees, extracted from `assigned_to[].id`. Forms need
  // the IDs to round-trip through the API; the detail view uses `assignedTo`.
  final List<String> assignedToIds;
  final RelatedEntity? relatedTo;
  // Raw parent FK IDs from the API, exposed separately because the backend
  // serializes FKs as plain UUID strings (not nested objects), which the
  // `relatedTo` parser misses. Exactly one of these can be non-null per the
  // backend's `Task.clean()` invariant.
  final String? accountId;
  final String? opportunityId;
  final String? caseId;
  final String? leadId;
  final List<String> tags;
  // Resolved team names attached to the task (TaskSerializer returns nested
  // Team objects, but only the display name is used in the UI today).
  final List<String> teamNames;
  final Map<String, dynamic> customFields;
  // Task creator, backend serializes via UserSerializer, exposing at least
  // an email. We surface both first/last name (joined) and email so the UI
  // can pick whichever is non-empty.
  final String? createdByName;
  final String? createdByEmail;
  final DateTime createdAt;
  final DateTime? updatedAt;

  const Task({
    required this.id,
    required this.title,
    this.description,
    this.dueDate,
    required this.status,
    required this.priority,
    this.assignedTo,
    this.assignedToIds = const [],
    this.relatedTo,
    this.accountId,
    this.opportunityId,
    this.caseId,
    this.leadId,
    this.tags = const [],
    this.teamNames = const [],
    this.customFields = const {},
    this.createdByName,
    this.createdByEmail,
    required this.createdAt,
    this.updatedAt,
  });

  /// Check if task is completed
  bool get completed => status == TaskStatus.completed;

  /// Check if task is overdue
  bool get isOverdue {
    if (completed || dueDate == null) return false;
    return dueDate!.isBefore(DateTime.now());
  }

  /// Check if task is due today
  bool get isDueToday {
    if (dueDate == null) return false;
    final now = DateTime.now();
    return dueDate!.year == now.year &&
        dueDate!.month == now.month &&
        dueDate!.day == now.day;
  }

  /// Check if task is due within 2 hours
  bool get isDueSoon {
    if (completed || dueDate == null) return false;
    final now = DateTime.now();
    final diff = dueDate!.difference(now);
    return diff.inHours >= 0 && diff.inHours <= 2;
  }

  /// Check if task is upcoming (future, not today)
  bool get isUpcoming {
    if (completed || dueDate == null) return false;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final taskDate = DateTime(dueDate!.year, dueDate!.month, dueDate!.day);
    return taskDate.isAfter(today);
  }

  /// Get task due status category
  TaskDueStatus get dueStatus {
    if (completed) return TaskDueStatus.completed;
    if (isOverdue) return TaskDueStatus.overdue;
    if (isDueToday) return TaskDueStatus.today;
    if (dueDate == null) return TaskDueStatus.noDueDate;
    return TaskDueStatus.upcoming;
  }

  /// Days until due (negative if overdue)
  int? get daysUntilDue {
    if (dueDate == null) return null;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final taskDate = DateTime(dueDate!.year, dueDate!.month, dueDate!.day);
    return taskDate.difference(today).inDays;
  }

  /// Get assigned user name (first one if multiple)
  String get assignedToName {
    if (assignedTo == null || assignedTo!.isEmpty) return 'Unassigned';
    final first = assignedTo!.first;
    final email = first['user__email'] as String? ?? '';
    return email.split('@').first;
  }

  /// Factory constructor to create Task from JSON
  factory Task.fromJson(Map<String, dynamic> json) {
    // Parse tags
    List<String> parsedTags = [];
    if (json['tags'] != null) {
      final tagsList = json['tags'] as List<dynamic>;
      parsedTags = tagsList
          .map((t) {
            if (t is Map<String, dynamic>) {
              return t['name'] as String? ?? '';
            }
            return t.toString();
          })
          .where((t) => t.isNotEmpty)
          .toList();
    }

    // Parse assigned_to
    List<Map<String, dynamic>>? parsedAssignedTo;
    List<String> parsedAssignedToIds = const [];
    if (json['assigned_to'] != null) {
      final assignedList = json['assigned_to'] as List<dynamic>;
      parsedAssignedTo = assignedList
          .map((a) => a is Map<String, dynamic> ? a : <String, dynamic>{})
          .toList();
      parsedAssignedToIds = parsedAssignedTo
          .map((a) => a['id']?.toString() ?? '')
          .where((s) => s.isNotEmpty)
          .toList();
    }

    // Parse related entity (account, lead, opportunity, ticket).
    // Backend serializes FKs as plain UUID strings, so prefer the *Id capture
    // path below for round-tripping; the nested-object branch is kept for any
    // endpoint that does enrich (e.g. dashboard activity).
    String? extractId(dynamic raw) {
      if (raw == null) return null;
      if (raw is String && raw.isNotEmpty) return raw;
      if (raw is Map<String, dynamic>) return raw['id']?.toString();
      return null;
    }

    final accountId = extractId(json['account']);
    final opportunityId = extractId(json['opportunity']);
    final caseId = extractId(json['case']);
    final leadId = extractId(json['lead']);

    RelatedEntity? relatedEntity;
    if (json['account'] is Map<String, dynamic>) {
      relatedEntity = RelatedEntity.fromJson(
        json['account'] as Map<String, dynamic>,
        RelatedEntityType.account,
      );
    } else if (json['lead'] is Map<String, dynamic>) {
      relatedEntity = RelatedEntity.fromJson(
        json['lead'] as Map<String, dynamic>,
        RelatedEntityType.lead,
      );
    } else if (json['opportunity'] is Map<String, dynamic>) {
      relatedEntity = RelatedEntity.fromJson(
        json['opportunity'] as Map<String, dynamic>,
        RelatedEntityType.opportunity,
      );
    } else if (json['case'] is Map<String, dynamic>) {
      relatedEntity = RelatedEntity.fromJson(
        json['case'] as Map<String, dynamic>,
        RelatedEntityType.ticket_,
      );
    }

    final rawCustomFields = json['custom_fields'];
    final Map<String, dynamic> parsedCustomFields =
        rawCustomFields is Map<String, dynamic>
        ? Map<String, dynamic>.from(rawCustomFields)
        : const {};

    // Teams. TaskSerializer returns nested objects. Fall back to a string
    // if the API ever switches to plain values.
    final List<String> parsedTeamNames = [];
    if (json['teams'] is List) {
      for (final t in json['teams'] as List<dynamic>) {
        if (t is Map<String, dynamic>) {
          final name = t['name'] as String?;
          if (name != null && name.isNotEmpty) parsedTeamNames.add(name);
        } else if (t is String && t.isNotEmpty) {
          parsedTeamNames.add(t);
        }
      }
    }

    // created_by, UserSerializer shape: {id, email, name, profile_pic}.
    // Be defensive: backend serializers sometimes return null or a plain id,
    // and `name` may be an empty string for users who never set it.
    String? createdByName;
    String? createdByEmail;
    final cb = json['created_by'];
    if (cb is Map<String, dynamic>) {
      createdByEmail = cb['email'] as String?;
      final name = (cb['name'] as String?)?.trim();
      if (name != null && name.isNotEmpty) createdByName = name;
    }

    return Task(
      id: json['id']?.toString() ?? '',
      title: json['title'] as String? ?? '',
      description: json['description'] as String?,
      dueDate: json['due_date'] != null
          ? DateTime.tryParse(json['due_date'] as String)
          : null,
      status: TaskStatus.fromString(json['status'] as String?),
      priority: Priority.fromString(json['priority'] as String?),
      assignedTo: parsedAssignedTo,
      assignedToIds: parsedAssignedToIds,
      relatedTo: relatedEntity,
      accountId: accountId,
      opportunityId: opportunityId,
      caseId: caseId,
      leadId: leadId,
      tags: parsedTags,
      teamNames: parsedTeamNames,
      customFields: parsedCustomFields,
      createdByName: createdByName,
      createdByEmail: createdByEmail,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String) ?? DateTime.now()
          : DateTime.now(),
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'] as String)
          : null,
    );
  }

  /// Convert Task to JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'title': title,
      'description': description,
      'due_date': dueDate?.toIso8601String().split('T').first,
      'status': status.value,
      'priority': priority.label,
    };
  }

  Task copyWith({
    String? id,
    String? title,
    String? description,
    DateTime? dueDate,
    TaskStatus? status,
    Priority? priority,
    List<Map<String, dynamic>>? assignedTo,
    List<String>? assignedToIds,
    RelatedEntity? relatedTo,
    String? accountId,
    String? opportunityId,
    String? caseId,
    String? leadId,
    List<String>? tags,
    List<String>? teamNames,
    Map<String, dynamic>? customFields,
    String? createdByName,
    String? createdByEmail,
    DateTime? createdAt,
    DateTime? updatedAt,
  }) {
    return Task(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      dueDate: dueDate ?? this.dueDate,
      status: status ?? this.status,
      priority: priority ?? this.priority,
      assignedTo: assignedTo ?? this.assignedTo,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      relatedTo: relatedTo ?? this.relatedTo,
      accountId: accountId ?? this.accountId,
      opportunityId: opportunityId ?? this.opportunityId,
      caseId: caseId ?? this.caseId,
      leadId: leadId ?? this.leadId,
      tags: tags ?? this.tags,
      teamNames: teamNames ?? this.teamNames,
      customFields: customFields ?? this.customFields,
      createdByName: createdByName ?? this.createdByName,
      createdByEmail: createdByEmail ?? this.createdByEmail,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Task && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}

/// Task due status for grouping
enum TaskDueStatus {
  overdue,
  today,
  upcoming,
  noDueDate,
  completed;

  String get label {
    switch (this) {
      case TaskDueStatus.overdue:
        return 'Overdue';
      case TaskDueStatus.today:
        return 'Today';
      case TaskDueStatus.upcoming:
        return 'Upcoming';
      case TaskDueStatus.noDueDate:
        return 'No Due Date';
      case TaskDueStatus.completed:
        return 'Completed';
    }
  }
}
