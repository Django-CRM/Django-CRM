/// Sales goals and the attainment board, from `/api/opportunities/goals/`.
///
/// `progress_value`, `progress_percent` and `status` are the server's, computed
/// over CLOSED_WON opportunities inside the period. They are read straight off
/// the payload and never recomputed here: this app cannot see the opportunities
/// another person's goal counts, so any local arithmetic would be a different
/// number wearing the same label.
///
/// Reading is open to any member and narrowed server-side to their own goals
/// and their teams'. Creating, editing and deleting are admin-only and answer
/// 403 to everyone else, which is why the screens gate on role.
library;

/// The three goal kinds the backend accepts (`common/utils.py` GOAL_TYPES).
const List<String> goalTypes = ['REVENUE', 'DEALS_CLOSED', 'ACTIVITIES'];

/// The deal types a weight can be set against (`common/utils.py`
/// OPPORTUNITY_TYPES). A type left out of a goal's map counts at full value, so
/// the form leaves every box blank and an untouched form stores `{}`. An
/// ACTIVITIES goal has no deal type and the backend refuses weights on one.
const List<String> dealTypes = [
  'NEW_BUSINESS',
  'EXISTING_BUSINESS',
  'RENEWAL',
  'UPSELL',
  'CROSS_SELL',
];

String dealTypeLabel(String type) {
  switch (type) {
    case 'NEW_BUSINESS':
      return 'New business';
    case 'EXISTING_BUSINESS':
      return 'Existing business';
    case 'RENEWAL':
      return 'Renewal';
    case 'UPSELL':
      return 'Upsell';
    case 'CROSS_SELL':
      return 'Cross-sell';
    default:
      return type;
  }
}

/// The four periods the backend accepts (`common/utils.py` PERIOD_TYPES).
const List<String> goalPeriodTypes = [
  'MONTHLY',
  'QUARTERLY',
  'YEARLY',
  'CUSTOM',
];

String goalTypeLabel(String type) {
  switch (type) {
    case 'DEALS_CLOSED':
      return 'Deals closed';
    case 'ACTIVITIES':
      return 'Activities';
    default:
      return 'Revenue';
  }
}

String goalPeriodLabel(String period) {
  switch (period) {
    case 'MONTHLY':
      return 'Monthly';
    case 'QUARTERLY':
      return 'Quarterly';
    case 'YEARLY':
      return 'Yearly';
    case 'CUSTOM':
      return 'Custom';
    default:
      return period;
  }
}

/// The four values `SalesGoal.status` takes, spelled for a person.
///
/// The server compares attainment against elapsed time, so "behind" means
/// behind pace rather than below target: a goal at 40% a week into a month is
/// ahead, and the same 40% three weeks in is not.
String goalStatusLabel(String status) {
  switch (status) {
    case 'completed':
      return 'Target met';
    case 'on_track':
      return 'On track';
    case 'at_risk':
      return 'At risk';
    case 'behind':
      return 'Behind pace';
    default:
      return status;
  }
}

/// One goal, from a `SalesGoalSerializer` row.
class SalesGoal {
  const SalesGoal({
    required this.id,
    required this.name,
    required this.goalType,
    required this.targetValue,
    required this.periodType,
    required this.periodStart,
    required this.periodEnd,
    this.assignedToId,
    this.assignedToName,
    this.teamId,
    this.teamName,
    this.isActive = true,
    this.typeWeights = const {},
    this.progressValue = 0,
    this.progressPercent = 0,
    this.status = 'on_track',
  });

  final String id;
  final String name;
  final String goalType;
  final double targetValue;
  final String periodType;

  /// Date-only on the server. Held as `YYYY-MM-DD` rather than a DateTime so a
  /// timezone conversion can never move a period boundary by a day.
  final String periodStart;
  final String periodEnd;

  /// A goal belongs to one person, or one team, or the whole org. The API
  /// permits both FKs at once; the forms on both clients offer one target, so
  /// nothing either of them writes can set two.
  final String? assignedToId;
  final String? assignedToName;
  final String? teamId;
  final String? teamName;

