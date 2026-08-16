import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/sales_goal.dart';
import '../services/api_service.dart';

export '../services/api_service.dart' show ApiResponse;

/// What the goals list is narrowed to.
///
/// Held here rather than in the screen so a refresh keeps the filter, and so
/// the query string has one definition that a test can pin.
class GoalFilters {
  const GoalFilters({this.query = '', this.periodType, this.window = ''});

  final String query;
  final String? periodType;

  /// `''` for every goal, `current` for the ones whose period contains today,
  /// `active` for the ones nobody has paused. Two separate API flags, because
  /// they mean different things: a paused goal can still be in its period.
  final String window;

  bool get isFiltered =>
      query.trim().isNotEmpty ||
      (periodType ?? '').isNotEmpty ||
      window.isNotEmpty;

  GoalFilters copyWith({String? query, String? periodType, String? window}) =>
      GoalFilters(
        query: query ?? this.query,
        // Passing null clears the period, so it cannot ride copyWith's usual
        // "null means unchanged" convention. The screen sends '' to clear.
        periodType: periodType == '' ? null : (periodType ?? this.periodType),
        window: window ?? this.window,
      );
}

/// The list query for a set of filters.
///
/// `period_type` and `window` are checked against their allowed sets rather
/// than forwarded. They come from pickers today, so nothing can currently send
/// a bad one, and a filter that pasted anything through would turn a later typo
/// into an empty list with no explanation. `search` is free text and goes
/// through [Uri], which encodes it.
///
/// `limit=1000` fetches the whole (small) set so the totals cover every goal
/// rather than one page of them.
String goalListQuery(GoalFilters filters) {
  final params = <String, String>{'limit': '1000'};
  final search = filters.query.trim();
  if (search.isNotEmpty) params['search'] = search;
  final period = filters.periodType ?? '';
  if (goalPeriodTypes.contains(period)) params['period_type'] = period;
  if (filters.window == 'current') params['current'] = 'true';
  if (filters.window == 'active') params['active'] = 'true';
  return Uri(queryParameters: params).query;
}

/// The goals screen's data: the list, the board, and the derived totals.
class GoalsData {
  const GoalsData({
    this.goals = const [],
    this.leaderboard = const [],
    this.totals = const GoalTotals(),
    this.filters = const GoalFilters(),
  });

  final List<SalesGoal> goals;
  final List<GoalLeaderRow> leaderboard;
  final GoalTotals totals;

  /// Echoed back so the screen can tell "no goals here" from "no goals match
  /// this filter". Saying the first when the second is true sends somebody off
  /// to create a duplicate of a goal they already have.
  final GoalFilters filters;
}

/// Sales goals, plus the attainment board.
///
/// Two requests, in parallel, because there is no endpoint that answers both
/// and the board is not derivable from the list: it ranks on an uncapped
/// percentage the list's `progress_percent` caps at 100.
///
/// The board failing must not take the page down with it. It is the smaller
/// half of the screen and a goals list with no ranking beside it is still the
/// thing somebody opened this for, so its failure degrades to an empty board.
/// The list failing is a real failure and surfaces as one.
class GoalsNotifier extends AsyncNotifier<GoalsData> {
  final ApiService _apiService = ApiService();

  GoalFilters _filters = const GoalFilters();

  @override
  Future<GoalsData> build() => _fetch();

