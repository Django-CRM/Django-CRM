import 'dart:async';
import 'dart:convert';

import 'package:bottle_crm/data/models/record_matches.dart';
import 'package:bottle_crm/providers/custom_objects_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/services/api_contract.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/objects/record_matches_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _id = '11111111-1111-1111-1111-111111111111';
const _a = '22222222-2222-2222-2222-222222222222';
const _b = '33333333-3333-3333-3333-333333333333';

Map<String, dynamic> _body({
  List<Map<String, dynamic>> matches = const [],
  int dismissed = 0,
}) => {
  'side': 'property',
  'object': {
    'key': 'requirement',
    'label': 'Requirement',
    'label_plural': 'Requirements',
  },
  'matches': matches,
  'dismissed_count': dismissed,
};

Map<String, dynamic> _row(String id, String name) => {
  'id': id,
  'name': name,
  'facts': [
    {'label': 'Budget', 'value': 'up to 250,000'},
    {'label': 'Type', 'value': 'Apartment'},
  ],
};

ApiResponse<Map<String, dynamic>> _ok(Map<String, dynamic> body) =>
    ApiResponse(success: true, data: body, statusCode: 200);

class _Api extends CustomObjectsApi {
  Future<ApiResponse<Map<String, dynamic>>> Function(bool dismissed) onFetch =
      (_) async => _ok(_body());
  ApiResponse<Map<String, dynamic>> change = const ApiResponse(
    success: true,
    statusCode: 204,
  );
  final calls = <String>[];

