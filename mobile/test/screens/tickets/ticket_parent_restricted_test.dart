import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_detail_screen.dart';
import 'package:bottle_crm/widgets/cards/ticket_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

/// A ticket whose parent the viewer may not open (D51).
///
/// `parent_summary` used to carry the parent's name to anyone who could read
/// the child. The API now sends `{id, name: null, status: null, restricted:
/// true}` for a hidden parent, the redaction `/tree/` uses for a hidden node,
/// and the phone must say "A ticket you cannot open", not offer to open it
/// (that would only answer 403), and still let the viewer detach from it,
/// since unlinking needs write on the child alone.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const hiddenJson = {
    'id': 'p-hidden',
    'name': null,
    'status': null,
    'restricted': true,
  };
  const readableJson = {
    'id': 'p-open',
    'name': 'Outage umbrella',
    'status': 'Pending',
    'restricted': false,
  };

  group('TicketParentSummary.fromJson', () {
    test('a hidden parent reads as the tree phrase, with no status', () {
      final p = TicketParentSummary.fromJson(hiddenJson);
      expect(p.id, 'p-hidden');
      expect(p.restricted, isTrue);
      expect(p.name, 'A ticket you cannot open');
      expect(p.name, TicketTreeNode.restrictedName);
      expect(p.status, isNull);
    });

    test('a readable parent keeps its name and status', () {
      final p = TicketParentSummary.fromJson(readableJson);
      expect(p.restricted, isFalse);
      expect(p.name, 'Outage umbrella');
      expect(p.status, 'Pending');
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

  Ticket child(Map<String, dynamic> parent) => Ticket.fromJson({
    'id': 'c1',
    'name': 'My sub-ticket',
    'status': 'New',
    'priority': 'Normal',
    'created_at': '2026-09-01T09:00:00Z',
    'parent': parent['id'],
    'parent_summary': parent,
  });

  group('TicketCard at 390px', () {
    testWidgets('a hidden parent shows the sub-ticket pill and no name', (
      tester,
    ) async {
      // Layout across text scales and widths is covered by
      // test/widgets/ticket_card_layout_test.dart (D53).
      usePhone(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light,
          home: Scaffold(body: TicketCard(ticketItem: child(hiddenJson))),
        ),
      );
      expect(tester.takeException(), isNull);
      expect(find.text('Sub-ticket'), findsOneWidget);
    });
  });

  group('TicketDetailScreen at 390px', () {
    Widget app(Ticket ticket) => ProviderScope(
      overrides: [
        ticketsProvider.overrideWith(() => _FakeTicketsNotifier(ticket)),
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

    testWidgets('a hidden parent is named as one and does not open', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(app(child(hiddenJson)));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      final row = find.text('A ticket you cannot open');
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      expect(row, findsOneWidget);

      await tester.tap(row);
      await tester.pumpAndSettle();
      expect(find.text('opened p-hidden'), findsNothing);
    });

    testWidgets('a readable parent is named and opens', (tester) async {
      usePhone(tester);
      await tester.pumpWidget(app(child(readableJson)));
      await tester.pumpAndSettle();

      final row = find.text('Outage umbrella');
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      await tester.tap(row);
      await tester.pumpAndSettle();
      expect(find.text('opened p-open'), findsOneWidget);
    });

    Future<void> openParentSheet(WidgetTester tester) async {
      await tester.tap(find.byIcon(LucideIcons.moreVertical));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Change / detach parent'));
      await tester.pumpAndSettle();
    }

    testWidgets('detaching from a hidden parent is offered, unnamed', (
      tester,
    ) async {
      usePhone(tester);
      await tester.pumpWidget(app(child(hiddenJson)));
      await tester.pumpAndSettle();
      await openParentSheet(tester);

      expect(find.text('Detach from parent ticket'), findsOneWidget);
      expect(find.textContaining('Detach from "'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('detaching from a readable parent names it', (tester) async {
      usePhone(tester);
      await tester.pumpWidget(app(child(readableJson)));
      await tester.pumpAndSettle();
      await openParentSheet(tester);

      expect(find.text('Detach from "Outage umbrella"'), findsOneWidget);
    });

    testWidgets('a long parent name wraps in the sheet at large text', (
      tester,
    ) async {
      usePhone(tester, textScale: 1.3);
      await tester.pumpWidget(
        app(
          child({
            ...readableJson,
            'name': 'Every printer on the third floor stopped at once',
          }),
        ),
      );
      await tester.pumpAndSettle();
      await openParentSheet(tester);

      expect(find.textContaining('Every printer'), findsWidgets);
      expect(tester.takeException(), isNull);
    });
  });
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier(this.ticket);

  final Ticket ticket;

  @override
  Future<TicketsListData> build() async =>
      TicketsListData(tickets: const [], totalCount: 0, hasMore: false);

  @override
  Future<TicketDetailResult?> getTicketDetail(String id) async =>
      TicketDetailResult(
        ticketObj: ticket,
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
}