  final bool isActive;

  /// Optional multiplier per opportunity type, e.g. `{'RENEWAL': 0.5}`. A type
  /// left out weighs 1, so an empty map is an unweighted goal. Never null, so
  /// callers do not guard at every use.
  final Map<String, num> typeWeights;

  final double progressValue;
  final int progressPercent;
  final String status;

  /// Who the goal is for, as the list prints it.
  String get targetLabel {
    if (assignedToName != null && assignedToName!.isNotEmpty) {
      return assignedToName!;
    }
    if (teamName != null && teamName!.isNotEmpty) return '${teamName!} (team)';
    return 'Whole organisation';
  }

  /// How many deal types this goal actually re-weighs. A weight of exactly 1
  /// is the default rather than an adjustment, so it does not count.
  int get weightedTypeCount => typeWeights.values.where((w) => w != 1).length;

  /// `REVENUE` targets are money; `DEALS_CLOSED` and `ACTIVITIES` targets are
  /// counts. Handing the caller the distinction rather than a formatted string
  /// keeps the org's currency symbol out of this file.
  ///
  /// Written as an equality rather than `!= 'DEALS_CLOSED'`, which is what it
  /// used to say: that phrasing made every goal type added later default to
  /// money, and ACTIVITIES duly arrived and rendered a quota of forty logged
  /// activities as "$40".
  bool get isMoney => goalType == 'REVENUE';

  factory SalesGoal.fromJson(Map<String, dynamic> json) {
    final assigned = json['assigned_to_detail'] as Map<String, dynamic>?;
    final team = json['team_detail'] as Map<String, dynamic>?;
    return SalesGoal(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      goalType: json['goal_type']?.toString() ?? 'REVENUE',
      targetValue: _toDouble(json['target_value']),
      periodType: json['period_type']?.toString() ?? 'MONTHLY',
      periodStart: json['period_start']?.toString() ?? '',
      periodEnd: json['period_end']?.toString() ?? '',
      assignedToId: json['assigned_to']?.toString(),
      assignedToName: assigned == null ? null : _personName(assigned),
      teamId: json['team']?.toString(),
      teamName: team?['name']?.toString(),
      isActive: json['is_active'] as bool? ?? true,
      typeWeights: _toWeights(json['type_weights']),
      progressValue: _toDouble(json['progress_value']),
      progressPercent: (json['progress_percent'] as num?)?.toInt() ?? 0,
      status: json['status']?.toString() ?? 'on_track',
    );
  }
}

/// One row of the attainment board.
///
/// Narrowed server-side by the same rule as the list, so a member sees their
/// own standing and their teams', not the whole org's. [user] is a name: the
/// endpoint used to send the person's email address here and again under an
/// `email` key, and both clients printed the address.
class GoalLeaderRow {
  const GoalLeaderRow({
    required this.rank,
    required this.goalId,
    required this.goalName,
    required this.user,
    required this.target,
    required this.achieved,
    required this.percent,
  });

  final int rank;
  final String goalId;
  final String goalName;
  final String user;
  final double target;
  final double achieved;

  /// Uncapped, unlike `SalesGoal.progressPercent`, which the model caps at 100.
  /// 104% is the interesting number on a board.
  final int percent;

  factory GoalLeaderRow.fromJson(Map<String, dynamic> json) {
    final user = json['user'] as Map<String, dynamic>?;
    return GoalLeaderRow(
      rank: (json['rank'] as num?)?.toInt() ?? 0,
      goalId: json['goal_id']?.toString() ?? '',
      goalName: json['goal_name']?.toString() ?? '',
      user: user?['name']?.toString() ?? 'Unknown',
      target: _toDouble(json['target']),
      achieved: _toDouble(json['achieved']),
      percent: (json['percent'] as num?)?.toInt() ?? 0,
    );
  }
}

/// The header numbers. There is no goals-summary endpoint, so these are
/// arithmetic over rows already fetched, never a second request.
class GoalTotals {
  const GoalTotals({
    this.count = 0,
    this.active = 0,
    this.target = 0,
    this.achieved = 0,
    this.behind = 0,
  });

