import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/data/models/ticket_board.dart';
import 'package:bottle_crm/providers/ticket_board_provider.dart';
import 'package:bottle_crm/screens/tickets/ticket_board_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The ticket board at a real phone width, and the move it offers.
///
/// Rendered at 390px and at 1.3x text, where a card row that only just fits
/// stops fitting. An overflow is a thrown FlutterError, so `takeException()`
/// being null is the assertion.
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

  Future<_FakeBoard> pump(
    WidgetTester tester, {
    TicketBoardData data = _byStatus,
  }) async {
    final fake = _FakeBoard(data);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [ticketBoardProvider.overrideWith(() => fake)],
        child: const MaterialApp(home: TicketBoardScreen()),
      ),
    );
    await tester.pumpAndSettle();
    return fake;
  }

  Finder inSheet(String text) =>
      find.descendant(of: find.byType(BottomSheet), matching: find.text(text));

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits a 390px phone at text scale $scale', (tester) async {
      usePhone(tester, textScale: scale);
      await pump(tester);

      expect(tester.takeException(), isNull);
      expect(find.text('Ticket board'), findsOneWidget);
      expect(find.text('New'), findsOneWidget);
      expect(find.text('Closed'), findsOneWidget);
      expect(find.text('Duplicate'), findsNothing);
      expect(find.text('A ticket with a long enough name to wrap'), findsOne);
      expect(find.text('SLA breached'), findsOneWidget);
      expect(find.text('Urgent'), findsOneWidget);
      expect(find.text('Showing the first 1 of 120.'), findsOneWidget);

      await tester.tap(find.text('A ticket with a long enough name to wrap'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Open ticket'), findsOneWidget);
    });
  }

  testWidgets('a card is offered every other lane and never Duplicate', (
    tester,
  ) async {
    usePhone(tester);
    final fake = await pump(tester);

    await tester.tap(find.text('A ticket with a long enough name to wrap'));
    await tester.pumpAndSettle();

    expect(find.text('Move to'), findsOneWidget);
    expect(inSheet('New'), findsNothing);
    expect(inSheet('Pending'), findsOneWidget);
    expect(inSheet('Closed'), findsOneWidget);
    expect(inSheet('Duplicate'), findsNothing);

    await tester.tap(inSheet('Pending'));
    await tester.pumpAndSettle();

    expect(fake.moves, [('case-1', 'Pending')]);
    expect(find.text('Moved to Pending.'), findsOneWidget);
  });

  testWidgets('a refused move shows the server sentence', (tester) async {
    usePhone(tester);
    final fake = await pump(tester);
    fake.refusal =
        'An approval is required before this case can be closed (rule: VIP).';

    await tester.tap(find.text('A ticket with a long enough name to wrap'));
    await tester.pumpAndSettle();
    await tester.tap(inSheet('Closed'));
    await tester.pumpAndSettle();

    expect(
      find.text(
        'An approval is required before this case can be closed (rule: VIP).',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Moved to'), findsNothing);
  });

  testWidgets('the picker lists By status and each pipeline', (tester) async {
    usePhone(tester, textScale: 1.3);
    final fake = await pump(tester);

    await tester.tap(find.text('Ticket board'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(inSheet('By status'), findsOneWidget);
    expect(inSheet('Support'), findsOneWidget);

    await tester.tap(inSheet('Support'));
    await tester.pumpAndSettle();

    expect(fake.selected, ['pipe-support']);
  });

  testWidgets('a pipeline lane shows its WIP limit as count/limit', (
    tester,
  ) async {
    usePhone(tester);
    await pump(tester, data: _pipeline);

    expect(tester.takeException(), isNull);
    expect(find.text('Support'), findsOneWidget);
    expect(find.text('1/3'), findsOneWidget);
  });
}

const _support = TicketPipelineSummary(id: 'pipe-support', name: 'Support');

const _byStatus = TicketBoardData(
  pipelines: [_support],
  lanes: [
    TicketBoardLane(
      id: 'New',
      name: 'New',
      count: 120,
      cards: [
        TicketBoardCard(
          id: 'case-1',
          name: 'A ticket with a long enough name to wrap',
          accountName: 'An account whose name is also far too long for a line',
          assignee: 'somebody.with.a.long.address@example.com',
          priority: TicketPriority.urgent,
          slaBreached: true,
        ),
      ],
    ),
    TicketBoardLane(id: 'Pending', name: 'Pending'),
    TicketBoardLane(id: 'Closed', name: 'Closed'),
  ],
);

const _pipeline = TicketBoardData(
  pipelines: [_support],
  active: _support,
  lanes: [
    TicketBoardLane(
      id: 'st-triage',
      name: 'Triage',
      isStatus: false,
      count: 1,
      wipLimit: 3,
      cards: [TicketBoardCard(id: 'case-9', name: 'Pipeline ticket')],
    ),
    TicketBoardLane(id: 'st-done', name: 'Done', isStatus: false),
  ],
);

class _FakeBoard extends TicketBoardNotifier {
  _FakeBoard(this._data);

  final TicketBoardData _data;
  final List<(String, String)> moves = [];
  final List<String?> selected = [];
  String? refusal;

  @override
  Future<TicketBoardData> build() async => _data;

  @override
  Future<void> select(String? pipelineId) async => selected.add(pipelineId);

  @override
  Future<ApiResponse<Map<String, dynamic>>> moveTicket({
    required String ticketId,
    required TicketBoardLane target,
  }) async {
    moves.add((ticketId, target.id));
    return refusal == null
        ? const ApiResponse(success: true, statusCode: 200)
        : ApiResponse(success: false, message: refusal, statusCode: 400);
  }
}
