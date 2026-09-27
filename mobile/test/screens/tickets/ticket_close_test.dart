import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/data/models/time_entry.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

/// Closing a ticket from the phone sends no `closed_on`. The server dates the
/// close today in the org's timezone (`cases.approvals.closing_date`), so the
/// phone neither reads the org's zone nor guesses a date from its own clock,
/// and an org stored under a legacy zone name such as `US/Eastern` closes like
/// any other. When the server refuses the close (an approval rule), its own
/// words reach the user.
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

  late _RoutingClient client;
  setUp(() {
    client = _RoutingClient();
    ApiService().setClientForTesting(client);
  });
  tearDown(() => ApiService().setClientForTesting(http.Client()));

  const refusal =
      'An approval is required before this case can be closed '
      '(rule: Close needs sign-off).';

  Widget app() => ProviderScope(
    overrides: [
      ticketsProvider.overrideWith(_FakeTicketsNotifier.new),
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
            builder: (_, _) => const TicketDetailScreen(ticketId: 'c1'),
          ),
        ],
      ),
    ),
  );

  Future<void> closeFromMenu(WidgetTester tester) async {
    await tester.tap(find.byTooltip('More'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Close ticket'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, 'Close ticket'));
    await tester.pumpAndSettle();
  }

  Future<void> closeFromStatusPicker(WidgetTester tester) async {
    await tester.tap(find.byTooltip('More'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Change status'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Closed').last);
    await tester.pumpAndSettle();
  }

  for (final (name, close) in [
    ('the Close ticket action', closeFromMenu),
    ('the status picker', closeFromStatusPicker),
  ]) {
    testWidgets('$name sends the status and no closed_on', (tester) async {
      usePhone(tester);
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await close(tester);

      expect(client.patches, [
        {'status': 'Closed'},
      ]);
      // No timezone lookup: the server knows the org's day.
      expect(client.paths, isNot(contains('/org/settings/')));
      expect(client.paths, isNot(contains('/org/timezones/')));
    });

    testWidgets('$name shows the server\'s refusal at 1.3x text', (
      tester,
    ) async {
      usePhone(tester, textScale: 1.3);
      client.patchAnswer = (
        400,
        {
          'error': true,
          'errors': {
            'status': [refusal],
          },
        },
      );
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await close(tester);

      expect(client.patches, [
        {'status': 'Closed'},
      ]);
      expect(find.textContaining(refusal), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}

class _RoutingClient extends http.BaseClient {
  final List<Map<String, dynamic>> patches = [];
  final List<String> paths = [];
  (int, Map<String, dynamic>) patchAnswer = (200, {'error': false});

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final path = request.url.path.replaceFirst(RegExp(r'^/api'), '');
    paths.add(path);
    var answer = (200, <String, dynamic>{});
    if (request.method == 'PATCH' && path == '/cases/c1/') {
      final body = (request as http.Request).body;
      patches.add(jsonDecode(body) as Map<String, dynamic>);
      answer = patchAnswer;
    }
    final (status, body) = answer;
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      status,
      request: request,
    );
  }
}

class _FakeTicketsNotifier extends TicketsNotifier {
  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async =>
      TicketDetailResult(
        ticketObj: Ticket.fromJson({
          'id': id,
          'name': 'Printer on fire',
          'status': 'New',
          'priority': 'Normal',
          'created_at': '2026-09-01T09:00:00Z',
        }),
        activities: const [],
        commentPermission: true,
        internalCommentIds: const {},
      );

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
