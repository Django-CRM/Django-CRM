import 'dart:convert';

import 'package:bottle_crm/providers/analytics_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_analytics_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// One failing analytics call used to blank the whole dashboard: the provider
/// kept a single `error`, and the screen showed only that. Each section now
/// loads, fails and retries on its own.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AnalyticsNotifier', () {
    late _AnalyticsClient client;
    late ProviderContainer container;

    setUp(() {
      client = _AnalyticsClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
    });

    tearDown(() {
      container.dispose();
      ApiService().setClientForTesting(http.Client());
    });

    Future<AnalyticsDashboard> load() async {
      // Let the load `build()` schedules start first; the one below then
      // supersedes it, which is also the path a filter change takes.
      container.read(analyticsProvider);
      await Future<void>.delayed(Duration.zero);
      await container
          .read(analyticsProvider.notifier)
          .setQuery(const AnalyticsQuery());
      return container.read(analyticsProvider);
    }

    test('one failing call is that section only', () async {
      client.failing.add('/cases/analytics/frt/');

      final data = await load();

      expect(data.isLoading, isFalse);
      expect(data.errors.keys, [AnalyticsSection.frt]);
      expect(data.errors[AnalyticsSection.frt], 'Server exploded');
      expect(data.frt, isNull);
      // Everything else still arrived.
      expect(data.nrt?['count'], 7);
      expect(data.mttr?['count'], 7);
      expect(data.backlog?['series'], isNotEmpty);
      expect(data.sla?['frt_breach_rate'], 0.5);
      expect(data.csat?['count'], 7);
      expect(data.agents.single['email'], 'a@example.com');
    });

    test('retry fetches that section alone and clears its error', () async {
      client.failing.add('/cases/csat/aggregate/');
      await load();
      expect(container.read(analyticsProvider).errors.keys, [
        AnalyticsSection.csat,
      ]);

      client.failing.clear();
      client.paths.clear();
      await container
          .read(analyticsProvider.notifier)
          .retry(AnalyticsSection.csat);

      final data = container.read(analyticsProvider);
      expect(client.paths, ['/cases/csat/aggregate/']);
      expect(data.errors, isEmpty);
      expect(data.csat?['count'], 7);
      expect(data.frt?['count'], 7);
    });

    test('a failing agents call keeps no stale rows', () async {
      client.failing.add('/cases/analytics/agents/');

      final data = await load();

      expect(data.agents, isEmpty);
      expect(data.errors.keys, [AnalyticsSection.agents]);
    });

    test('everything answering is no error at all', () async {
      final data = await load();
      expect(data.errors, isEmpty);
    });
  });

  group('TicketAnalyticsScreen at 390px', () {
    void usePhone(WidgetTester tester, {double textScale = 1.0}) {
      tester.view.devicePixelRatio = 3.0;
      tester.view.physicalSize = const Size(390 * 3, 844 * 3);
      tester.platformDispatcher.textScaleFactorTestValue = textScale;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    }

    for (final scale in [1.0, 1.3]) {
      testWidgets('a failed section says so and the rest render, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        final fake = _FakeAnalytics(
          const AnalyticsDashboard(
            nrt: {
              'median_hours': 2.5,
              'p90_hours': 6.0,
              'count': 14,
              'breach_count': 3,
            },
            errors: {AnalyticsSection.frt: 'First response did not load.'},
          ),
        );
        await tester.pumpWidget(
          ProviderScope(
            overrides: [analyticsProvider.overrideWith(() => fake)],
            child: const MaterialApp(home: TicketAnalyticsScreen()),
          ),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        expect(find.text('First response did not load.'), findsOneWidget);
        // The neighbouring tile still shows its figures.
        expect(find.text('2.5h'), findsOneWidget);
        expect(find.text('p90 6.0h · 14 answered'), findsOneWidget);
        // The failed tile does not pretend to zero breaches.
        expect(find.text('0 breached'), findsNothing);

        final retry = find.text('Retry');
        expect(retry, findsOneWidget);
        expect(tester.getSize(find.byType(TextButton)).height, greaterThan(43));
        await tester.tap(retry);
        await tester.pumpAndSettle();
        expect(fake.retried, [AnalyticsSection.frt]);
      });
    }
  });
}

/// Every analytics endpoint answers 200 with a small body, except the paths in
/// [failing], which answer 500.
class _AnalyticsClient extends http.BaseClient {
  final Set<String> failing = {};
  final List<String> paths = [];

  static const _bodies = <String, Map<String, dynamic>>{
    '/cases/analytics/frt/': {'count': 7, 'median_hours': 1.0},
    '/cases/analytics/nrt/': {'count': 7, 'median_hours': 1.0},
    '/cases/analytics/mttr/': {'count': 7, 'median_hours': 1.0},
    '/cases/analytics/backlog/': {
      'series': [
        {'open_count': 3, 'urgent_count': 1},
      ],
    },
    '/cases/analytics/sla/': {'frt_breach_rate': 0.5},
    '/cases/csat/aggregate/': {'count': 7, 'average': 4.0},
    '/cases/analytics/agents/': {
      'results': [
        {'email': 'a@example.com', 'handled': 2},
      ],
    },
  };

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final path = request.url.path.replaceFirst(RegExp(r'^/api'), '');
    paths.add(path);
    final fail = failing.contains(path);
    final body = fail
        ? const {'detail': 'Server exploded'}
        : (_bodies[path] ?? const {});
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      fail ? 500 : 200,
      request: request,
    );
  }
}

class _FakeAnalytics extends AnalyticsNotifier {
  _FakeAnalytics(this._data);

  final AnalyticsDashboard _data;
  final List<AnalyticsSection> retried = [];

  @override
  AnalyticsDashboard build() => _data;

  @override
  Future<void> setQuery(AnalyticsQuery query) async {}

  @override
  Future<void> retry(AnalyticsSection section) async => retried.add(section);
}
