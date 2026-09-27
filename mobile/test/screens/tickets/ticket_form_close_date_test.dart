import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/lookup_models.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_form_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The ticket form's close date is optional. Left empty, the save carries no
/// `closed_on` and the server dates the close today in the org's timezone
/// (`cases.approvals.closing_date`); a picked date is sent as picked. Checked
/// at 390px with the system font at 1.3x.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester) {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(390, 844);
    tester.platformDispatcher.textScaleFactorTestValue = 1.3;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<_FakeTicketsNotifier> pump(
    WidgetTester tester, {
    Ticket? existing,
  }) async {
    usePhone(tester);
    final notifier = _FakeTicketsNotifier(existing);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          ticketsProvider.overrideWith(() => notifier),
          usersProvider.overrideWithValue(const []),
          tagsProvider.overrideWithValue(const []),
          contactOptionsProvider.overrideWithValue(const []),
          accountOptionsProvider.overrideWithValue(const [
            AccountLookup(id: 'a1', name: 'Acme'),
          ]),
          customFieldDefinitionsProvider(
            'Case',
          ).overrideWith((ref) async => const []),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: TicketFormScreen(ticketId: existing?.id),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return notifier;
  }

  Future<void> chooseClosed(WidgetTester tester) async {
    await tester.tap(find.text('New').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Closed').last);
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester, String label) async {
    await tester.scrollUntilVisible(
      find.text(label),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text(label));
    await tester.pumpAndSettle();
  }

  final open = Ticket(
    id: 't1',
    name: 'Printer on fire',
    status: TicketStatus.newStatus,
    priority: TicketPriority.normal,
    ticketType: TicketType.question,
    accountId: 'a1',
    createdAt: DateTime(2026, 5, 1),
  );

  testWidgets('closing with no date sends no closed_on', (tester) async {
    final notifier = await pump(tester, existing: open);
    await chooseClosed(tester);

    expect(find.text('Closed on (optional)'), findsOneWidget);
    expect(find.text('Today'), findsOneWidget);
    expect(find.textContaining('dated today in your'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await save(tester, 'Update Ticket');

    final payload = notifier.updates.single;
    expect(payload['status'], 'Closed');
    expect(payload.containsKey('closed_on'), isFalse);
  });

  testWidgets('a picked date is sent, and Clear goes back to none', (
    tester,
  ) async {
    final notifier = await pump(tester, existing: open);
    await chooseClosed(tester);

    await tester.tap(find.text('Today'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(find.text('Today'), findsNothing);
    expect(find.text('Clear'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Clear'));
    await tester.pumpAndSettle();
    expect(find.text('Today'), findsOneWidget);

    await tester.tap(find.text('Today'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    await save(tester, 'Update Ticket');

    expect(
      notifier.updates.single['closed_on'],
      matches(RegExp(r'^\d{4}-\d{2}-\d{2}$')),
    );
  });

  testWidgets('a ticket created as Closed with no date sends no closed_on', (
    tester,
  ) async {
    final notifier = await pump(tester);
    await tester.enterText(find.byType(TextFormField).first, 'Printer');
    await tester.tap(find.text('Select an account'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Acme'));
    await tester.pumpAndSettle();
    await chooseClosed(tester);

    await save(tester, 'Create Ticket');

    final payload = notifier.creates.single;
    expect(payload['status'], 'Closed');
    expect(payload.containsKey('closed_on'), isFalse);
  });

  testWidgets('a date already saved is not offered a Clear that does nothing', (
    tester,
  ) async {
    await pump(
      tester,
      existing: Ticket(
        id: 't2',
        name: 'Old one',
        status: TicketStatus.closed,
        priority: TicketPriority.normal,
        ticketType: TicketType.question,
        accountId: 'a1',
        createdAt: DateTime(2026, 5, 1),
        closedOn: DateTime(2026, 5, 9),
      ),
    );

    expect(find.text('2026-05-09'), findsOneWidget);
    expect(find.text('Clear'), findsNothing);
    expect(find.textContaining('change when it was closed'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier(this.existing);

  final Ticket? existing;
  final List<Map<String, dynamic>> creates = [];
  final List<Map<String, dynamic>> updates = [];

  @override
  Future<TicketsListData> build() async =>
      const TicketsListData(tickets: [], totalCount: 0, hasMore: false);

  @override
  Future<Ticket?> getTicketById(String id) async => existing;

  // Refused, so the form stays put and the payload is all that is checked.
  @override
  Future<ApiResponse<Map<String, dynamic>>> createTicket(
    Map<String, dynamic> ticketData,
  ) async {
    creates.add(ticketData);
    return ApiResponse(success: false, statusCode: 400, message: 'held');
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateTicket(
    String id,
    Map<String, dynamic> ticketData,
  ) async {
    updates.add(ticketData);
    return ApiResponse(success: false, statusCode: 400, message: 'held');
  }
}
