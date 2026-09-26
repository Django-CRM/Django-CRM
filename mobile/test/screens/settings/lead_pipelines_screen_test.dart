import 'dart:convert';

import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/screens/settings/lead_pipeline_detail_screen.dart';
import 'package:bottle_crm/screens/settings/lead_pipelines_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

/// The lead pipeline screens at a real phone width, against the real
/// providers and a fake server.
///
/// Rendered at 390px and at 1.3x text, because the default 800px surface
/// proves nothing about a phone and large text is where a row that only just
/// fits stops fitting. An overflow is a thrown FlutterError, so
/// `takeException()` being null is the layout assertion. Beyond layout: a
/// member sees no write control, an admin's controls are 44px targets, and a
/// refusal arrives as the server's sentence.
const _pipelines = '''
{"pipelines": [
  {"id": "p1", "name": "Inbound enquiries from the website and the phone line",
   "description": "Every enquiry that has not become a customer yet, whichever door it came in by",
   "is_default": true, "stage_count": 3, "lead_count": 7},
  {"id": "p2", "name": "Admissions", "description": "",
   "is_default": false, "stage_count": 0, "lead_count": 0}
]}
''';

const _detail = '''
{"id": "p1", "name": "Inbound", "description": "Web and phone",
 "is_default": true, "stage_count": 3, "lead_count": 7,
 "stages": [
   {"id": "s1", "name": "New", "order": 0, "color": "#3B82F6",
    "stage_type": "open", "maps_to_status": "assigned", "win_probability": 0,
    "lead_count": 5},
   {"id": "s2", "name": "Qualified and waiting on a signed proposal", "order": 1,
    "color": "#F59E0B", "stage_type": "open", "maps_to_status": "in process",
    "win_probability": 25, "lead_count": 2},
   {"id": "s3", "name": "Won", "order": 2, "color": "#22C55E",
    "stage_type": "won", "maps_to_status": "converted", "win_probability": 100,
    "lead_count": 0}
 ]}
''';

class _FakeClient extends http.BaseClient {
  final Map<String, (int, String)> replies = {
    'GET /api/leads/pipelines/': (200, _pipelines),
    'GET /api/leads/pipelines/p1/': (200, _detail),
  };
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  Iterable<http.BaseRequest> get writes => sent.where((r) => r.method != 'GET');

  Map<String, dynamic> lastWriteBody() =>
      jsonDecode(bodies[sent.indexOf(writes.last)]) as Map<String, dynamic>;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    final (status, body) =
        replies['${request.method} ${request.url.path}'] ?? (200, '{}');
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeClient client;

