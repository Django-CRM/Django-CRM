/// Where the time went, as `/api/time-entries/report/` returns it.
///
/// The timesheet answers "what did I do this week". This answers the question
/// asked of that data afterwards, usually by somebody else: how much of the
/// month went to this account, and how much of it can be billed.
///
/// Money arrives as a string, not a double, because the API builds it from
/// Decimal columns and a JSON number would round it on the way. It is parsed
/// once here rather than at every call site.
library;

/// One grouped row: an agent, a ticket or an account.
class TimeReportRow {
  const TimeReportRow({
    required this.name,
    this.key,
    this.totalMinutes = 0,
    this.billableMinutes = 0,
    this.billableValue = 0,
    this.entryCount = 0,
  });

  /// The id of the thing this row groups, or null where there is none: time
  /// on tickets with no account lands in a named bucket rather than being
  /// dropped, and unattributed time is exactly what a report is run to find.
  final String? key;
  final String name;
  final int totalMinutes;
  final int billableMinutes;
  final double billableValue;
  final int entryCount;

  bool get isBillableOnly => billableMinutes == totalMinutes;

  factory TimeReportRow.fromJson(Map<String, dynamic> json) {
    return TimeReportRow(
      key: json['key'] as String?,
      name: json['name'] as String? ?? '',
      totalMinutes: json['total_minutes'] as int? ?? 0,
      billableMinutes: json['billable_minutes'] as int? ?? 0,
      billableValue: _money(json['billable_value']),
      entryCount: json['entry_count'] as int? ?? 0,
    );
  }
}

/// The whole report: the window the server actually reported on, the rows,
/// and the totals under them.
class TimeReport {
  const TimeReport({
    required this.start,
    required this.end,
    this.groupBy = 'agent',
    this.rows = const [],
    this.totalMinutes = 0,
    this.billableMinutes = 0,
    this.billableValue = 0,
    this.entryCount = 0,
    this.currencies = const [],
  });

  /// Read back from the response rather than kept from the request: asked for
  /// without a window, the API picks the last 30 days, and the header has to
  /// name the days actually reported on.
  final DateTime start;
  final DateTime end;
  final String groupBy;
  final List<TimeReportRow> rows;

  final int totalMinutes;
  final int billableMinutes;
  final double billableValue;
  final int entryCount;

  /// Every currency present in the window. The value totals add across all of
  /// them, so more than one here means no single symbol can label the figure
  /// honestly and the screen says so instead of picking one.
  final List<String> currencies;

  bool get isEmpty => rows.isEmpty;

  bool get isMixedCurrency => currencies.length > 1;

  String? get currency => currencies.length == 1 ? currencies.first : null;

  int get billableShare =>
      totalMinutes == 0 ? 0 : ((billableMinutes / totalMinutes) * 100).round();

  factory TimeReport.fromJson(Map<String, dynamic> json) {
    final totals = (json['totals'] as Map<String, dynamic>?) ?? const {};
    return TimeReport(
      start: _date(json['start']),
      end: _date(json['end']),
      groupBy: json['group_by'] as String? ?? 'agent',
      rows: ((json['rows'] as List<dynamic>?) ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(TimeReportRow.fromJson)
          .toList(),
      totalMinutes: totals['total_minutes'] as int? ?? 0,
      billableMinutes: totals['billable_minutes'] as int? ?? 0,
      billableValue: _money(totals['billable_value']),
      entryCount: totals['entry_count'] as int? ?? 0,
      currencies: ((json['currencies'] as List<dynamic>?) ?? const [])
          .map((c) => c.toString())
          .toList(),
    );
  }
}

/// A decimal string from the API as a double. Absent or unparseable is 0,
/// never null: a missing total is nothing logged, and every caller would
/// otherwise write the same `?? 0`.
double _money(dynamic value) =>
    value == null ? 0 : (double.tryParse(value.toString()) ?? 0);

/// A YYYY-MM-DD string as a local calendar date.
///
/// Parsed with an explicit midnight rather than `DateTime.parse` on the bare
/// date, so it is local midnight and not UTC midnight: reading a UTC midnight
/// in a zone behind Greenwich lands on the previous day and mislabels the
/// window by one.
DateTime _date(dynamic value) {
  final text = value?.toString() ?? '';
  return DateTime.tryParse('${text}T00:00:00') ?? DateTime.now();
}