  /// Every goal, active or not.
  final int count;

  final int active;

  /// Summed over the ACTIVE goals only, matching the "Active goals only" note
  /// the header carries. A retired goal's target is not something anyone is
  /// still working towards.
  final double target;
  final double achieved;

  /// Active goals behind pace whose period has not ended. An ended goal is
  /// settled: nobody can influence it, so listing it as a thing to worry about
  /// is noise.
  final int behind;
}

/// Totals over [goals], as of [today] (`YYYY-MM-DD`).
///
/// [today] is injected rather than read from the clock so the boundary case,
/// a goal whose period ends today, can be tested at all. It counts: the web
/// compared `period_end` as a UTC instant against `Date.now()` and so dropped a
/// goal out of `behind` part-way through its own final day.
GoalTotals goalTotals(List<SalesGoal> goals, {required String today}) {
  final active = goals.where((g) => g.isActive).toList();
  return GoalTotals(
    count: goals.length,
    active: active.length,
    target: active.fold(0, (sum, g) => sum + g.targetValue),
    achieved: active.fold(0, (sum, g) => sum + g.progressValue),
    behind: active
        .where((g) => g.status == 'behind' && g.periodEnd.compareTo(today) >= 0)
        .length,
  );
}

/// Today as `YYYY-MM-DD` in the device's own timezone.
///
/// Not `toIso8601String()` on a UTC value: that hands back yesterday for
/// anywhere east of Greenwich for part of every day, and these are date-only
/// fields where a day matters.
String goalToday([DateTime? now]) {
  String two(int n) => n.toString().padLeft(2, '0');
  final d = now ?? DateTime.now();
  return '${d.year.toString().padLeft(4, '0')}-${two(d.month)}-${two(d.day)}';
}

const Map<String, int> _statusRank = {
  'behind': 0,
  'at_risk': 1,
  'on_track': 2,
  'completed': 3,
};

/// Active first, then most urgent, then furthest along. Matches the web's
/// order so the same org reads the same way on either client.
List<SalesGoal> sortGoalsByUrgency(List<SalesGoal> goals) {
  final sorted = [...goals];
  sorted.sort((a, b) {
    final byActive = (b.isActive ? 1 : 0) - (a.isActive ? 1 : 0);
    if (byActive != 0) return byActive;
    final byStatus =
        (_statusRank[a.status] ?? 9) - (_statusRank[b.status] ?? 9);
    if (byStatus != 0) return byStatus;
    return b.progressPercent - a.progressPercent;
  });
  return sorted;
}

/// What the form sends, and the two rules the serializer enforces.
///
/// A UX hint, never the guard: `SalesGoalCreateSerializer.validate` rejects
/// both of these itself, and it is the only thing a caller with curl meets.
/// Saying it here means somebody is told before a round trip rather than after.
///
/// Returns null when the values are acceptable, otherwise the one message to
/// show. Name is checked too, because the model has `blank=False` and a blank
/// one comes back as a field error rather than something readable.
String? validateGoalForm({
  required String name,
  required String targetValue,
  required String periodStart,
  required String periodEnd,
}) {
  if (name.trim().isEmpty) return 'Give the goal a name.';

  final target = double.tryParse(targetValue.trim());
  if (target == null) return 'Target must be a number.';
  // `> 0`, matching the serializer. Zero is rejected there too, and a goal of
  // zero is met before it starts.
  if (target <= 0) return 'Target must be greater than 0.';

  if (periodStart.isEmpty || periodEnd.isEmpty) {
    return 'Pick a start and an end date.';
  }
  // The serializer rejects `period_end <= period_start`, so equal dates are
  // refused as well as reversed ones. A one-day goal is not expressible here
  // and the message must not imply it is.
  if (periodEnd.compareTo(periodStart) <= 0) {
    return 'The end date must be after the start date.';
  }
  return null;
}

double _toDouble(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0;
}

