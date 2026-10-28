import '../api/enterprise_schema.g.dart' as enterprise;

/// One fact on a match row, such as "Price: 250,000".
class MatchFact {
  const MatchFact({required this.label, required this.value});

  final String label;
  final String value;
}

class MatchRow {
  const MatchRow({required this.id, this.name = '', this.facts = const []});

  final String id;
  final String name;
  final List<MatchFact> facts;
}

/// The answer of `GET /api/real-estate/matches/<id>/`.
class RecordMatches {
  const RecordMatches({
    this.side = '',
    this.objectKey = '',
    this.objectLabel = '',
    this.objectLabelPlural = '',
    this.matches = const [],
    this.dismissedCount = 0,
  });

  final String side;
  final String objectKey;
  final String objectLabel;
  final String objectLabelPlural;
  final List<MatchRow> matches;
  final int dismissedCount;

  /// "Matching requirements", from the other side's plural label.
  String get heading {
    final plural = objectLabelPlural.isEmpty ? 'records' : objectLabelPlural;
    return 'Matching ${plural.toLowerCase()}';
  }

  /// Reads a `RealEstateMatches`.
  factory RecordMatches.fromJson(Map<String, dynamic> json) {
    final w = enterprise.RealEstateMatches.fromJson(json);
    final label = w.object?.label ?? '';
    final plural = w.object?.labelPlural ?? '';
    return RecordMatches(
      side: w.side ?? '',
      objectKey: w.object?.key ?? '',
      objectLabel: label,
      objectLabelPlural: plural.isEmpty ? label : plural,
      matches: [
        for (final m in w.matches ?? const <enterprise.RealEstateMatch>[])
          if ((m.id ?? '').isNotEmpty)
            MatchRow(
              id: m.id!,
              name: m.name ?? '',
              facts: [
                for (final f
                    in m.facts ?? const <enterprise.RealEstateMatchFact>[])
                  if ((f.value ?? '').isNotEmpty)
                    MatchFact(label: f.label ?? '', value: f.value!),
              ],
            ),
      ],
      dismissedCount: w.dismissedCount ?? 0,
    );
  }
}
