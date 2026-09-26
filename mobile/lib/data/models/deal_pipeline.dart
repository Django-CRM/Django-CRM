import 'package:flutter/material.dart' show Color;

import '../../core/theme/app_colors.dart';

/// Deal pipelines and their stages, as `GET /opportunities/pipelines/` serves
/// them: every pipeline of the org, default first, each with its stages in
/// board order.
///
/// A stage's `code` is what a deal stores and what the board moves by; its
/// `label` is what people read; its `kind` (open, won or lost) is what every
/// rule reads. Nothing here decides a deal is closed from its code, except
/// [legacyDealStageKind] for a server too old to send the kind.
///
/// Every rule checked here is also checked by the serializers behind those
/// endpoints. The checks save a round trip; they are not a guard.

const dealStageOpen = 'open';
const dealStageWon = 'won';
const dealStageLost = 'lost';

/// `opportunity.workflow.STAGE_KINDS`.
const dealStageKinds = {
  dealStageOpen: 'Open',
  dealStageWon: 'Won',
  dealStageLost: 'Lost',
};

/// The six stages every pipeline starts with, in board order, as
/// `(code, label, kind, expected_days)`. Only read as a fallback: by a server
/// that predates configurable pipelines, and for a deal row that carries no
/// `stage_kind`.
const _legacyStages = [
  ('PROSPECTING', 'Prospecting', dealStageOpen, 14),
  ('QUALIFICATION', 'Qualification', dealStageOpen, 14),
  ('PROPOSAL', 'Proposal', dealStageOpen, 10),
  ('NEGOTIATION', 'Negotiation', dealStageOpen, 10),
  ('CLOSED_WON', 'Closed Won', dealStageWon, null),
  ('CLOSED_LOST', 'Closed Lost', dealStageLost, null),
];

/// `opportunity.workflow.STAGE_PROBABILITIES`: the seeded open stages' odds.
const _legacyProbabilities = {
  'PROSPECTING': 10,
  'QUALIFICATION': 25,
  'PROPOSAL': 50,
  'NEGOTIATION': 75,
};

/// The kind of a seeded stage code, for a row that does not say.
String legacyDealStageKind(String code) => switch (code) {
  'CLOSED_WON' => dealStageWon,
  'CLOSED_LOST' => dealStageLost,
  _ => dealStageOpen,
};

/// The label of a seeded stage code, else the code made readable.
String legacyDealStageLabel(String code) {
  for (final s in _legacyStages) {
    if (s.$1 == code) return s.$2;
  }
  return code
      .toLowerCase()
      .split('_')
      .where((w) => w.isNotEmpty)
      .map((w) => '${w[0].toUpperCase()}${w.substring(1)}')
      .join(' ');
}

/// The probability a deal takes on entering a stage, as
/// `opportunity.workflow.stage_probability` sets it server-side: 100 for a won
/// stage, 0 for a lost one, the seeded odds for a seeded open code, else 0.
int dealStageProbability(String code, String kind) {
  if (kind == dealStageWon) return 100;
  if (kind == dealStageLost) return 0;
  return _legacyProbabilities[code] ?? 0;
}

const _openPalette = [
  AppColors.gray400,
  AppColors.primary500,
  AppColors.purple500,
  AppColors.warning500,
  AppColors.teal500,
  AppColors.accent500,
];

/// A stage's colour: green for won, red for lost, and for an open stage a
/// colour picked by its place among the pipeline's open stages, so a renamed
/// or added stage never needs a table entry.
Color dealStageColor(String kind, int openIndex) {
  if (kind == dealStageWon) return AppColors.success500;
  if (kind == dealStageLost) return AppColors.danger500;
  return _openPalette[openIndex % _openPalette.length];
}

/// One stage of a deal pipeline.
class DealPipelineStage {
  const DealPipelineStage({
    required this.id,
    required this.code,
    required this.label,
    this.order = 0,
    this.kind = dealStageOpen,
    this.expectedDays,
    this.warningDays,
  });