String _personName(Map<String, dynamic> profile) {
  final details = profile['user_details'] as Map<String, dynamic>?;
  final name = details?['name']?.toString() ?? '';
  if (name.trim().isNotEmpty) return name.trim();
  final email = details?['email']?.toString() ?? '';
  return email.isNotEmpty ? email : 'Unnamed';
}

/// A period as "1 May - 31 May", from the two date-only strings the API sends.
///
/// Parsed rather than reformatted through DateTime.toLocal(): these are
/// date-only fields, and a timezone conversion can move a period boundary by a
/// day. Anything that does not parse is handed back as it arrived, so a
/// malformed row shows the raw value instead of a wrong date.
String goalDateRange(String start, String end) {
  final from = _shortDate(start);
  final to = _shortDate(end);
  if (from == null || to == null) return '$start to $end';
  return '$from - $to';
}

const List<String> _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

String? _shortDate(String isoDate) {
  final parts = isoDate.split('-');
  if (parts.length != 3) return null;
  final month = int.tryParse(parts[1]);
  final day = int.tryParse(parts[2]);
  if (month == null || day == null || month < 1 || month > 12) return null;
  return '$day ${_months[month - 1]}';
}

/// The stored deal type weights, narrowed to the numbers they must be.
///
/// The column is free-form JSON, and the write serializer is what guarantees
/// its shape, so a row written before that validator existed (or by hand in a
/// shell) can still hold a string or a null. Anything that is not a number is
/// dropped rather than coerced: a weight of "heavy" is not a weight of zero,
/// and quietly inventing one would misreport somebody's quota.
Map<String, num> _toWeights(dynamic value) {
  if (value is! Map) return const {};
  final weights = <String, num>{};
  value.forEach((key, weight) {
    if (weight is num) weights[key.toString()] = weight;
  });
  return weights;
}

/// One finished period from `/api/opportunities/goals/history/`.
///
/// A row is a period AND a goal type. Grouping on the period alone pooled a
/// revenue target in currency with a deals-closed target in deals and printed
/// the sum as money, so the API groups by both and this carries the type that
/// decides how the figures are formatted.
///
/// [percent] is uncapped, unlike `SalesGoal.progressPercent`: a settled period
/// that came in 25% over is a different result from one that landed exactly on
/// target, and the difference is final.
class GoalHistoryPeriod {
  const GoalHistoryPeriod({
    required this.periodStart,
    required this.periodEnd,
    required this.periodType,
    required this.goalType,
    required this.goalsCount,
    required this.attainedCount,
    required this.target,
    required this.achieved,
    required this.percent,
    required this.goals,
  });

  final String periodStart;
  final String periodEnd;
  final String periodType;
  final String goalType;

  /// Goals in this period, and how many of them reached their own target.
  /// Counted per goal rather than off the pooled total: three reps, two of whom
  /// missed while the third doubled up, is not "the team made its number".
  final int goalsCount;
  final int attainedCount;

  final double target;
  final double achieved;
  final int percent;
  final List<SalesGoal> goals;

  /// Same rule as [SalesGoal.isMoney], applied to the period's own type.
  bool get isMoney => goalType == 'REVENUE';

  factory GoalHistoryPeriod.fromJson(Map<String, dynamic> json) {
    final goals = json['goals'] as List<dynamic>? ?? const [];
    return GoalHistoryPeriod(
      periodStart: json['period_start']?.toString() ?? '',
      periodEnd: json['period_end']?.toString() ?? '',
      periodType: json['period_type']?.toString() ?? 'MONTHLY',
      goalType: json['goal_type']?.toString() ?? 'REVENUE',
      goalsCount: (json['goals_count'] as num?)?.toInt() ?? 0,
      attainedCount: (json['attained_count'] as num?)?.toInt() ?? 0,
      target: _toDouble(json['target']),
      achieved: _toDouble(json['achieved']),
      percent: (json['percent'] as num?)?.toInt() ?? 0,
      goals: goals
          .whereType<Map<String, dynamic>>()
          .map(SalesGoal.fromJson)
          .toList(),
    );
  }
}
