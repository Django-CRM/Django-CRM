import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

/// Merge and unmerge on ticket detail follow the server's answer.
///
/// `can_merge` on the detail response gates "Merge into another ticket", and
/// `can_unmerge` on each merged-from entry gates its Unmerge. The picker lists
/// only `GET /cases/<id>/merge-targets/`, never the loaded list, which holds
/// tickets a non-admin's merge would refuse. The server stays the gate; these
/// only stop the phone offering what it would refuse.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('parsing', () {
    late _JsonClient client;
    late ProviderContainer container;

    setUp(() async {
      client = _JsonClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
      await container.read(ticketsProvider.future);
    });

    tearDown(() {
      container.dispose();
      ApiService().setClientForTesting(http.Client());
    });

    Map<String, dynamic> detail({Object? canMerge, Object? canUnmerge}) => {
      'cases_obj': {'id': 't1', 'name': 'Ticket', 'status': 'New'},
      'can_merge': ?canMerge,
      'merged_from_cases': [
        {
          'id': 's1',
          'name': 'Source',
          'merged_at': '2026-09-21T10:00:00Z',
          'restricted': false,
          'can_unmerge': ?canUnmerge,
        },
      ],
    };

    test('can_merge and can_unmerge are read from the detail', () async {
      client.body = detail(canMerge: true, canUnmerge: true);

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketDetail('t1');

      expect(result?.canMerge, isTrue);
      expect(result?.mergedFromCases.single.canUnmerge, isTrue);
    });

    test('an absent or false flag reads as not allowed', () async {
      client.body = detail(canMerge: false);

      final result = await container
          .read(ticketsProvider.notifier)
          .getTicketDetail('t1');

      expect(result?.canMerge, isFalse);
      expect(result?.mergedFromCases.single.canUnmerge, isFalse);
      expect(result?.copyWith().canMerge, isFalse);
    });

    test('merge targets come from the server, searched by name', () async {
      client.body = {
        'results': [
          {
            'id': 'm1',
            'name': 'Printer jam',
            'status': 'Pending',
            'priority': 'High',
            'account_name': 'Acme',
          },
          {
            'id': 'm2',
            'name': 'Printer smoke',
            'status': 'New',
            'priority': 'Low',
            'account_name': null,
          },
        ],
      };

      final response = await container
          .read(ticketsProvider.notifier)
          .mergeTargets('t1', search: ' print ');

      expect(client.last!.url.path, '/api/cases/t1/merge-targets/');
      expect(client.last!.url.queryParameters, {'search': 'print'});
      expect(response.success, isTrue);
      final targets = response.data!;
      expect(targets.map((t) => t.id), ['m1', 'm2']);
      expect(targets.first.accountName, 'Acme');
      expect(targets.first.status, TicketStatus.pending);
      expect(targets.first.priority, TicketPriority.high);
      expect(targets.last.accountName, isNull);
    });

    test('an empty search sends no search parameter', () async {
      client.body = {'results': []};

      await container.read(ticketsProvider.notifier).mergeTargets('t1');

      expect(client.last!.url.queryParameters, isEmpty);
    });

    test('a 403 carries the server sentence', () async {
      client.status = 403;
      client.body = {
        'error': true,
        'errors':
            'You must be an admin, or the creator of this ticket, to merge it.',
      };

      final response = await container
          .read(ticketsProvider.notifier)
          .mergeTargets('t1');

      expect(response.success, isFalse);
      expect(
        response.message,
        'You must be an admin, or the creator of this ticket, to merge it.',
      );
    });
  });

  group('TicketDetailScreen at 390px', () {
    void usePhone(WidgetTester tester) {
      tester.view.devicePixelRatio = 3.0;
      tester.view.physicalSize = const Size(390 * 3, 844 * 3);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
    }

    Future<_FakeTicketsNotifier> pump(
      WidgetTester tester, {
      required bool canMerge,
      List<MergedFromSummary> sources = const [],
    }) async {
      final fake = _FakeTicketsNotifier(canMerge: canMerge, sources: sources);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            ticketsProvider.overrideWith(() => fake),
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
                  builder: (_, _) => const TicketDetailScreen(ticketId: 't1'),
                ),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      return fake;
    }

    Future<void> openMore(WidgetTester tester) async {
      await tester.tap(find.byTooltip('More'));
      await tester.pumpAndSettle();
    }

    testWidgets('no merge action when can_merge is false', (tester) async {
      usePhone(tester);
      await pump(tester, canMerge: false);

      await openMore(tester);

      expect(find.text('Change status'), findsOneWidget);
      expect(find.text('Merge into another ticket'), findsNothing);
    });

    testWidgets('the picker lists only the server targets, searched', (
      tester,
    ) async {
      usePhone(tester);
      final fake = await pump(tester, canMerge: true);

      await openMore(tester);
      await tester.tap(find.text('Merge into another ticket'));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Merge into…'), findsOneWidget);
      expect(find.text('Printer jam'), findsOneWidget);
      // In the loaded list, but not a target the server offered.
      expect(find.text('Loaded list ticket'), findsNothing);
      expect(fake.searches, ['']);

      await tester.enterText(find.byType(TextField), 'jam');
      await tester.pump(const Duration(milliseconds: 100));
      expect(fake.searches, ['']);
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pumpAndSettle();
      expect(fake.searches, ['', 'jam']);

      await tester.tap(find.text('Printer jam'));
      await tester.pumpAndSettle();
      expect(find.text('Merge ticket?'), findsOneWidget);
    });

    testWidgets('a refused target search shows the server sentence', (
      tester,
    ) async {
      usePhone(tester);
      final fake = await pump(tester, canMerge: true);
      fake.targetsRefusal =
          'You must be an admin, or the creator of this '
          'ticket, to merge it.';

      await openMore(tester);
      await tester.tap(find.text('Merge into another ticket'));
      await tester.pumpAndSettle();

      expect(
        find.text(
          'You must be an admin, or the creator of this ticket, to merge it.',
        ),
        findsOneWidget,
      );
      expect(find.text('Printer jam'), findsNothing);
    });

    testWidgets('Unmerge shows only where can_unmerge is true', (tester) async {
      usePhone(tester);
      await pump(
        tester,
        canMerge: false,
        sources: const [
          MergedFromSummary(
            id: 's1',
            name: 'Mine to unmerge',
            canUnmerge: true,
          ),
          MergedFromSummary(id: 's2', name: 'Someone else'),
        ],
      );

      final other = find.text('Someone else');
      await tester.ensureVisible(other);
      await tester.pumpAndSettle();

      expect(find.text('Mine to unmerge'), findsOneWidget);
      expect(other, findsOneWidget);
      expect(find.text('Unmerge'), findsOneWidget);
    });
  });
}

