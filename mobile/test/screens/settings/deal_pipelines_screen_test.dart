import 'dart:convert';

import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/screens/settings/deal_pipeline_detail_screen.dart';
import 'package:bottle_crm/screens/settings/deal_pipelines_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

/// The deal pipeline settings at a real phone width, against the real
/// provider and a fake server.
///
/// Rendered at 390px and at 1.3x text: an overflow is a thrown FlutterError,
/// so `takeException()` being null is the layout assertion. Beyond layout: a
/// member sees no write control, an admin's controls are 44px targets, a
/// reorder sends every stage id, and a refusal arrives as the server's
/// sentence.
const _pipelines = '''
{"pipelines": [
  {"id": "p1", "name": "Sales pipeline for the whole of the northern region",
   "is_default": true,
   "stages": [
     {"id": "s1", "code": "PROSPECTING", "label": "Prospecting", "order": 0,
      "kind": "open", "expected_days": 14, "warning_days": 10},
     {"id": "s2", "code": "DEMO_BOOKED_AND_WAITING_ON_PROCUREMENT",
      "label": "Demo booked and waiting on procurement sign-off", "order": 1,
      "kind": "open", "expected_days": null, "warning_days": null},
     {"id": "s3", "code": "CLOSED_WON", "label": "Closed Won", "order": 2,
      "kind": "won", "expected_days": null, "warning_days": null},
     {"id": "s4", "code": "CLOSED_LOST", "label": "Closed Lost", "order": 3,
      "kind": "lost", "expected_days": null, "warning_days": null}
   ]},
  {"id": "p2", "name": "Partners", "is_default": false, "stages": []}
]}
''';

