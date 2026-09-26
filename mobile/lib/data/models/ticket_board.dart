import 'package:flutter/material.dart';

import 'lead_board.dart';
import 'ticket.dart';

/// The ticket board: tickets grouped by status, or by the stages of one
/// ticket pipeline.
///
/// Read from `GET /cases/pipelines/` (the picker) and `GET /cases/kanban/`
/// (the lanes; `?pipeline_id=` for a pipeline's stages). In status mode each
/// lane's id is the status value and a move sends `{status}`; in pipeline mode
/// it is the stage id and a move sends `{stage_id}`.
///
/// The status-mode Duplicate lane is dropped. The board hides merged
/// duplicates, so that lane is always empty, and moving a ticket into it would
/// make the ticket vanish without a merge.

/// A pipeline as the picker lists it: the same `{id, name}` the lead board
/// reads.
typedef TicketPipelineSummary = LeadPipelineSummary;

/// One ticket on the board. Only what a card shows; the detail screen is a
/// tap away.
class TicketBoardCard {
  const TicketBoardCard({
    required this.id,
    required this.name,
    this.accountName = '',
    this.assignee = '',
    this.priority = TicketPriority.normal,
    this.slaBreached = false,
    this.slaAtRisk = false,
  });

  final String id;
  final String name;
  final String accountName;

  /// The first assignee's name or email, or empty when nobody is assigned.
  final String assignee;
  final TicketPriority priority;
  final bool slaBreached;
  final bool slaAtRisk;

  factory TicketBoardCard.fromJson(Map<String, dynamic> json) {
    final assigned = json['assigned_to'];
    String assignee = '';
    if (assigned is List && assigned.isNotEmpty && assigned.first is Map) {
      final details = (assigned.first as Map)['user_details'];
      if (details is Map) {
        final name = (details['name'] as String?) ?? '';
        assignee = name.isNotEmpty ? name : (details['email'] as String?) ?? '';
      }
    }
    final name = (json['name'] as String?)?.trim() ?? '';
    return TicketBoardCard(
      id: (json['id'] ?? '').toString(),
      name: name.isNotEmpty ? name : 'Untitled ticket',
      accountName: (json['account_name'] as String?) ?? '',
      assignee: assignee,
      priority: TicketPriority.fromString(json['priority'] as String?),
      slaBreached: json['is_sla_breached'] == true,
      slaAtRisk: json['is_sla_at_risk'] == true,
    );
  }
}

/// One lane: a status, or a stage of the open pipeline.
class TicketBoardLane {
  const TicketBoardLane({
    required this.id,
    required this.name,
    this.isStatus = true,
    this.color = const Color(0xFF6B7280),
    this.count = 0,
    this.wipLimit,
    this.cards = const [],
  });

  /// The status value in status mode, the stage id in pipeline mode.
  final String id;
  final String name;
  final bool isStatus;
  final Color color;

  /// Every ticket in the lane the caller may see. The API sends at most 100
  /// cards a lane, so this can be larger than `cards.length`.
  final int count;
  final int? wipLimit;
  final List<TicketBoardCard> cards;

  bool get isTruncated => count > cards.length;

  /// The `PATCH /cases/<id>/move/` body that lands a ticket in this lane.
  Map<String, dynamic> get moveBody =>
      isStatus ? {'status': id} : {'stage_id': id};

  factory TicketBoardLane.fromJson(
    Map<String, dynamic> json, {
    required bool isStatus,
  }) {
    final raw = json['cases'];
    final cards = raw is List
        ? raw
              .whereType<Map<String, dynamic>>()
              .map(TicketBoardCard.fromJson)
              .toList()
        : <TicketBoardCard>[];
    return TicketBoardLane(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Stage',
      isStatus: isStatus,
      color: leadStageSwatch(json['color'] as String?),
      count: json['case_count'] as int? ?? cards.length,
      wipLimit: json['wip_limit'] as int?,
      cards: cards,
    );
  }

  /// The lanes in their `order`, without the status-mode Duplicate lane.
  static List<TicketBoardLane> fromKanbanJson(Map<String, dynamic> json) {
    final isStatus = json['mode'] != 'pipeline';
    final columns =
        (json['columns'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .where(
              (c) => !(isStatus && c['id'] == TicketStatus.duplicate.value),
            )
            .toList()
          ..sort(
            (a, b) =>
                (a['order'] as int? ?? 0).compareTo(b['order'] as int? ?? 0),
          );
    return columns
        .map((c) => TicketBoardLane.fromJson(c, isStatus: isStatus))
        .toList();
  }
}