  final String id;
  final String code;
  final String label;
  final int order;
  final String kind;

  /// Days a deal may sit here before it counts as past expected. Null means
  /// the stage never ages, which is always the case for won and lost.
  final int? expectedDays;

  /// An earlier day to show "Past expected", when sooner than
  /// [expectedDays]. Null for none.
  final int? warningDays;

  bool get isOpen => kind == dealStageOpen;
  bool get isWon => kind == dealStageWon;
  bool get isLost => kind == dealStageLost;
  bool get isClosed => isWon || isLost;

  /// `(pastExpected, stalled)` in whole days, or null when the stage never
  /// ages. The same arithmetic as `opportunity.workflow.aging_thresholds`,
  /// used here only to describe a stage; each deal's status is the server's.
  (int, int)? get agingThresholds {
    final expected = expectedDays;
    if (!isOpen || expected == null || expected <= 0) return null;
    final warning = warningDays;
    final yellow = (warning != null && warning > 0 && warning < expected)
        ? warning
        : expected;
    return (yellow, (expected * 1.5).ceil());
  }

  factory DealPipelineStage.fromJson(Map<String, dynamic> json) {
    final code = (json['code'] ?? '').toString();
    return DealPipelineStage(
      id: (json['id'] ?? '').toString(),
      code: code,
      label: (json['label'] as String?) ?? legacyDealStageLabel(code),
      order: (json['order'] as num?)?.toInt() ?? 0,
      kind: (json['kind'] as String?) ?? legacyDealStageKind(code),
      expectedDays: (json['expected_days'] as num?)?.toInt(),
      warningDays: (json['warning_days'] as num?)?.toInt(),
    );
  }
}

/// One pipeline with its stages, in board order.
class DealPipeline {
  const DealPipeline({
    required this.id,
    required this.name,
    this.isDefault = false,
    this.stages = const [],
  });

  /// Empty for [DealPipeline.legacy], which no request may name.
  final String id;
  final String name;
  final bool isDefault;
  final List<DealPipelineStage> stages;

  /// The single pipeline a server without configurable pipelines has. Its id
  /// is empty, so nothing sends it as a `pipeline` value.
  static final legacy = DealPipeline(
    id: '',
    name: 'Sales',
    isDefault: true,
    stages: [
      for (final (i, s) in _legacyStages.indexed)
        DealPipelineStage(
          id: s.$1,
          code: s.$1,
          label: s.$2,
          order: i,
          kind: s.$3,
          expectedDays: s.$4,
        ),
    ],
  );

  DealPipeline withStages(List<DealPipelineStage> stages) =>
      DealPipeline(id: id, name: name, isDefault: isDefault, stages: stages);

  DealPipelineStage? stageByCode(String code) {
    for (final s in stages) {
      if (s.code == code) return s;
    }
    return null;
  }

  /// Where a new deal starts, and where a deal moved to this pipeline lands.
  DealPipelineStage? get firstOpenStage {
    for (final s in stages) {
      if (s.isOpen) return s;
    }
    return null;
  }

  DealPipelineStage? get firstLostStage {
    for (final s in stages) {
      if (s.isLost) return s;
    }
    return null;
  }

  /// See [dealStageColor].
  Color colorOf(DealPipelineStage stage) {
    var openIndex = 0;
    for (final s in stages) {
      if (s.code == stage.code) break;
      if (s.isOpen) openIndex++;
    }
    return dealStageColor(stage.kind, openIndex);
  }

  factory DealPipeline.fromJson(Map<String, dynamic> json) {
    final stages =
        (json['stages'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(DealPipelineStage.fromJson)
            .toList()
          ..sort((a, b) => a.order.compareTo(b.order));
    return DealPipeline(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] as String?) ?? 'Pipeline',
      isDefault: json['is_default'] == true,
      stages: stages,
    );
  }
}

