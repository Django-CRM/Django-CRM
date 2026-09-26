import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/data/models/time_entry.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/tickets/ticket_time_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:lucide_icons_flutter/lucide_icons.dart';

/// Ticket detail, three answers the API can give besides a ticket:
///
/// * A merged ticket answers `redirect_to`, the ticket it was merged into. The
///   phone opens that one in its place, as the web redirects, so it never
///   offers a status change on the merged ticket (the API refuses one with
///   "unmerge first").
/// * A ticket the caller may not open answers 404, the same body a missing one
///   gets. The screen shows a not-found state and does not crash.
/// * Link and detach take the ticket's write rule on the server, and
///   `comment_permission` is its answer, so the action is offered only when it
///   is true.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('getTicketDetail', () {
    late _RoutingClient client;
    late ProviderContainer container;

    setUp(() async {
      client = _RoutingClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
      await container.read(ticketsProvider.future);
      client.paths.clear();
    });

    tearDown(() {
      container.dispose();
      ApiService().setClientForTesting(http.Client());
    });

    Map<String, dynamic> ticket(String id) => {
      'cases_obj': {'id': id, 'name': 'Ticket $id', 'status': 'New'},
      'comment_permission': true,
    };

    test('a merged ticket comes back as the one it was merged into', () async {
      client.routes['/cases/src/'] = (200, {'redirect_to': 'tgt'});
      client.routes['/cases/tgt/'] = (200, ticket('tgt'));

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketDetail('src');

      expect(result?.ticketObj.id, 'tgt');
    });

    test('a second redirect is not followed, and is a failed load', () async {
      client.routes['/cases/src/'] = (200, {'redirect_to': 'tgt'});
      client.routes['/cases/tgt/'] = (200, {'redirect_to': 'other'});
      client.routes['/cases/other/'] = (200, ticket('other'));

      await expectLater(
        container.read(ticketsProvider.notifier).getTicketDetail('src'),
        throwsA(isA<TicketLoadFailure>()),
      );
      expect(client.paths, isNot(contains('/cases/other/')));
    });

    test('a 500 is a failed load, not a missing ticket', () async {
      client.routes['/cases/t1/'] = (500, {'detail': 'boom'});

      await expectLater(
        container.read(ticketsProvider.notifier).getTicketDetail('t1'),
        throwsA(
          isA<TicketLoadFailure>().having(
            (f) => f.message,
            'message',
            TicketLoadFailure.fallback,
          ),
        ),
      );
    });

    test('offline is a failed load that says so', () async {
      client.offline = true;

      await expectLater(
        container.read(ticketsProvider.notifier).getTicketDetail('t1'),
        throwsA(
          isA<TicketLoadFailure>().having(
            (f) => f.message,
            'message',
            contains('Cannot reach the server'),
          ),
        ),
      );
    });

    test('the edit form reads any failure as nothing to edit', () async {
      client.routes['/cases/t1/'] = (500, {'detail': 'boom'});
      expect(
        await container.read(ticketsProvider.notifier).getTicketById('t1'),
        isNull,
      );
    });

    test('the edit form\'s fetch does not follow a merge', () async {
      // It saves back to the id it was given, so following would write the
      // surviving ticket's fields onto the merged one.
      client.routes['/cases/src/'] = (200, {'redirect_to': 'tgt'});
      client.routes['/cases/tgt/'] = (200, ticket('tgt'));

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketById('src');

      expect(result, isNull);
      expect(client.paths, ['/cases/src/']);
    });

    test('a 404 is no ticket, not a crash', () async {
      client.routes['/cases/hidden/'] = (404, {'detail': 'No such case.'});

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketDetail('hidden');

      expect(result, isNull);
    });

    test('an ordinary ticket is itself', () async {
      client.routes['/cases/t1/'] = (200, ticket('t1'));

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketDetail('t1');

      expect(result?.ticketObj.id, 't1');
      expect(client.paths, ['/cases/t1/']);
    });
  });

  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Widget app(String openedId, _FakeTicketsNotifier notifier) => ProviderScope(
    overrides: [
      ticketsProvider.overrideWith(() => notifier),
      usersProvider.overrideWithValue(const []),
      tagsProvider.overrideWithValue(const []),
      accountOptionsProvider.overrideWithValue(const []),
    ],
    child: MaterialApp.router(
      theme: AppTheme.light,
      routerConfig: GoRouter(
        initialLocation: '/here',
        routes: [
          GoRoute(
            path: '/here',
            builder: (_, _) => TicketDetailScreen(ticketId: openedId),
          ),
          GoRoute(
            path: '/tickets/:id',
            builder: (_, s) =>
                Scaffold(body: Text('opened ${s.pathParameters['id']}')),
          ),
        ],
      ),
    ),
  );

  Ticket ticketNamed(String id) => Ticket.fromJson({
    'id': id,
    'name': 'Printer on fire',
    'status': 'New',
    'priority': 'Normal',
    'created_at': '2026-09-01T09:00:00Z',
  });

  group('TicketTimePanel', () {
    for (final canLog in [true, false]) {
      testWidgets('logging and the timer follow comment_permission ($canLog)', (
        tester,
      ) async {
        usePhone(tester, textScale: 1.3);
        await tester.pumpWidget(
          ProviderScope(
            overrides: [
              ticketsProvider.overrideWith(() => _FakeTicketsNotifier()),
            ],
            child: MaterialApp(
              theme: AppTheme.light,
              home: Scaffold(
                body: SingleChildScrollView(
                  child: TicketTimePanel(ticketId: 't1', canLogTime: canLog),
                ),
              ),
            ),
          ),
        );
        await tester.pumpAndSettle();

        final shown = canLog ? findsOneWidget : findsNothing;
        expect(find.text('Start timer'), shown);
        expect(find.byTooltip('Add manual entry'), shown);
        expect(find.text('TIME TRACKING'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('TicketDetailScreen at 390px', () {
    testWidgets('a merged ticket opens the one it was merged into', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(
        app('src', _FakeTicketsNotifier(detail: ticketNamed('tgt'))),
      );
      await tester.pumpAndSettle();

      expect(find.text('opened tgt'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    for (final scale in [1.0, 1.3]) {
      testWidgets('a failed load is not "not found", and retries, at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        final notifier = _FakeTicketsNotifier(
          failure: const TicketLoadFailure(
            'Cannot reach the server. Check your connection and try again.',
          ),
        );
        await tester.pumpWidget(app('t1', notifier));
        await tester.pumpAndSettle();

        expect(find.text('Could not load this ticket'), findsOneWidget);
        expect(find.textContaining('Cannot reach the server'), findsOneWidget);
        expect(find.text('Ticket not found'), findsNothing);
        expect(tester.takeException(), isNull);

        // Back online: Retry loads the ticket.
        notifier
          ..failure = null
          ..detail = ticketNamed('t1');
        await tester.tap(find.text('Retry'));
        await tester.pumpAndSettle();
        expect(find.text('Could not load this ticket'), findsNothing);
        expect(find.text('Printer on fire'), findsWidgets);
      });

      testWidgets('a hidden or missing ticket is a not-found state at $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        await tester.pumpWidget(app('hidden', _FakeTicketsNotifier()));
        await tester.pumpAndSettle();

        expect(find.text('Ticket not found'), findsOneWidget);
        expect(
          find.text('It does not exist, or you do not have access to it.'),
          findsOneWidget,
        );
        // Asking again gets the same 404, so no retry is offered.
        expect(find.text('Retry'), findsNothing);
        expect(tester.takeException(), isNull);
      });
    }

    Future<void> openActions(WidgetTester tester) async {
      await tester.tap(find.byIcon(LucideIcons.moreVertical));
      await tester.pumpAndSettle();
    }

    testWidgets('link to a parent is offered to someone who may change it', (
      tester,
    ) async {
      usePhone(tester, textScale: 1.3);
      await tester.pumpWidget(
        app(
          't1',
          _FakeTicketsNotifier(
            detail: ticketNamed('t1'),
            commentPermission: true,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await openActions(tester);

      expect(find.text('Link to parent ticket'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    for (final canWrite in [true, false]) {
      testWidgets(
        'close with children follows comment_permission ($canWrite)',
        (tester) async {
          usePhone(tester, textScale: 1.3);
          final parent = Ticket.fromJson({
            'id': 't1',
            'name': 'Outage',
            'status': 'New',
            'priority': 'Normal',
            'created_at': '2026-09-01T09:00:00Z',
            'child_count': 2,
          });
          await tester.pumpWidget(
            app(
              't1',
              _FakeTicketsNotifier(detail: parent, commentPermission: canWrite),
            ),
          );
          await tester.pumpAndSettle();
          await openActions(tester);

          expect(
            find.text('Close with children'),
            canWrite ? findsOneWidget : findsNothing,
          );
          expect(tester.takeException(), isNull);
        },
      );
    }

    for (final canWrite in [true, false]) {
      testWidgets('every change to the ticket follows comment_permission '
          '($canWrite)', (tester) async {
        usePhone(tester, textScale: 1.3);
        // The approval panel fetches on its own; answer it with nothing.
        final client = _RoutingClient()
          ..routes['/cases/approvals/'] = (200, {'approvals': []});
        ApiService().setClientForTesting(client);
        addTearDown(() => ApiService().setClientForTesting(http.Client()));

        await tester.pumpWidget(
          app(
            't1',
            _FakeTicketsNotifier(
              detail: ticketNamed('t1'),
              commentPermission: canWrite,
            ),
          ),
        );
        await tester.pumpAndSettle();
        final shown = canWrite ? findsOneWidget : findsNothing;

        expect(find.byTooltip('Edit'), shown);

        // Overview panels: linking an article, requesting an approval.
        final scrollable = find
            .descendant(
              of: find.byType(TabBarView),
              matching: find.byType(Scrollable),
            )
            .first;
        await tester.scrollUntilVisible(
          find.text('SOLUTIONS'),
          200,
          scrollable: scrollable,
        );
        expect(find.byTooltip('Link a solution'), shown);
        await tester.scrollUntilVisible(
          find.text('No approval requested yet.'),
          200,
          scrollable: scrollable,
        );
        expect(find.text('Request approval'), shown);

        // The action sheet.
        await openActions(tester);
        for (final label in [
          'Reassign',
          'Change status',
          'Change priority',
          'Close ticket',
          'Link to parent ticket',
        ]) {
          expect(find.text(label), shown, reason: label);
        }
        await tester.tapAt(const Offset(20, 20));
        await tester.pumpAndSettle();

        // Files: attaching one is a change to the ticket.
        await tester.tap(find.text('Files'));
        await tester.pumpAndSettle();
        expect(find.text('Attach a file'), shown);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('link to a parent is not offered to a reader', (tester) async {
      usePhone(tester);
      await tester.pumpWidget(
        app(
          't1',
          _FakeTicketsNotifier(
            detail: ticketNamed('t1'),
            commentPermission: false,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await openActions(tester);

      expect(find.text('Link to parent ticket'), findsNothing);
      expect(find.text('Change / detach parent'), findsNothing);
    });
  });
}

/// Answers each path with a canned status and JSON body, and records the
/// paths asked for.
class _RoutingClient extends http.BaseClient {
  final Map<String, (int, Map<String, dynamic>)> routes = {};
  final List<String> paths = [];

  /// Throw as a dropped connection does.
  bool offline = false;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    if (offline) throw const SocketException('Network is unreachable');
    final path = request.url.path.replaceFirst(RegExp(r'^/api'), '');
    paths.add(path);
    final (status, body) =
        routes[path] ?? (200, {'cases': [], 'cases_count': 0});
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      status,
      request: request,
    );
  }
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier({
    this.detail,
    this.commentPermission = true,
    this.failure,
  });

  /// Null stands for a 404.
  Ticket? detail;
  final bool commentPermission;

  /// Thrown instead, for a load that failed some other way.
  TicketLoadFailure? failure;

  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async {
    if (failure != null) throw failure!;
    if (detail == null) return null;
    return TicketDetailResult(
      ticketObj: detail!,
      activities: const [],
      commentPermission: commentPermission,
      internalCommentIds: const {},
    );
  }

  @override
  Future<TicketWatchers?> getWatchers(String id) async => const TicketWatchers(
    watchers: [],
    count: 0,
    isCurrentUserWatching: false,
  );

  @override
  Future<TicketTreeNode?> fetchTree(String id) async => null;

  @override
  Future<List<TimeEntry>> fetchTimeEntries(String id) async => const [];

  @override
  Future<TimeSummary?> fetchTimeSummary(String id) async => null;
}
