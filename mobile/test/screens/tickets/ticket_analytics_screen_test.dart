import 'package:bottle_crm/providers/analytics_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_analytics_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The ticket analytics dashboard at a real phone width.
///
/// Rendered at 390px and at a raised text scale because the metric tiles used
/// to sit in a grid with a fixed aspect ratio, and a tile whose height is fixed
/// while its text grows is exactly what overflows. An overflow is a thrown
/// FlutterError, so `takeException()` being null is the assertion.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<void> pump(WidgetTester tester, AnalyticsDashboard data) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [analyticsProvider.overrideWith(() => _FakeAnalytics(data))],
        child: const MaterialApp(home: TicketAnalyticsScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// The dashboard is a lazy ListView, so anything below the fold is not built
  /// until it is scrolled to.
  Future<void> scrollTo(WidgetTester tester, Finder finder) async {
    await tester.scrollUntilVisible(
      finder,
      200,
      scrollable: find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits a 390px phone at text scale $scale', (tester) async {
      usePhone(tester, textScale: scale);
      await pump(tester, _full);

      expect(tester.takeException(), isNull);

      // Next response sits beside first response, with its own breaches.
      expect(find.text('NEXT RESPONSE'), findsOneWidget);
      expect(find.text('2.5h'), findsOneWidget);
      expect(find.text('p90 6.0h · 14 answered'), findsOneWidget);
      expect(find.text('3 breached'), findsOneWidget);
      expect(find.text('5 breached'), findsOneWidget);

      // The SLA tile carries all three rates.
      expect(
        find.text(
          'First response above · Resolution 10.0% · Next response 7.5%',
        ),
        findsOneWidget,
      );

      await scrollTo(tester, find.text('1 (5%)').last);
      expect(tester.takeException(), isNull);
      expect(find.text('CUSTOMER SATISFACTION'), findsOneWidget);
      expect(find.text('4.2'), findsOneWidget);
      expect(find.text('out of 5 · 20 ratings'), findsOneWidget);
      // Five labelled bars, each with its share of the count.
      for (final label in ['5', '4', '3', '2', '1']) {
        expect(find.text(label), findsWidgets);
      }
      expect(find.text('10 (50%)'), findsOneWidget);
      expect(find.text('6 (30%)'), findsOneWidget);
      expect(find.text('2 (10%)'), findsOneWidget);
      expect(find.text('1 (5%)'), findsNWidgets(2));
      expect(find.byType(FractionallySizedBox), findsNWidgets(5));
    });
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('says so when nobody rated, at text scale $scale', (
      tester,
    ) async {
      usePhone(tester, textScale: scale);
      await pump(
        tester,
        const AnalyticsDashboard(
          frt: _frt,
          nrt: _nrt,
          sla: _sla,
          csat: {
            'average': null,
            'count': 0,
            'distribution': {'1': 0, '2': 0, '3': 0, '4': 0, '5': 0},
          },
        ),
      );

      final empty = find.textContaining('No ratings in this window.');
      await scrollTo(tester, empty);
      expect(tester.takeException(), isNull);
      expect(empty, findsOneWidget);
      // No bars and no average: a 0.0 would read as a terrible score.
      expect(find.byType(FractionallySizedBox), findsNothing);
      expect(find.textContaining('out of 5'), findsNothing);
    });
  }

  testWidgets('an endpoint that sent nothing reads as no data, not a crash', (
    tester,
  ) async {
    usePhone(tester, textScale: 1.3);
    await pump(tester, const AnalyticsDashboard(frt: _frt));

    expect(tester.takeException(), isNull);
    expect(find.text('NEXT RESPONSE'), findsOneWidget);
    expect(find.textContaining(' · 0 answered'), findsOneWidget);
    expect(find.text('0 breached'), findsOneWidget);
  });
}

const _frt = {
  'median_hours': 1.25,
  'p90_hours': 4.0,
  'count': 40,
  'breach_count': 5,
};

const _nrt = {
  'median_hours': 2.5,
  'p90_hours': 6.0,
  'count': 14,
  'breach_count': 3,
};

const _sla = {
  'frt_breach_rate': 0.125,
  'resolution_breach_rate': 0.1,
  'nrt_breach_rate': 0.075,
};

const _full = AnalyticsDashboard(
  frt: _frt,
  nrt: _nrt,
  mttr: {'median_hours': 30.0, 'p90_hours': 70.0, 'count': 22},
  backlog: {
    'series': [
      {'open_count': 12, 'urgent_count': 3},
    ],
  },
  sla: _sla,
  csat: {
    'average': 4.2,
    'count': 20,
    'distribution': {'1': 1, '2': 1, '3': 2, '4': 6, '5': 10},
  },
  agents: [
    {
      'email': 'somebody.with.a.very.long.address@example.com',
      'handled': 12,
      'avg_frt_hours': 0.5,
      'breach_rate': 0.25,
    },
  ],
);

class _FakeAnalytics extends AnalyticsNotifier {
  _FakeAnalytics(this._data);

  final AnalyticsDashboard _data;

  @override
  AnalyticsDashboard build() => _data;

  /// The screen applies its date filter on the first frame; with no server
  /// behind the test that must not replace the fixture.
  @override
  Future<void> setQuery(AnalyticsQuery query) async {}
}