/// The pipeline a screen shows: the one chosen if it still exists, else the
/// org's default, else the first. Null only for an empty list.
DealPipeline? activeDealPipeline(List<DealPipeline> pipelines, String? id) {
  if (pipelines.isEmpty) return null;
  for (final p in pipelines) {
    if (id != null && p.id == id) return p;
  }
  for (final p in pipelines) {
    if (p.isDefault) return p;
  }
  return pipelines.first;
}

/// The pipeline a deal is in. A deal from a server that sends no `pipeline`
/// is in the org's only (default) one; a deal naming a pipeline this list does
/// not hold gets null rather than some other pipeline's stages.
DealPipeline? dealPipelineOf(List<DealPipeline> pipelines, String? id) {
  if (id == null) return activeDealPipeline(pipelines, null);
  for (final p in pipelines) {
    if (p.id == id) return p;
  }
  return null;
}

/// The model's `max_length` on a pipeline name and on a stage label.
const dealPipelineNameMax = 100;
const dealStageLabelMax = 100;

/// The serializer's bounds on `expected_days` and `warning_days`.
const dealStageDaysMin = 1;
const dealStageDaysMax = 3650;

/// Why a pipeline name cannot be sent, or null when it can.
String? dealPipelineNameProblem(String name) {
  final trimmed = name.trim();
  if (trimmed.isEmpty) return 'Give the pipeline a name.';
  if (trimmed.length > dealPipelineNameMax) {
    return 'Keep the name to $dealPipelineNameMax characters.';
  }
  return null;
}

/// Why a stage cannot be sent, or null when it can. The two day counts are the
/// text in their boxes, where empty means "none". They are only read for an
/// open stage, since the server drops them from a closed one.
String? dealStageDraftProblem({
  required String label,
  required String kind,
  required String expectedDays,
  required String warningDays,
}) {
  final trimmed = label.trim();
  if (trimmed.isEmpty) return 'Give the stage a name.';
  if (trimmed.length > dealStageLabelMax) {
    return 'Keep the name to $dealStageLabelMax characters.';
  }
  if (kind != dealStageOpen) return null;
  for (final text in [expectedDays, warningDays]) {
    if (text.trim().isEmpty) continue;
    final days = int.tryParse(text.trim());
    if (days == null || days < dealStageDaysMin || days > dealStageDaysMax) {
      return 'Days must be a whole number from $dealStageDaysMin to '
          '$dealStageDaysMax.';
    }
  }
  return null;
}

/// The body for adding or editing a stage. No `code` (derived server-side and
/// fixed) and no `order` (the reorder endpoint's job). The day counts are
/// always present, since null is how "never ages" is sent, and a closed stage
/// sends null for both.
Map<String, dynamic> dealStagePayload({
  required String label,
  required String kind,
  required String expectedDays,
  required String warningDays,
}) {
  final open = kind == dealStageOpen;
  return {
    'label': label.trim(),
    'kind': kind,
    'expected_days': open ? int.tryParse(expectedDays.trim()) : null,
    'warning_days': open ? int.tryParse(warningDays.trim()) : null,
  };
}

/// What a deal in a stage of [kind] still needs before it can be saved, as
/// `(field, sentence)`, or null when nothing. Mirrors the server: a won deal
/// records its amount, and any closed deal records when it closed.
({String field, String message})? dealCloseProblem({
  required String kind,
  required String stageLabel,
  required double? amount,
  required DateTime? closeDate,
}) {
  if (kind == dealStageWon && (amount == null || amount <= 0)) {
    return (field: 'amount', message: 'A deal in $stageLabel needs an amount.');
  }
  if ((kind == dealStageWon || kind == dealStageLost) && closeDate == null) {
    return (
      field: 'closed_on',
      message: 'A deal in $stageLabel needs a close date.',
    );
  }
  return null;
}