class _FakeClient extends http.BaseClient {
  final Map<String, (int, String)> replies = {
    'GET /api/opportunities/pipelines/': (200, _pipelines),
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
                builder: (_, _) => const DealPipelinesScreen(),
              ),
              GoRoute(
                path: '/more/settings/deal-pipelines/:pipelineId',
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
          home: DealPipelineDetailScreen(pipelineId: 'p1'),
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
          expect(find.text('Partners'), findsOneWidget);
          expect(find.text('4 stages'), findsOneWidget);
          expect(find.text('Default'), findsOneWidget);
          expect(
            find.text('Rename'),
            isAdmin ? findsNWidgets(2) : findsNothing,
          );
          // The default pipeline is never deletable, so one Delete at most.
          expect(find.text('Delete'), isAdmin ? findsOneWidget : findsNothing);
          expect(
            find.byTooltip('New pipeline'),
            isAdmin ? findsOneWidget : findsNothing,
          );
        });
      }
    }

    testWidgets('an admin control is at least 44px tall', (tester) async {
      await pumpList(tester, isAdmin: true);

      final button = find.ancestor(
        of: find.text('Rename').first,
        matching: find.byType(OutlinedButton),
      );
      expect(tester.getSize(button).height, greaterThanOrEqualTo(44));
    });

    testWidgets('a row opens its pipeline', (tester) async {
      await pumpList(tester, isAdmin: false);

      await tester.tap(find.text('Partners'));
      await tester.pumpAndSettle();

      expect(find.text('pipeline p2'), findsOneWidget);
    });

    testWidgets('a refused delete shows the server sentence', (tester) async {
      client.replies['DELETE /api/opportunities/pipelines/p2/'] = (
        400,
        '{"error": true, "errors": "This pipeline still holds 3 deals."}',
      );
      await pumpList(tester, isAdmin: true);

      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      await tester.tap(inDialog('Delete'));
      await tester.pumpAndSettle();

      expect(client.writes.single.method, 'DELETE');
      expect(find.text('This pipeline still holds 3 deals.'), findsOneWidget);
    });

    testWidgets('a write the server refuses with 403 shows its sentence', (
      tester,
    ) async {
      client.replies['PATCH /api/opportunities/pipelines/p2/'] = (
        403,
        '{"detail": "Only admins can change deal pipelines."}',
      );
      // Pumped as an admin so the control exists; the server is what refuses.
      await pumpList(tester, isAdmin: true);

      await tester.tap(find.text('Rename').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Channel');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(client.lastWriteBody(), {'name': 'Channel'});
      expect(
        find.text('Only admins can change deal pipelines.'),
        findsOneWidget,
      );
    });

    testWidgets('a new pipeline is posted by name', (tester) async {
      client.replies['POST /api/opportunities/pipelines/'] = (
        201,
        '{"id": "p3", "name": "Renewals", "is_default": false, "stages": []}',
      );
      await pumpList(tester, isAdmin: true, textScale: 1.3);

      await tester.tap(find.byTooltip('New pipeline'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.enterText(find.byType(TextField), '  Renewals ');
      await tester.tap(find.text('Create'));
      await tester.pumpAndSettle();

      expect(client.lastWriteBody(), {'name': 'Renewals'});
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
          expect(
            find.text('Demo booked and waiting on procurement sign-off'),
            findsOneWidget,
          );
          expect(
            find.text('Open. Past expected at 10 days, stalled at 21.'),
            findsOneWidget,
          );
          expect(find.text('Open. Never ages.'), findsOneWidget);
          expect(find.text('Won'), findsOneWidget);
          final writes = isAdmin ? findsOneWidget : findsNothing;
          expect(find.byTooltip('Add stage'), writes);
          expect(find.byTooltip('Move Prospecting down'), writes);
          expect(find.byTooltip('Delete Closed Lost'), writes);
        });
      }
    }

    testWidgets('a member cannot open the stage editor', (tester) async {
      await pumpDetail(tester, isAdmin: false);

      await tester.tap(find.text('Prospecting'));
      await tester.pumpAndSettle();

      expect(find.text('Edit stage'), findsNothing);
    });

    testWidgets('the reorder buttons are 44px and send the whole order', (
      tester,
    ) async {
      client.replies['POST /api/opportunities/pipelines/p1/stages/reorder/'] = (
        200,
        '{}',
      );
      await pumpDetail(tester, isAdmin: true);

      final down = find.byTooltip('Move Prospecting down');
      expect(tester.getSize(down).height, greaterThanOrEqualTo(44));
      expect(tester.getSize(down).width, greaterThanOrEqualTo(44));

      await tester.tap(down);
      await tester.pumpAndSettle();

      expect(
        client.writes.single.url.path,
        '/api/opportunities/pipelines/p1/stages/reorder/',
      );
      expect(client.lastWriteBody(), {
        'stage_ids': ['s2', 's1', 's3', 's4'],
      });
      expect(find.text('Order saved'), findsOneWidget);
    });

    testWidgets('a refused stage delete shows the server sentence', (
      tester,
    ) async {
      client.replies['DELETE /api/opportunities/stages/s4/'] = (
        400,
        '{"error": true, "errors": "This is the pipeline\'s last lost stage."}',
      );
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Delete Closed Lost'));
      await tester.pumpAndSettle();
      await tester.tap(inDialog('Delete'));
      await tester.pumpAndSettle();

      expect(
        find.text("This is the pipeline's last lost stage."),
        findsOneWidget,
      );
    });

    testWidgets('an open stage is saved with its days by PATCH', (
      tester,
    ) async {
      client.replies['PATCH /api/opportunities/stages/s1/'] = (200, '{}');
      await pumpDetail(tester, isAdmin: true, textScale: 1.3);

      await tester.tap(find.text('Prospecting'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Edit stage'), findsOneWidget);

      await tester.enterText(find.byType(TextField).at(1), '20');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(client.writes.single.method, 'PATCH');
      expect(client.lastWriteBody(), {
        'label': 'Prospecting',
        'kind': 'open',
        'expected_days': 20,
        'warning_days': 10,
      });
    });

    testWidgets('a closed stage offers no days and sends none', (tester) async {
      client.replies['PATCH /api/opportunities/stages/s3/'] = (200, '{}');
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.text('Closed Won'));
      await tester.pumpAndSettle();

      expect(find.text('Expected days (optional)'), findsNothing);
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(client.lastWriteBody(), {
        'label': 'Closed Won',
        'kind': 'won',
        'expected_days': null,
        'warning_days': null,
      });
    });

    testWidgets('days out of range are caught before sending', (tester) async {
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Add stage'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'Demo');
      await tester.enterText(find.byType(TextField).at(1), '4000');
      await tester.tap(find.text('Add stage').last);
      await tester.pumpAndSettle();

      expect(
        find.text('Days must be a whole number from 1 to 3650.'),
        findsOneWidget,
      );
      expect(client.writes, isEmpty);
    });

    testWidgets('a duplicate label shows the field sentence', (tester) async {
      client.replies['POST /api/opportunities/pipelines/p1/stages/'] = (
        400,
        '{"error": true, "errors": {"label": '
            '["This pipeline already has a stage with this name."]}}',
      );
      await pumpDetail(tester, isAdmin: true);

      await tester.tap(find.byTooltip('Add stage'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'prospecting');
      await tester.tap(find.text('Add stage').last);
      await tester.pumpAndSettle();

      expect(client.lastWriteBody(), {
        'label': 'prospecting',
        'kind': 'open',
        'expected_days': null,
        'warning_days': null,
      });
      expect(
        find.text('This pipeline already has a stage with this name.'),
        findsOneWidget,
      );
    });
  });
}