  setUp(() {
    client = _FakeClient();
    ApiService().setClientForTesting(client);
  });

  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<void> pumpList(
    WidgetTester tester, {
    required bool isAdmin,
    double textScale = 1.0,
  }) async {
    usePhone(tester, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [isOrgAdminProvider.overrideWithValue(isAdmin)],
        child: MaterialApp.router(
          routerConfig: GoRouter(
            initialLocation: '/here',
            routes: [
              GoRoute(
                path: '/here',
                builder: (_, _) => const LeadPipelinesScreen(),
              ),
              GoRoute(
                path: '/more/settings/lead-pipelines/:pipelineId',
                builder: (_, s) =>
                    Text('pipeline ${s.pathParameters['pipelineId']}'),
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> pumpDetail(
    WidgetTester tester, {
    required bool isAdmin,
    double textScale = 1.0,
  }) async {
    usePhone(tester, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [isOrgAdminProvider.overrideWithValue(isAdmin)],
        child: const MaterialApp(
          home: LeadPipelineDetailScreen(pipelineId: 'p1'),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Finder inDialog(String text) =>
      find.descendant(of: find.byType(AlertDialog), matching: find.text(text));

  group('the pipeline list', () {
    for (final scale in [1.0, 1.3]) {
      for (final isAdmin in [true, false]) {
        final who = isAdmin ? 'an admin' : 'a member';
        testWidgets('fits 390px at $scale for $who', (tester) async {
          await pumpList(tester, isAdmin: isAdmin, textScale: scale);

          expect(tester.takeException(), isNull);
          expect(find.text('Admissions'), findsOneWidget);
          expect(find.text('3 stages, 7 leads'), findsOneWidget);
          expect(find.text('Default'), findsOneWidget);
          final writes = isAdmin ? findsNWidgets(2) : findsNothing;
          expect(find.text('Rename'), writes);
          expect(find.text('Delete'), writes);
          expect(
            find.byTooltip('New pipeline'),
            isAdmin ? findsOneWidget : findsNothing,
          );
        });
      }
    }

    testWidgets('an admin control is at least 44px tall', (tester) async {
      await pumpList(tester, isAdmin: true);

      expect(
        tester.getSize(find.text('Rename').first).height,
        lessThan(44),
        reason: 'sanity: the label alone is smaller than its button',
      );
      final button = find.ancestor(
        of: find.text('Rename').first,
        matching: find.byType(OutlinedButton),
      );
      expect(tester.getSize(button).height, greaterThanOrEqualTo(44));
    });

    testWidgets('a row opens its pipeline', (tester) async {
      await pumpList(tester, isAdmin: false);

      await tester.tap(find.text('Admissions'));
      await tester.pumpAndSettle();

      expect(find.text('pipeline p2'), findsOneWidget);
    });

    testWidgets('a refused delete shows the lead count sentence', (
      tester,
    ) async {
      client.replies['DELETE /api/leads/pipelines/p1/'] = (
        400,
        '{"error": "This pipeline still has 7 lead(s). Move them out of its '
            'stages first."}',
      );
      await pumpList(tester, isAdmin: true);

      await tester.tap(find.text('Delete').first);
      await tester.pumpAndSettle();
      await tester.tap(inDialog('Delete'));
      await tester.pumpAndSettle();

      expect(client.writes.single.method, 'DELETE');
      expect(
        find.text(
          'This pipeline still has 7 lead(s). Move them out of its stages '
          'first.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a new pipeline is posted from the sheet', (tester) async {
      client.replies['POST /api/leads/pipelines/'] = (201, '{"id": "p3"}');
      await pumpList(tester, isAdmin: true, textScale: 1.3);

      await tester.tap(find.byTooltip('New pipeline'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.enterText(find.byType(TextField).first, 'Partners');
      await tester.tap(find.text('Create'));
      await tester.pumpAndSettle();

      expect(client.lastWriteBody(), {
        'name': 'Partners',
        'description': '',
        'create_default_stages': true,
      });
      expect(find.text('Pipeline created'), findsOneWidget);
    });

    testWidgets('a blank name is caught before anything is sent', (
      tester,
    ) async {
      await pumpList(tester, isAdmin: true);

      await tester.tap(find.byTooltip('New pipeline'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create'));
      await tester.pumpAndSettle();

      expect(find.text('Give the pipeline a name.'), findsOneWidget);
      expect(client.writes, isEmpty);
    });
  });

  group('one pipeline', () {
    for (final scale in [1.0, 1.3]) {
      for (final isAdmin in [true, false]) {
        final who = isAdmin ? 'an admin' : 'a member';
        testWidgets('fits 390px at $scale for $who', (tester) async {
          await pumpDetail(tester, isAdmin: isAdmin, textScale: scale);

          expect(tester.takeException(), isNull);
          expect(find.text('Inbound'), findsOneWidget);
          expect(
            find.text('Qualified and waiting on a signed proposal'),
            findsOneWidget,
          );
          expect(
            find.text('Open, sets status to In Process, 25% win'),
            findsOneWidget,
          );
          final writes = isAdmin ? findsOneWidget : findsNothing;
          expect(find.byTooltip('Add stage'), writes);
          expect(find.byTooltip('Move New down'), writes);
          expect(find.byTooltip('Move Won up'), writes);
          expect(find.byTooltip('Delete New'), writes);
        });
      }
    }

    testWidgets('a member cannot open the stage editor', (tester) async {
      await pumpDetail(tester, isAdmin: false);

      await tester.tap(find.text('New'));
      await tester.pumpAndSettle();

      expect(find.text('Edit stage'), findsNothing);
    });

    testWidgets('the reorder buttons are 44px and send the whole order', (
      tester,
    ) async {
      await pumpDetail(tester, isAdmin: true);

      final down = find.byTooltip('Move New down');
      expect(tester.getSize(down).height, greaterThanOrEqualTo(44));
      expect(tester.getSize(down).width, greaterThanOrEqualTo(44));

      await tester.tap(down);
      await tester.pumpAndSettle();

      expect(
        client.writes.single.url.path,
        '/api/leads/pipelines/p1/stages/reorder/',
      );
      expect(client.lastWriteBody(), {
        'stage_ids': ['s2', 's1', 's3'],
      });
      expect(find.text('Order saved'), findsOneWidget);
    });

    testWidgets('a refused stage delete shows the lead count sentence', (
      tester,
    ) async {
      client.replies['DELETE /api/leads/stages/s1/'] = (
        400,
        '{"error": "This stage still has 5 lead(s). Move them to another '
            'stage first."}',
      );
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Delete New'));
      await tester.pumpAndSettle();
      await tester.tap(inDialog('Delete'));
      await tester.pumpAndSettle();

      expect(
        find.text(
          'This stage still has 5 lead(s). Move them to another stage first.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('an untouched save keeps a stored "converted" mapping', (
      tester,
    ) async {
      await pumpDetail(tester, isAdmin: true, textScale: 1.3);

      await tester.tap(find.text('Won'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Edit stage'), findsOneWidget);
      expect(find.text('Converted'), findsOneWidget);

      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(client.writes.single.method, 'PUT');
      expect(client.writes.single.url.path, '/api/leads/stages/s3/');
      expect(client.lastWriteBody(), {
        'name': 'Won',
        'stage_type': 'won',
        'maps_to_status': 'converted',
        'win_probability': 100,
      });
    });

    testWidgets('a win probability over 100 is caught before sending', (
      tester,
    ) async {
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Add stage'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'Demo');
      await tester.enterText(find.byType(TextField).last, '101');
      await tester.tap(find.text('Add stage').last);
      await tester.pumpAndSettle();

      expect(
        find.text('Win probability must be a whole number from 0 to 100.'),
        findsOneWidget,
      );
      expect(client.writes, isEmpty);
    });

    testWidgets('a duplicate stage name shows the server sentence', (
      tester,
    ) async {
      client.replies['POST /api/leads/pipelines/p1/stages/'] = (
        400,
        '{"error": true, "errors": {"name": '
            '["This pipeline already has a stage with that name."]}}',
      );
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Add stage'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'new');
      await tester.tap(find.text('Add stage').last);
      await tester.pumpAndSettle();

      expect(client.lastWriteBody()['name'], 'new');
      expect(client.lastWriteBody().containsKey('order'), isFalse);
      expect(
        find.text('This pipeline already has a stage with that name.'),
        findsOneWidget,
      );
    });
  });
}
