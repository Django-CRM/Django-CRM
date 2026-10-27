import '../api/enterprise_schema.g.dart' as enterprise;
import 'custom_object.dart';

/// What a column of a custom object's board holds: one of the dropdown's
/// options, the records with no value, or the records holding a value that is
/// no longer an option.
enum BoardColumnKind {
  option,
  none,
  other;

  static BoardColumnKind parse(dynamic raw) => switch (raw) {
    'none' => none,
    'other' => other,
    _ => option,
  };
}

class ObjectBoardColumn {
  const ObjectBoardColumn({
    required this.kind,
    required this.label,
    this.value,
    this.count = 0,
    this.records = const [],
  });

  final BoardColumnKind kind;
  final String label;

  /// The option's value; null for the "No value" and "Other values" columns.
  final String? value;

  /// Every record in the column, which can exceed [records] (the server sends
  /// at most 100).
  final int count;
  final List<ObjectRecord> records;

  bool get isTruncated => count > records.length;

  /// "Other values" is read-only: nothing is dropped on it.
  bool get acceptsDrops => kind != BoardColumnKind.other;

  factory ObjectBoardColumn.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordBoardColumn.fromJson(json);
    final records = [
      for (final r in w.records ?? const <enterprise.CustomRecordBoardCard>[])
        ObjectRecord.fromJson(r.json),
    ];
    return ObjectBoardColumn(
      kind: BoardColumnKind.parse(w.kind),
      label: w.label ?? '',
      value: w.value,
      count: w.count ?? records.length,
      records: records,
    );
  }

  ObjectBoardColumn copyWith({List<ObjectRecord>? records, int? count}) =>
      ObjectBoardColumn(
        kind: kind,
        label: label,
        value: value,
        count: count ?? this.count,
        records: records ?? this.records,
      );
}

class ObjectBoard {
  const ObjectBoard({
    required this.fieldKey,
    required this.fieldLabel,
    this.columns = const [],
  });

  final String fieldKey;
  final String fieldLabel;
  final List<ObjectBoardColumn> columns;

  factory ObjectBoard.fromJson(Map<String, dynamic> json) {
    final w = enterprise.CustomRecordBoard.fromJson(json);
    return ObjectBoard(
      fieldKey: w.field?.key ?? '',
      fieldLabel: w.field?.label ?? '',
      columns: [
        for (final c
            in w.columns ?? const <enterprise.CustomRecordBoardColumn>[])
          ObjectBoardColumn.fromJson(c.json),
      ],
    );
  }

  /// The board with the card [record] swapped for the server's copy, which
  /// carries the rights the viewer now has on it.
  ObjectBoard replaced(ObjectRecord record) => ObjectBoard(
    fieldKey: fieldKey,
    fieldLabel: fieldLabel,
    columns: [
      for (final c in columns)
        c.copyWith(
          records: [for (final r in c.records) r.id == record.id ? record : r],
        ),
    ],
  );

  /// The column [record] sits in, or null when it is on no column.
  ObjectBoardColumn? columnOf(String recordId) {
    for (final c in columns) {
      if (c.records.any((r) => r.id == recordId)) return c;
    }
    return null;
  }

  /// Whether [record] may be dropped on [target], by the same rules as the
  /// web: not on the column it is already in, not on "Other values", and not
  /// on "No value" when the field is required. The server decides again.
  bool canDrop(
    ObjectRecord record,
    ObjectBoardColumn target, {
    required bool fieldRequired,
  }) {
    if (!target.acceptsDrops) return false;
    if (target.kind == BoardColumnKind.none && fieldRequired) return false;
    return !identical(columnOf(record.id), target);
  }

  /// The board with [record] moved to the end of [target], optimistically.
  /// Counts follow when the column the record left held it in full.
  ObjectBoard moved(ObjectRecord record, ObjectBoardColumn target) {
    return ObjectBoard(
      fieldKey: fieldKey,
      fieldLabel: fieldLabel,
      columns: [
        for (final c in columns)
          if (identical(c, target))
            c.copyWith(records: [...c.records, record], count: c.count + 1)
          else if (c.records.any((r) => r.id == record.id))
            c.copyWith(
              records: [
                for (final r in c.records)
                  if (r.id != record.id) r,
              ],
              count: c.count - 1,
            )
          else
            c,
      ],
    );
  }
}

/// What a drop sends: the card now above the dropped one is the last card of
/// the target column, so it lands at the end.
Map<String, dynamic> boardMoveBody(
  ObjectRecord record,
  ObjectBoardColumn target,
) {
  final others = [
    for (final r in target.records)
      if (r.id != record.id) r,
  ];
  return {
    'record': record.id,
    'value': target.value,
    'above_id': others.isEmpty ? null : others.last.id,
    'below_id': null,
  };
}
