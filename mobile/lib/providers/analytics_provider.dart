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
  final String? error;

  const AnalyticsDashboard({
    this.frt,
    this.mttr,
    this.backlog,
    this.agents = const [],
    this.sla,
    this.nrt,
    this.csat,
    this.isLoading = false,
    this.error,
  });
}

class AnalyticsNotifier extends Notifier<AnalyticsDashboard> {
  final ApiService _api = ApiService();
  AnalyticsQuery _query = const AnalyticsQuery();

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

  Future<void> _load() async {
    state = AnalyticsDashboard(isLoading: true, agents: state.agents);
    final params = _query.toParams();
    String build(String url) => params.isEmpty
        ? url
        : Uri.parse(url).replace(queryParameters: params).toString();

    final results = await Future.wait([
      _api.get(build(ApiConfig.analyticsFrt)),
      _api.get(build(ApiConfig.analyticsMttr)),
      _api.get(build(ApiConfig.analyticsBacklog)),
      _api.get(build(ApiConfig.analyticsAgents)),
      _api.get(build(ApiConfig.analyticsSla)),
      _api.get(build(ApiConfig.analyticsNrt)),
      _api.get(build(ApiConfig.csatAggregate)),
    ]);

    String? error;
    for (final r in results) {
      if (!r.success) error = r.message ?? 'Failed to load analytics';
    }

    final agentsResp = results[3];
    final agents = (agentsResp.data?['results'] as List<dynamic>? ?? [])
        .whereType<Map<String, dynamic>>()
        .toList();

    state = AnalyticsDashboard(
      frt: results[0].data,
      mttr: results[1].data,
      backlog: results[2].data,
      agents: agents,
      sla: results[4].data,
      nrt: results[5].data,
      csat: results[6].data,
      isLoading: false,
      error: error,
    );
  }
}

final analyticsProvider =
    NotifierProvider<AnalyticsNotifier, AnalyticsDashboard>(
      AnalyticsNotifier.new,
    );
