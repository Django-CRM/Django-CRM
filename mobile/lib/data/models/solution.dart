import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';

/// Knowledge Base solution: a reusable answer that can be linked to tickets.
/// Mirrors `cases.models.Solution`.
class Solution {
  final String id;
  final String title;
  final String description;
  final SolutionStatus status;
  final bool isPublished;
  final int caseCount;

  /// Ids of the tags this article is filed under. Agent-side only: the portal
  /// serializers carry no tags at all, because the vocabulary is shared with
  /// deals and reads like "At Risk". The portal uses them to pick related
  /// articles and never prints the names.
  final List<String> tagIds;

  /// Id of the user who wrote the article, from the detail route's
  /// `created_by`. Carried because editing and deleting belong to admins and
  /// the author, while approving and publishing belong to admins alone.
  final String? createdById;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  const Solution({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    this.isPublished = false,
    this.caseCount = 0,
    this.tagIds = const [],
    this.createdById,
    this.createdAt,
    this.updatedAt,
  });

  factory Solution.fromJson(Map<String, dynamic> json) {
    return Solution(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      description: json['description']?.toString() ?? '',
      status: SolutionStatus.fromString(json['status'] as String?),
      isPublished: json['is_published'] as bool? ?? false,
      caseCount: json['case_count'] as int? ?? 0,
      tagIds: _tagIds(json['tags']),
      // A bare id on the detail route; a nested object would also be readable.
      createdById: json['created_by'] is Map<String, dynamic>
          ? (json['created_by'] as Map<String, dynamic>)['id']?.toString()
          : json['created_by']?.toString(),
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'].toString())
          : null,
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'].toString())
          : null,
    );
  }

  Map<String, dynamic> toCreatePayload() => {
    'title': title,
    'description': description,
    'status': status.value,
    'is_published': isPublished,
    // Always sent. The form shows every active tag, so an unticked one is a
    // deliberate "not this", and the API reads an explicit empty list as
    // "clear" while an absent key means "leave alone".
    'tags': tagIds,
  };

  Solution copyWith({
    String? id,
    String? title,
    String? description,
    SolutionStatus? status,
    bool? isPublished,
    int? caseCount,
    List<String>? tagIds,
  }) {
    return Solution(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      status: status ?? this.status,
      isPublished: isPublished ?? this.isPublished,
      caseCount: caseCount ?? this.caseCount,
      tagIds: tagIds ?? this.tagIds,
      createdById: createdById,
      createdAt: createdAt,
      updatedAt: updatedAt,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Solution && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}

/// The API nests `tags` as objects, not bare ids. Mirrors the helper of the
/// same shape in `account.dart`.
List<String> _tagIds(dynamic raw) {
  if (raw is! List) return const [];
  return raw
      .map((e) => e is Map ? e['id']?.toString() : e?.toString())
      .whereType<String>()
      .where((s) => s.isNotEmpty)
      .toList(growable: false);
}

enum SolutionStatus {
  draft('draft', 'Draft', AppColors.gray500),
  reviewed('reviewed', 'Reviewed', AppColors.warning500),
  approved('approved', 'Approved', AppColors.success600);

  final String value;
  final String label;
  final Color color;
  const SolutionStatus(this.value, this.label, this.color);

  static SolutionStatus fromString(String? value) {
    return SolutionStatus.values.firstWhere(
      (s) => s.value == value,
      orElse: () => SolutionStatus.draft,
    );
  }
}