class _JsonClient extends http.BaseClient {
  Map<String, dynamic> body = const {'cases': [], 'cases_count': 0};
  int status = 200;
  http.BaseRequest? last;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    last = request;
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      status,
      request: request,
    );
  }
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier({required this.canMerge, required this.sources});

  final bool canMerge;
  final List<MergedFromSummary> sources;
  final List<String> searches = [];
  String? targetsRefusal;

  static final _ticket = Ticket.fromJson({
    'id': 't1',
    'name': 'Surviving ticket',
    'status': 'New',
    'priority': 'Normal',
    'created_at': '2026-09-01T09:00:00Z',
  });

  @override
  Future<TicketsListData> build() async => TicketsListData(
    tickets: [
      Ticket.fromJson({
        'id': 'l1',
        'name': 'Loaded list ticket',
        'status': 'New',
        'created_at': '2026-09-01T09:00:00Z',
      }),
    ],
    totalCount: 1,
    hasMore: false,
  );

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async =>
      TicketDetailResult(
        ticketObj: _ticket,
        activities: const [],
        mergedFromCases: sources,
        commentPermission: true,
        canMerge: canMerge,
        internalCommentIds: const {},
      );

  @override
  Future<ApiResponse<List<Ticket>>> mergeTargets(
    String sourceId, {
    String search = '',
  }) async {
    searches.add(search);
    if (targetsRefusal != null) {
      return ApiResponse(
        success: false,
        message: targetsRefusal,
        statusCode: 403,
      );
    }
    return ApiResponse(
      success: true,
      statusCode: 200,
      data: [
        Ticket.fromJson({
          'id': 'm1',
          'name': 'Printer jam',
          'status': 'Pending',
          'priority': 'High',
        }),
      ],
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
}
