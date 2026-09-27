import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/timesheet.dart';
import '../services/api_service.dart';
import '../services/org_date.dart';

export '../services/api_service.dart' show ApiResponse;

/// The Mon..Sun week the timesheet is showing, as an inclusive pair of dates.
///
/// Held apart from the loaded data so the week the user asked for survives a
/// failed or in-flight load. Folding it into the response would mean a network
/// error silently snapping the header back to the current week.
class TimesheetRange {
  const TimesheetRange(this.start, this.end);

  final DateTime start;
  final DateTime end;

  /// The ISO week containing [day]: Monday through Sunday.
  ///
  /// `weekday` is 1..7 with Monday at 1, so subtracting `weekday - 1` reaches
  /// Monday for every day including Sunday, which a `% 7` on a Sunday-first
  /// index would get wrong.
  ///
  /// Calendar arithmetic ([addDays]), not `Duration`: a week that crosses a
  /// DST change is not 7 x 24 hours, so a `Duration` step can land on the
  /// wrong day.
  factory TimesheetRange.weekOf(DateTime day) {
    final monday = addDays(day, 1 - day.weekday);
    return TimesheetRange(monday, addDays(monday, 6));
  }

  TimesheetRange shift(int days) =>
      TimesheetRange(addDays(start, days), addDays(end, days));

  /// Whether this is the week the org's today falls in, so the screen can
  /// hide a "This week" control that would do nothing.
  bool get isCurrent => TimesheetRange.weekOf(orgToday()).start == start;

  String get startParam => _param(start);
  String get endParam => _param(end);

  static String _param(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';
}

/// Which week the timesheet screen is on. Changing this refetches, because
/// [TimesheetNotifier.build] watches it.
class TimesheetRangeNotifier extends Notifier<TimesheetRange> {
  @override
  TimesheetRange build() => TimesheetRange.weekOf(orgToday());

  void shift(int days) => state = state.shift(days);

  void thisWeek() => state = TimesheetRange.weekOf(orgToday());
}

final timesheetRangeProvider =
    NotifierProvider<TimesheetRangeNotifier, TimesheetRange>(
      TimesheetRangeNotifier.new,
    );

/// One week of the caller's own time.
///
/// Read-only apart from one write: stopping a running timer. Starting a timer
/// and logging manual time both belong to a ticket, and live on the ticket
/// screen, because an entry with no ticket is not something this API can
/// store.
class TimesheetNotifier extends AsyncNotifier<TimesheetWeek> {
  final ApiService _apiService = ApiService();

  @override
  Future<TimesheetWeek> build() {
    // Watched, not read: picking a different week is what refetches, and this
    // is the whole of that wiring.
    final range = ref.watch(timesheetRangeProvider);
    return _fetch(range);
  }

  Future<TimesheetWeek> _fetch(TimesheetRange range) async {
    final response = await _apiService.get(
      ApiConfig.timesheet(start: range.startParam, end: range.endParam),
    );
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load your timesheet.');
    }
    return TimesheetWeek.fromJson(response.data!);
  }

  Future<void> refresh() async {
    final range = ref.read(timesheetRangeProvider);
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(range));
  }

  /// Stop a running timer.
  ///
  /// Ownership is the server's call: the endpoint is org-scoped and refuses an
  /// entry that is neither yours nor visible to you as an admin. Nothing is
  /// re-checked here and this must not be read as having checked it.
  ///
  /// The week is refetched on success rather than patched locally, because
  /// stopping settles `duration_minutes`, which only the server computes.
  Future<ApiResponse<Map<String, dynamic>>> stopTimer(String entryId) async {
    if (entryId.isEmpty) {
      // An empty id would POST to `/time-entries//stop/`, a different path
      // whose failure reads as a routing bug rather than a missing argument.
      return const ApiResponse(
        success: false,
        message: 'Which timer? No entry was given.',
        statusCode: 0,
      );
    }
    try {
      final response = await _apiService.post(
        ApiConfig.timeEntryStop(entryId),
        const {},
      );
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final timesheetProvider =
    AsyncNotifierProvider<TimesheetNotifier, TimesheetWeek>(
      TimesheetNotifier.new,
    );