  Future<GoalsData> _fetch() async {
    final results = await Future.wait([
      _apiService.get('${ApiConfig.goals}?${goalListQuery(_filters)}'),
      _apiService.get('${ApiConfig.goalsLeaderboard}?period_type=MONTHLY'),
    ]);

    final listResponse = results[0];
    if (!listResponse.success || listResponse.data == null) {
      throw Exception(listResponse.message ?? 'Could not load the goals.');
    }

    final goals = sortGoalsByUrgency(
      listFromEnvelope(listResponse.data!, const [
        'goals',
      ]).map(SalesGoal.fromJson).toList(),
    );

    final boardResponse = results[1];
    final leaderboard = boardResponse.success && boardResponse.data != null
        ? listFromEnvelope(boardResponse.data!, const [
            'leaderboard',
          ]).map(GoalLeaderRow.fromJson).toList()
        : <GoalLeaderRow>[];

    return GoalsData(
      goals: goals,
      leaderboard: leaderboard,
      totals: goalTotals(goals, today: goalToday()),
      filters: _filters,
    );
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Narrow the list. The API does the filtering, so this refetches rather than
  /// sifting the rows already on screen: a search that only looked at the
  /// fetched page would quietly disagree with the same search on the web.
  Future<void> applyFilters(GoalFilters filters) async {
    _filters = filters;
    await refresh();
  }

  /// `POST /api/opportunities/goals/`. Admin-only server-side.
  Future<ApiResponse<Map<String, dynamic>>> createGoal(
    Map<String, dynamic> body,
  ) => _write(() => _apiService.post(ApiConfig.goals, body));

  /// `PUT /api/opportunities/goals/<id>/`. Admin-only, and shares the write
  /// serializer (so also the org check on `assigned_to` and `team`) with create.
  Future<ApiResponse<Map<String, dynamic>>> updateGoal(
    String id,
    Map<String, dynamic> body,
  ) => _write(() => _apiService.put(ApiConfig.goal(id), body));

  /// `DELETE /api/opportunities/goals/<id>/`. Admin-only. A hard delete: the
  /// row goes, which is why the screen confirms first.
  Future<ApiResponse<Map<String, dynamic>>> deleteGoal(String id) =>
      _write(() => _apiService.delete(ApiConfig.goal(id)));

  /// Every write refetches on success rather than patching locally: creating or
  /// editing a goal changes `progress_value`, `progress_percent` and `status`,
  /// and all three are computed server-side from opportunities this app cannot
  /// see. Guessing them would put a wrong number on screen.
  Future<ApiResponse<Map<String, dynamic>>> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() send,
  ) async {
    try {
      final response = await send();
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final goalsProvider = AsyncNotifierProvider<GoalsNotifier, GoalsData>(
  GoalsNotifier.new,
);

/// Finished goal periods with what each one attained, newest first.
///
/// A separate provider rather than a third leg of `GoalsNotifier._fetch`: the
/// goals screen never shows history, so folding it in would make every open of
/// that screen pay for a request nobody on it reads.
final goalHistoryProvider = FutureProvider<List<GoalHistoryPeriod>>((
  ref,
) async {
  final response = await ApiService().get(ApiConfig.goalsHistory);
  if (!response.success || response.data == null) {
    throw Exception(response.message ?? 'Could not load goal history.');
  }
  return listFromEnvelope(response.data!, const [
    'history',
  ]).map(GoalHistoryPeriod.fromJson).toList();
});

/// Turn the form's weight boxes into the map the write serializer takes.
///
/// A blank box is not a weight of zero, it is "do not re-weigh this type", so
/// it stays out of the map. Anything non-numeric is passed through as typed
/// rather than coerced: the backend validator names the offending type in its
/// 400, and turning "heavy" into 0 would store a quota nobody asked for.
///
/// Always sent on a write, even when empty, so clearing a box actually clears
/// the weight. PUT is partial, so an omitted key would keep the old map and a
/// weight could never be removed.
Map<String, dynamic> goalWeightFields(Map<String, String> entered) {
  final weights = <String, dynamic>{};
  for (final type in dealTypes) {
    final raw = entered[type]?.trim() ?? '';
    if (raw.isEmpty) continue;
    final parsed = num.tryParse(raw);
    weights[type] = parsed ?? raw;
  }
  return {'type_weights': weights};
}

/// One goal for the edit form.
///
/// Fetched rather than taken from the list so the form opens on what the server
/// holds now, and so a goal reached by a stale link 404s here instead of being
/// silently drawn from an old cache.
final goalProvider = FutureProvider.family<SalesGoal, String>((ref, id) async {
  final response = await ApiService().get(ApiConfig.goal(id));
  if (!response.success || response.data == null) {
    throw Exception(response.message ?? 'Could not load that goal.');
  }
  return SalesGoal.fromJson(response.data!);
});

/// Turn the form's single target choice into the pair of FKs the API takes.
///
/// One picker, decoded here into exactly one of `assigned_to` / `team` (or
/// neither, for a whole-org goal), so nothing this app sends can set both.
/// Both are explicitly nulled rather than omitted: PUT is partial, so leaving a
/// key out keeps the old value, and switching a goal from a person to a team
/// would otherwise leave it assigned to both.
Map<String, dynamic> goalTargetFields(String? target) {
  if (target != null && target.startsWith('profile:')) {
    return {'assigned_to': target.substring('profile:'.length), 'team': null};
  }
  if (target != null && target.startsWith('team:')) {
    return {'assigned_to': null, 'team': target.substring('team:'.length)};
  }
  return {'assigned_to': null, 'team': null};
}

/// The form's target value for an existing goal, the inverse of the above.
String goalTargetValue(SalesGoal goal) {
  if ((goal.assignedToId ?? '').isNotEmpty) {
    return 'profile:${goal.assignedToId}';
  }
  if ((goal.teamId ?? '').isNotEmpty) return 'team:${goal.teamId}';
  return 'org';
}
