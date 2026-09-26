import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// A ticket with a merged-in source the viewer may not open (D52).
///
/// `merged_from_cases` used to carry every source's name to anyone who could
/// read the surviving ticket. The API now sends `{id, name: null, merged_at,
/// restricted: true}` for a hidden source, the `parent_summary` rule (D51), and
/// the phone must say "A ticket you cannot open" and not offer to unmerge it
/// (unmerging needs admin or creator of both, so it would only answer 403).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const hiddenJson = {
    'id': 's-hidden',
    'name': null,
    'merged_at': '2026-09-20T10:00:00Z',
    'restricted': true,
  };
  const readableJson = {
    'id': 's-open',
    'name': 'Printer jam duplicate',
    'merged_at': '2026-09-21T10:00:00Z',
    'restricted': false,
    'can_unmerge': true,
  };

  group('MergedFromSummary.fromJson', () {
    test('a hidden source reads as the tree phrase', () {
      final s = MergedFromSummary.fromJson(hiddenJson);
      expect(s.id, 's-hidden');
      expect(s.restricted, isTrue);
      expect(s.name, 'A ticket you cannot open');
      expect(s.name, TicketParentSummary.restrictedName);
      expect(s.mergedAt, DateTime.utc(2026, 9, 20, 10));
      // Absent `can_unmerge` reads as not allowed.
      expect(s.canUnmerge, isFalse);
    });

    test('a readable source keeps its name', () {
      final s = MergedFromSummary.fromJson(readableJson);
      expect(s.restricted, isFalse);
      expect(s.name, 'Printer jam duplicate');
      expect(s.canUnmerge, isTrue);
    });
  });

  group('TicketDetailScreen at 390px', () {
    void usePhone(WidgetTester tester) {
      tester.view.devicePixelRatio = 3.0;
      tester.view.physicalSize = const Size(390 * 3, 844 * 3);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
    }

    final ticket = Ticket.fromJson({
      'id': 't1',
      'name': 'Surviving ticket',
      'status': 'New',
      'priority': 'Normal',
      'created_at': '2026-09-01T09:00:00Z',
    });

    Widget app(List<MergedFromSummary> sources) => ProviderScope(
      overrides: [
        ticketsProvider.overrideWith(
          () => _FakeTicketsNotifier(ticket, sources),
        ),
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
              builder: (_, _) => TicketDetailScreen(ticketId: ticket.id),
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

    testWidgets('a hidden source is named as one and offers no unmerge', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(
        app([
          MergedFromSummary.fromJson(hiddenJson),
          MergedFromSummary.fromJson(readableJson),
        ]),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      final hidden = find.text('A ticket you cannot open');
      await tester.ensureVisible(hidden);
      await tester.pumpAndSettle();
      expect(hidden, findsOneWidget);
      expect(find.text('Printer jam duplicate'), findsOneWidget);
      // One Unmerge, for the readable source only.
      expect(find.text('Unmerge'), findsOneWidget);

      await tester.tap(hidden);
      await tester.pumpAndSettle();
      expect(find.text('opened s-hidden'), findsNothing);
      expect(find.text('Unmerge ticket?'), findsNothing);
    });
  });
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier(this.ticket, this.sources);

  final Ticket ticket;
  final List<MergedFromSummary> sources;

  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async =>
      TicketDetailResult(
        ticketObj: ticket,
        activities: const [],
        mergedFromCases: sources,
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
}
