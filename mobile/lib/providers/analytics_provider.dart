import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../services/api_service.dart';

/// Filter bundle for the analytics dashboard. All fields optional; backend
/// defaults to "last 30 days" when window is missing.
class AnalyticsQuery {
  final DateTime? from;
  final DateTime? to;
  final String? priority;
  final String? agentId;
  final String? teamId;

  const AnalyticsQuery({
    this.from,
    this.to,
    this.priority,
    this.agentId,
    this.teamId,
  });

  Map<String, String> toParams() {
    final p = <String, String>{};
    String d(DateTime t) =>
        '${t.year.toString().padLeft(4, '0')}-'
        '${t.month.toString().padLeft(2, '0')}-'
        '${t.day.toString().padLeft(2, '0')}';
    if (from != null) p['from'] = d(from!);
    if (to != null) p['to'] = d(to!);
    if (priority != null) p['priority'] = priority!;
    if (agentId != null) p['agent'] = agentId!;
    if (teamId != null) p['team'] = teamId!;
    return p;
  }
}

/// The dashboard's sections, one per endpoint. Each loads, fails and retries
/// on its own.
enum AnalyticsSection { frt, nrt, mttr, backlog, sla, csat, agents }

class AnalyticsDashboard {
  final Map<String, dynamic>? frt;
  final Map<String, dynamic>? mttr;
  final Map<String, dynamic>? backlog;
  final List<Map<String, dynamic>> agents;
  final Map<String, dynamic>? sla;

  /// Next response time: waits after the first reply, `compute_nrt` shape.
  final Map<String, dynamic>? nrt;

  /// `{average, count, distribution: {"1".."5": n}}`, windowed by when the
  /// customer answered.
  final Map<String, dynamic>? csat;
  final bool isLoading;

  /// Why a section has no figures, keyed by section. A section absent here
  /// loaded. One failing call used to blank the whole dashboard; now only its
  /// own section says so, with its own retry, and the rest still render.
  final Map<AnalyticsSection, String> errors;

  const AnalyticsDashboard({
    this.frt,
    this.mttr,
    this.backlog,
    this.agents = const [],
    this.sla,
    this.nrt,
    this.csat,
    this.isLoading = false,
    this.errors = const {},
  });

  /// This dashboard with [section] replaced by what [response] carried: its
  /// figures when it succeeded, its error when it did not.
  AnalyticsDashboard withSection(
    AnalyticsSection section,
    ApiResponse<Map<String, dynamic>> response,
  ) {
    final data = response.success ? response.data : null;
    final errors = {...this.errors}..remove(section);
    if (!response.success) {
      errors[section] = response.message ?? 'Could not load this section.';
    }
    List<Map<String, dynamic>> agentsFrom(Map<String, dynamic>? body) =>
        (body?['results'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .toList();
    return AnalyticsDashboard(
      frt: section == AnalyticsSection.frt ? data : frt,
      mttr: section == AnalyticsSection.mttr ? data : mttr,
      backlog: section == AnalyticsSection.backlog ? data : backlog,
      agents: section == AnalyticsSection.agents ? agentsFrom(data) : agents,
      sla: section == AnalyticsSection.sla ? data : sla,
      nrt: section == AnalyticsSection.nrt ? data : nrt,
      csat: section == AnalyticsSection.csat ? data : csat,
      isLoading: isLoading,
      errors: errors,
    );
  }
}

class AnalyticsNotifier extends Notifier<AnalyticsDashboard> {
  final ApiService _api = ApiService();
  AnalyticsQuery _query = const AnalyticsQuery();

  /// Bumped by every full load, so an answer that arrives after the filters
  /// changed is dropped instead of painting the old window's figures.
  int _generation = 0;

  @override
  AnalyticsDashboard build() {
    Future.microtask(_load);
    return const AnalyticsDashboard(isLoading: true);
  }

  Future<void> setQuery(AnalyticsQuery query) async {
    _query = query;
    await _load();
  }

  AnalyticsQuery get query => _query;

  /// Fetch one section again, leaving the others as they are.
  Future<void> retry(AnalyticsSection section) async {
    final generation = _generation;
    final response = await _fetch(section);
    if (generation != _generation) return;
    state = state.withSection(section, response);
  }

  static String _url(AnalyticsSection section) => switch (section) {
    AnalyticsSection.frt => ApiConfig.analyticsFrt,
    AnalyticsSection.nrt => ApiConfig.analyticsNrt,
    AnalyticsSection.mttr => ApiConfig.analyticsMttr,
    AnalyticsSection.backlog => ApiConfig.analyticsBacklog,
    AnalyticsSection.sla => ApiConfig.analyticsSla,
    AnalyticsSection.csat => ApiConfig.csatAggregate,
    AnalyticsSection.agents => ApiConfig.analyticsAgents,
  };

  Future<ApiResponse<Map<String, dynamic>>> _fetch(AnalyticsSection section) {
    final params = _query.toParams();
    final url = _url(section);
    return _api.get(
      params.isEmpty
          ? url
          : Uri.parse(url).replace(queryParameters: params).toString(),
    );
  }

  Future<void> _load() async {
    final generation = ++_generation;
    state = AnalyticsDashboard(isLoading: true, agents: state.agents);
    final sections = AnalyticsSection.values;
    final results = await Future.wait([for (final s in sections) _fetch(s)]);
    if (generation != _generation) return;

    var next = const AnalyticsDashboard();
    for (var i = 0; i < sections.length; i++) {
      next = next.withSection(sections[i], results[i]);
    }
    state = next;
  }
}

final analyticsProvider =
    NotifierProvider<AnalyticsNotifier, AnalyticsDashboard>(
      AnalyticsNotifier.new,
    );
