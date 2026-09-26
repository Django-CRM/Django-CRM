import 'package:flutter/material.dart';

/// The lead pipeline board: leads grouped by the stages of one `LeadPipeline`.
///
/// Read from `GET /leads/pipelines/` (the picker) and
/// `GET /leads/kanban/?pipeline_id=` (the lanes). The kanban response carries
/// the stages as `columns` and, beside them, `unstaged`: the leads in no
/// pipeline at all. A lead is created with no stage, so that group is the only
/// way into a pipeline, and it is shown as the first lane, "No stage". Moving
/// a lead back there takes it out of its pipeline.

const _grey = Color(0xFF6B7280);

/// A `#RRGGBB` stage colour, or grey for anything else.
Color leadStageSwatch(String? hex) {
  final raw = (hex ?? '').replaceFirst('#', '');
  if (raw.length != 6) return _grey;
  final value = int.tryParse(raw, radix: 16);
  return value == null ? _grey : Color(0xFF000000 | value);
}

/// One pipeline, as the picker lists it.
class LeadPipelineSummary {
  const LeadPipelineSummary({required this.id, required this.name});

  final String id;
  final String name;

  factory LeadPipelineSummary.fromJson(Map<String, dynamic> json) {
    return LeadPipelineSummary(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Untitled pipeline',
    );
  }
}

/// One lead on the board. Only what a card shows; the detail screen is a tap
/// away.
class LeadBoardCard {
  const LeadBoardCard({
    required this.id,
    required this.name,
    this.company = '',
    this.rating = '',
    this.owner = '',
    this.followUpOverdue = false,
  });

  final String id;
  final String name;
  final String company;

  /// `HOT`, `WARM`, `COLD`, or empty.
  final String rating;

  /// The first assignee's name or email, or empty when nobody is assigned.
  final String owner;
  final bool followUpOverdue;

  factory LeadBoardCard.fromJson(Map<String, dynamic> json) {
    final assigned = json['assigned_to'];
    String owner = '';
    if (assigned is List && assigned.isNotEmpty && assigned.first is Map) {
      final details = (assigned.first as Map)['user_details'];
      if (details is Map) {
        final name = (details['name'] as String?) ?? '';
        owner = name.isNotEmpty ? name : (details['email'] as String?) ?? '';
      }
    }
    final fullName = (json['full_name'] as String?)?.trim() ?? '';
    return LeadBoardCard(
      id: (json['id'] ?? '').toString(),
      name: fullName.isNotEmpty
          ? fullName
          : ((json['title'] as String?) ?? 'Unnamed lead'),
      company: (json['company_name'] as String?) ?? '',
      rating: (json['rating'] as String?) ?? '',
      owner: owner,
      followUpOverdue: json['is_follow_up_overdue'] == true,
    );
  }
}

/// One lane: a stage of the pipeline, or the "No stage" group.
class LeadBoardLane {
  const LeadBoardLane({
    required this.id,
    required this.name,
    this.color = _grey,
    this.count = 0,
    this.wipLimit,
    this.cards = const [],
    this.isUnstaged = false,
  });

  /// The stage id. Empty for the "No stage" lane, which is not a stage: a
  /// move there sends `stage_id: null`. See [moveStageId].
  final String id;
  final String name;
  final Color color;

  /// Every lead in the lane the caller may see. The API sends at most 100
  /// cards a lane, so this can be larger than `cards.length`.
  final int count;
  final int? wipLimit;
  final List<LeadBoardCard> cards;
  final bool isUnstaged;

  bool get isTruncated => count > cards.length;

  /// What a move into this lane sends as `stage_id`: the stage, or null for
  /// "No stage", which takes the lead out of its pipeline.
  String? get moveStageId => isUnstaged ? null : id;

  static List<LeadBoardCard> _cards(Object? raw) => raw is List
      ? raw
            .whereType<Map<String, dynamic>>()
            .map(LeadBoardCard.fromJson)
            .toList()
      : const [];

  factory LeadBoardLane.fromStageJson(Map<String, dynamic> json) {
    final cards = _cards(json['leads']);
    return LeadBoardLane(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Stage',
      color: leadStageSwatch(json['color'] as String?),
      count: json['lead_count'] as int? ?? cards.length,
      wipLimit: json['wip_limit'] as int?,
      cards: cards,
    );
  }

  factory LeadBoardLane.unstaged(Map<String, dynamic>? json) {
    final cards = _cards(json?['leads']);
    return LeadBoardLane(
      id: '',
      name: 'No stage',
      count: json?['lead_count'] as int? ?? cards.length,
      cards: cards,
      isUnstaged: true,
    );
  }

  /// "No stage" first, then the stages in their `order`.
  static List<LeadBoardLane> fromKanbanJson(Map<String, dynamic> json) {
    final columns =
        (json['columns'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .toList()
          ..sort(
            (a, b) =>
                (a['order'] as int? ?? 0).compareTo(b['order'] as int? ?? 0),
          );
    return [
      LeadBoardLane.unstaged(json['unstaged'] as Map<String, dynamic>?),
      ...columns.map(LeadBoardLane.fromStageJson),
    ];
  }
}

/// The stage a lead is in, as the lead detail endpoint names it in
/// `pipeline_stage`. Null there when the lead is in no pipeline.
class LeadStageRef {
  const LeadStageRef({
    required this.name,
    required this.pipelineName,
    this.color = _grey,
  });

  final String name;
  final String pipelineName;
  final Color color;

  static LeadStageRef? fromJson(Object? json) {
    if (json is! Map<String, dynamic>) return null;
    final pipeline = json['pipeline'];
    return LeadStageRef(
      name: (json['name'] as String?) ?? 'Stage',
      pipelineName: pipeline is Map ? (pipeline['name'] as String?) ?? '' : '',
      color: leadStageSwatch(json['color'] as String?),
    );
  }
}
