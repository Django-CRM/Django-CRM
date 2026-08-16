import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/time_report.dart';
import '../services/api_service.dart';

/// What the report is being asked for: a window, a grouping, a billable
/// filter.
///
/// Held apart from the loaded data for the same reason the timesheet's range
/// is: the filters the user picked have to survive a failed load, and folding
/// them into the response would snap the pickers back on every network error.
class TimeReportFilters {
  const TimeReportFilters({
    required this.start,
    required this.end,
    this.groupBy = 'agent',
    this.billable,
  });

  final DateTime start;
  final DateTime end;

  /// One of agent/ticket/account. The API rejects anything else, so the
  /// picker's values and this string are the same short list.
  final String groupBy;

  /// 'true', 'false', or null for everything. Null is not the same as 'false':
  /// one asks for all time, the other for non-billable time only.
  final String? billable;

  /// The last 30 days, matching what the API picks when asked without a
  /// window, so the screen opens on the same report either way.
  factory TimeReportFilters.recent() {
    final now = DateTime.now();
    final end = DateTime(now.year, now.month, now.day);
    return TimeReportFilters(
      start: end.subtract(const Duration(days: 29)),
      end: end,
    );
  }

  TimeReportFilters copyWith({
    DateTime? start,
    DateTime? end,
    String? groupBy,
    String? billable,
    bool clearBillable = false,
  }) {
    return TimeReportFilters(
      start: start ?? this.start,
      end: end ?? this.end,
      groupBy: groupBy ?? this.groupBy,
      billable: clearBillable ? null : (billable ?? this.billable),
    );
  }

  String get startParam => _param(start);
  String get endParam => _param(end);

  static String _param(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';
}

/// The filters the report screen is on. Changing these refetches, because
/// [TimeReportNotifier.build] watches them.
class TimeReportFiltersNotifier extends Notifier<TimeReportFilters> {
  @override
  TimeReportFilters build() => TimeReportFilters.recent();

  void setGroupBy(String groupBy) => state = state.copyWith(groupBy: groupBy);

  void setBillable(String? billable) => state = billable == null
      ? state.copyWith(clearBillable: true)
      : state.copyWith(billable: billable);

  void setWindow(DateTime start, DateTime end) =>
      state = state.copyWith(start: start, end: end);
}

final timeReportFiltersProvider =
    NotifierProvider<TimeReportFiltersNotifier, TimeReportFilters>(
      TimeReportFiltersNotifier.new,
    );

/// Totals over a window, grouped.
///
/// Read-only. Who may see what is the server's call: an agent's report covers
/// their own time and an admin's covers the org, which is why nothing here
/// sends a `profile` and nothing here filters by one.
class TimeReportNotifier extends AsyncNotifier<TimeReport> {
  final ApiService _apiService = ApiService();

  @override
  Future<TimeReport> build() {
    final filters = ref.watch(timeReportFiltersProvider);
    return _fetch(filters);
  }

  Future<TimeReport> _fetch(TimeReportFilters filters) async {
    final response = await _apiService.get(
      ApiConfig.timeReport(
        start: filters.startParam,
        end: filters.endParam,
        groupBy: filters.groupBy,
        billable: filters.billable,
      ),
    );
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load the time report.');
    }
    return TimeReport.fromJson(response.data!);
  }

  Future<void> refresh() async {
    final filters = ref.read(timeReportFiltersProvider);
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(filters));
  }
}

final timeReportProvider =
    AsyncNotifierProvider<TimeReportNotifier, TimeReport>(
      TimeReportNotifier.new,
    );