  @override
  Future<ApiResponse<Map<String, dynamic>>> fetchMatches(
    String id, {
    bool dismissed = false,
  }) {
    calls.add('fetch ${dismissed ? 'dismissed' : 'live'}');
    return onFetch(dismissed);
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> dismissMatch(
    String id,
    String otherId,
  ) async {
    calls.add('dismiss $otherId');
    return change;
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> undismissMatch(
    String id,
    String otherId,
  ) async {
    calls.add('undo $otherId');
    return change;
  }
}

Future<void> _pump(WidgetTester tester, _Api api, {bool settle = true}) async {
  tester.view.devicePixelRatio = 3.0;
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  addTearDown(tester.view.reset);
  final router = GoRouter(
    routes: [
      GoRoute(
        path: '/',
        builder: (_, _) => const Scaffold(
          body: SingleChildScrollView(child: RecordMatchesCard(recordId: _id)),
        ),
      ),
      GoRoute(
        path: AppRoutes.objectRecord('requirement', _a),
        builder: (_, _) => const Scaffold(body: Text('requirement screen')),
      ),
    ],
  );
  await tester.pumpWidget(
    ProviderScope(
      overrides: [customObjectsApiProvider.overrideWithValue(api)],
      child: MaterialApp.router(routerConfig: router),
    ),
  );
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

void main() {
  testWidgets('shows a spinner while loading', (tester) async {
    final api = _Api();
    final gate = Completer<ApiResponse<Map<String, dynamic>>>();
    api.onFetch = (_) => gate.future;
    await _pump(tester, api, settle: false);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.text('Matches'), findsOneWidget);
    gate.complete(_ok(_body()));
    await tester.pumpAndSettle();
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('lists the matches with their facts and opens one', (
    tester,
  ) async {
    final api = _Api()
      ..onFetch = (_) async =>
          _ok(_body(matches: [_row(_a, 'Asha, 2 bed'), _row(_b, 'Ben')]));
    await _pump(tester, api);
    expect(find.text('Matching requirements'), findsOneWidget);
    expect(find.text('Asha, 2 bed'), findsOneWidget);
    expect(find.text('Budget: up to 250,000'), findsNWidgets(2));
    expect(find.text('Type: Apartment'), findsNWidgets(2));
    expect(find.text('Dismiss'), findsNWidgets(2));
    expect(find.textContaining('dismissed'), findsNothing);

    await tester.tap(find.text('Asha, 2 bed'));
    await tester.pumpAndSettle();
    expect(find.text('requirement screen'), findsOneWidget);
  });

  testWidgets('says so when nothing matches', (tester) async {
    await _pump(tester, _Api());
    expect(find.text('No matches yet.'), findsOneWidget);
  });

  testWidgets('a failed load shows a quiet line and retries', (tester) async {
    final api = _Api()
      ..onFetch = (_) async =>
          const ApiResponse(success: false, message: 'x', statusCode: 500);
    await _pump(tester, api);
    expect(find.text('Could not load matches'), findsOneWidget);
    expect(find.text('No matches yet.'), findsNothing);

    api.onFetch = (_) async => _ok(_body(matches: [_row(_a, 'Asha')]));
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Could not load matches'), findsNothing);
    expect(find.text('Asha'), findsOneWidget);
  });

  testWidgets('dismiss sends the pair and reloads', (tester) async {
    final api = _Api()
      ..onFetch = (_) async => _ok(_body(matches: [_row(_a, 'Asha')]));
    await _pump(tester, api);
    api.onFetch = (_) async => _ok(_body(dismissed: 1));

    await tester.tap(find.text('Dismiss'));
    await tester.pumpAndSettle();

    expect(api.calls, ['fetch live', 'dismiss $_a', 'fetch live']);
    expect(find.text('Asha'), findsNothing);
    expect(find.text('No matches yet.'), findsOneWidget);
    expect(find.text('1 dismissed'), findsOneWidget);
  });

  testWidgets('a refused dismiss keeps the row and says why', (tester) async {
    final api = _Api();
    api.onFetch = (_) async => _ok(_body(matches: [_row(_a, 'Asha')]));
    api.change = const ApiResponse(
      success: false,
      message: 'Not found',
      statusCode: 404,
    );
    await _pump(tester, api);
    await tester.tap(find.text('Dismiss'));
    await tester.pumpAndSettle();

    expect(find.text('Asha'), findsOneWidget);
    expect(find.text('Not found'), findsOneWidget);
    expect(api.calls, ['fetch live', 'dismiss $_a']);
  });

  testWidgets('show dismissed lists them with Undo, and undo restores', (
    tester,
  ) async {
    final api = _Api()
      ..onFetch = (dismissed) async => _ok(
        dismissed
            ? _body(matches: [_row(_b, 'Ben')], dismissed: 1)
            : _body(matches: [_row(_a, 'Asha')], dismissed: 1),
      );
    await _pump(tester, api);
    expect(find.text('1 dismissed'), findsOneWidget);

    await tester.tap(find.text('Show'));
    await tester.pumpAndSettle();
    expect(api.calls.last, 'fetch dismissed');
    expect(find.text('Ben'), findsOneWidget);
    expect(find.text('Asha'), findsNothing);
    expect(find.text('Undo'), findsOneWidget);
    expect(find.text('Dismiss'), findsNothing);

    api.onFetch = (dismissed) async => _ok(
      dismissed ? _body() : _body(matches: [_row(_a, 'Asha'), _row(_b, 'Ben')]),
    );
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();

    expect(api.calls, contains('undo $_b'));
    expect(api.calls.last, 'fetch live');
    expect(find.text('No matches yet.'), findsNothing);
    expect(find.text('Ben'), findsOneWidget);
    expect(find.text('Dismiss'), findsNWidgets(2));
    expect(find.textContaining('dismissed'), findsNothing);
  });

  testWidgets('hiding dismissed returns to the live list', (tester) async {
    final api = _Api()
      ..onFetch = (dismissed) async => _ok(
        dismissed
            ? _body(matches: [_row(_b, 'Ben')], dismissed: 1)
            : _body(matches: [_row(_a, 'Asha')], dismissed: 1),
      );
    await _pump(tester, api);
    await tester.tap(find.text('Show'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Hide dismissed'));
    await tester.pumpAndSettle();
    expect(find.text('Asha'), findsOneWidget);
    expect(find.text('1 dismissed'), findsOneWidget);
  });

  testWidgets('buttons are disabled while a reload is in flight', (
    tester,
  ) async {
    final api = _Api()
      ..onFetch = (_) async =>
          _ok(_body(matches: [_row(_a, 'Asha')], dismissed: 1));
    await _pump(tester, api);
    final gate = Completer<ApiResponse<Map<String, dynamic>>>();
    api.onFetch = (_) => gate.future;
    await tester.tap(find.text('Show'));
    await tester.pump();

    TextButton button(String label) =>
        tester.widget<TextButton>(find.widgetWithText(TextButton, label));
    // The old row is still drawn, now under the other mode's label.
    expect(button('Undo').onPressed, isNull);
    expect(button('Hide dismissed').onPressed, isNull);
    final before = api.calls.length;
    await tester.tap(find.text('Undo'), warnIfMissed: false);
    await tester.pump();
    expect(api.calls.length, before);

    gate.complete(_ok(_body(matches: [_row(_b, 'Ben')], dismissed: 1)));
    await tester.pumpAndSettle();
    expect(button('Undo').onPressed, isNotNull);
    expect(find.text('Ben'), findsOneWidget);
  });

  testWidgets('each control is named for its record', (tester) async {
    final handle = tester.ensureSemantics();
    final api = _Api()
      ..onFetch = (_) async => _ok(
        _body(matches: [_row(_a, 'Asha'), _row(_b, 'Ben')], dismissed: 1),
      );
    await _pump(tester, api);
    expect(find.bySemanticsLabel('Dismiss Asha'), findsOneWidget);
    expect(find.bySemanticsLabel('Dismiss Ben'), findsOneWidget);

    await tester.tap(find.text('Show'));
    await tester.pumpAndSettle();
    expect(find.bySemanticsLabel('Undo Asha'), findsOneWidget);
    expect(find.bySemanticsLabel('Undo Ben'), findsOneWidget);
    handle.dispose();
  });

  for (final scale in [1.3, 1.5]) {
    testWidgets('fits 390px at text scale $scale', (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = scale;
      addTearDown(tester.platformDispatcher.clearAllTestValues);
      final long = 'A very long requirement name for a family of five' * 2;
      final api = _Api()
        ..onFetch = (_) async => _ok(
          _body(matches: [_row(_a, long), _row(_b, 'Ben')], dismissed: 3),
        );
      await _pump(tester, api);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Show'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('the real ApiService path sends the contract-clean requests', (
    tester,
  ) async {
    final seen = <String>[];
    ApiService().setClientForTesting(
      MockClient((request) async {
        seen.add(
          '${request.method} ${request.url.path}'
          '${request.url.query.isEmpty ? '' : '?${request.url.query}'}',
        );
        if (request.method == 'GET') {
          return http.Response(
            jsonEncode(_body(matches: [_row(_a, 'Asha')], dismissed: 1)),
            200,
            headers: {'content-type': 'application/json'},
          );
        }
        return http.Response('', 204);
      }),
    );
    addTearDown(() => ApiService().setClientForTesting(http.Client()));
    final api = CustomObjectsApi();

    final live = await api.fetchMatches(_id);
    expect(live.success, isTrue);
    final parsed = RecordMatches.fromJson(live.data!);
    expect(parsed.matches.single.name, 'Asha');
    expect(parsed.matches.single.facts.first.label, 'Budget');
    expect(parsed.dismissedCount, 1);
    expect(parsed.heading, 'Matching requirements');
    await api.fetchMatches(_id, dismissed: true);
    expect((await api.dismissMatch(_id, _a)).success, isTrue);
    expect((await api.undismissMatch(_id, _a)).success, isTrue);

    final path = '/api/real-estate/matches/$_id/';
    expect(seen, [
      'GET $path',
      'GET $path?dismissed=true',
      'PUT ${path}dismissed/$_a/',
      'DELETE ${path}dismissed/$_a/',
    ]);
    // flutter_test_config fails the test on any recorded contract violation.
    expect(apiContractViolations, isEmpty);
  });
}
