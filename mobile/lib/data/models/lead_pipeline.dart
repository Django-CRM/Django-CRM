import 'package:flutter/material.dart' show Color;

import 'lead_board.dart' show leadStageSwatch;

/// Lead pipelines as the settings screen administers them.
///
/// Read from `GET /leads/pipelines/` (the list, with counts) and
/// `GET /leads/pipelines/<id>/` (one pipeline with its stages). The board has
/// its own slimmer read of the same list in `lead_board.dart`; this is the
/// admin's view, which needs the counts and every stage field it can edit.
///
/// Every rule checked here is also checked by the serializers behind those
/// endpoints. The checks are a courtesy that saves a round trip, not a guard.

/// One pipeline, as the list shows it.
class LeadPipeline {
  const LeadPipeline({
    required this.id,
    required this.name,
    this.description = '',
    this.isDefault = false,
    this.stageCount = 0,
    this.leadCount = 0,
  });

  final String id;
  final String name;
  final String description;
  final bool isDefault;
  final int stageCount;

  /// The leads in this pipeline's stages that the caller may see.
  final int leadCount;

  factory LeadPipeline.fromJson(Map<String, dynamic> json) {
    return LeadPipeline(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Untitled pipeline',
      description: (json['description'] as String?) ?? '',
      isDefault: json['is_default'] == true,
      stageCount: json['stage_count'] as int? ?? 0,
      leadCount: json['lead_count'] as int? ?? 0,
    );
  }
}

/// One stage of a pipeline, with every field the stage sheet edits.
class LeadStage {
  const LeadStage({
    required this.id,
    required this.name,
    this.order = 0,
    this.color = '',
    this.stageType = 'open',
    this.mapsToStatus,
    this.winProbability = 0,
    this.leadCount = 0,
  });

  final String id;
  final String name;
  final int order;

  /// `#RRGGBB` as stored. See [swatch].
  final String color;
  final String stageType;

  /// The lead status a move into this stage sets, or null for none.
  final String? mapsToStatus;
  final int winProbability;
  final int leadCount;

  Color get swatch => leadStageSwatch(color);

  factory LeadStage.fromJson(Map<String, dynamic> json) {
    final status = json['maps_to_status'] as String?;
    return LeadStage(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Stage',
      order: json['order'] as int? ?? 0,
      color: (json['color'] as String?) ?? '',
      stageType: (json['stage_type'] as String?) ?? 'open',
      mapsToStatus: (status == null || status.isEmpty) ? null : status,
      winProbability: json['win_probability'] as int? ?? 0,
      leadCount: json['lead_count'] as int? ?? 0,
    );
  }
}

/// One pipeline with its stages, in board order.
class LeadPipelineDetail {
  const LeadPipelineDetail({required this.pipeline, this.stages = const []});

  final LeadPipeline pipeline;
  final List<LeadStage> stages;

  LeadPipelineDetail withStages(List<LeadStage> stages) =>
      LeadPipelineDetail(pipeline: pipeline, stages: stages);

  factory LeadPipelineDetail.fromJson(Map<String, dynamic> json) {
    final stages =
        (json['stages'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(LeadStage.fromJson)
            .toList()
          ..sort((a, b) => a.order.compareTo(b.order));
    return LeadPipelineDetail(
      pipeline: LeadPipeline.fromJson(json),
      stages: stages,
    );
  }
}

/// `LeadStage.STAGE_TYPE_CHOICES`.
const leadStageTypes = {'open': 'Open', 'won': 'Won', 'lost': 'Lost'};

/// The lead statuses a stage may be set to map to. `converted` is missing on
/// purpose: the server refuses it as a new value, because the board refuses
/// every move into a stage that converts.
const leadStageStatuses = {
  'assigned': 'Assigned',
  'in process': 'In Process',
  'recycled': 'Recycled',
  'closed': 'Closed',
};

/// The choices a picker offers for a stage type: the known ones, plus the
/// stage's current value when it is none of them, labelled with its name.
/// A picker with no option for the stored value would submit a different
/// one, so an unchanged save must be able to send the value back.
Map<String, String> leadStageTypeOptions(String current) => {
  ...leadStageTypes,
  if (!leadStageTypes.containsKey(current)) current: _label(current),
};

/// The choices for "sets status to", keyed by the value sent. Null is "none".
/// A stage that already maps to a status the list leaves out (an older
/// `converted`) keeps it as an extra option: the server lets an unchanged
/// value through, and dropping it would rewrite the stage on every save.
Map<String?, String> leadStageStatusOptions(String? current) => {
  null: 'None',
  ...leadStageStatuses,
  if (current != null && !leadStageStatuses.containsKey(current))
    current: _label(current),
};

String _label(String value) => value.isEmpty
    ? value
    : value
          .split(' ')
          .map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}')
          .join(' ');

/// The model's `max_length` on each name.
const leadPipelineNameMax = 255;
const leadStageNameMax = 100;

/// Why a pipeline name cannot be sent, or null when it can.
String? leadPipelineNameProblem(String name) {
  final trimmed = name.trim();
  if (trimmed.isEmpty) return 'Give the pipeline a name.';
  if (trimmed.length > leadPipelineNameMax) {
    return 'Keep the name to $leadPipelineNameMax characters.';
  }
  return null;
}

/// Why a stage cannot be sent, or null when it can. [winProbability] is the
/// text in the box, since "empty" and "not a number" are problems too.
String? leadStageDraftProblem({
  required String name,
  required String winProbability,
}) {
  final trimmed = name.trim();
  if (trimmed.isEmpty) return 'Give the stage a name.';
  if (trimmed.length > leadStageNameMax) {
    return 'Keep the name to $leadStageNameMax characters.';
  }
  final probability = int.tryParse(winProbability.trim());
  if (probability == null || probability < 0 || probability > 100) {
    return 'Win probability must be a whole number from 0 to 100.';
  }
  return null;
}

/// The body for creating or renaming a pipeline. `create_default_stages` goes
/// only on create; the server reads it there and nowhere else.
Map<String, dynamic> leadPipelinePayload({
  required String name,
  required String description,
  bool? createDefaultStages,
}) => {
  'name': name.trim(),
  'description': description.trim(),
  'create_default_stages': ?createDefaultStages,
};

/// The body for creating or editing a stage. No `order`: a new stage goes
/// last when none is sent, and position is the reorder endpoint's job. The
/// status key is always present, because null is how "none" is sent.
Map<String, dynamic> leadStagePayload({
  required String name,
  required String stageType,
  required String? mapsToStatus,
  required int winProbability,
}) => {
  'name': name.trim(),
  'stage_type': stageType,
  'maps_to_status': mapsToStatus,
  'win_probability': winProbability,
};
