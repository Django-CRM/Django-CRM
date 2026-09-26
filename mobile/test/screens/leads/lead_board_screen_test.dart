import 'package:bottle_crm/data/models/lead_board.dart';
import 'package:bottle_crm/providers/lead_board_provider.dart';
import 'package:bottle_crm/screens/leads/lead_board_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The lead pipeline board at a real phone width, and the move it offers.
///
/// Rendered at 390px because the default 800px test surface proves nothing
/// about a phone, and at a large text scale because that is where a row that
/// only just fits stops fitting. An overflow is a thrown FlutterError, so
/// `takeException()` being null is the assertion.
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
    LeadBoardData data = _admissions,
  }) async {
    final fake = _FakeBoard(data);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [leadBoardProvider.overrideWith(() => fake)],
        child: const MaterialApp(home: LeadBoardScreen()),
      ),
    );
    await tester.pumpAndSettle();
    return fake;
  }

  for (final scale in [1.0, 1.6]) {
    testWidgets('fits a 390px phone at text scale $scale', (tester) async {
      usePhone(tester, textScale: scale);
      await pump(tester);

      expect(tester.takeException(), isNull);
      expect(find.text('Admissions'), findsOneWidget);
      expect(find.text('No stage'), findsOneWidget);
      expect(find.text('New enquiry'), findsOneWidget);
      // The first lane is "No stage", with the lead that has not joined yet.
      expect(
        find.text('A lead with a long enough name to wrap'),
        findsOneWidget,
      );
    });
  }

  testWidgets('an org with no pipelines gets an explanation, not an error', (
    tester,
  ) async {
    usePhone(tester);
    await pump(tester, data: const LeadBoardData());

    expect(tester.takeException(), isNull);
    expect(find.text('No pipelines yet'), findsOneWidget);
  });

  testWidgets(
    'a lead in no stage can be moved into any stage, never back to none',
    (tester) async {
      usePhone(tester);
      final fake = await pump(tester);

      await tester.tap(find.text('A lead with a long enough name to wrap'));
      await tester.pumpAndSettle();

      expect(find.text('Move to'), findsOneWidget);
      final sheet = find.byType(BottomSheet);
      expect(
        find.descendant(of: sheet, matching: find.text('New enquiry')),
        findsOneWidget,
      );
      expect(
        find.descendant(of: sheet, matching: find.text('Admitted')),
        findsOneWidget,
      );
      expect(
        find.descendant(of: sheet, matching: find.text('No stage')),
        findsNothing,
      );

      await tester.tap(
        find.descendant(of: sheet, matching: find.text('Admitted')),
      );
      await tester.pumpAndSettle();

      expect(fake.moves, [('lead-unstaged', 'st-admitted')]);
      expect(find.text('Moved to Admitted.'), findsOneWidget);
    },
  );

  testWidgets('a refused move shows the server sentence', (tester) async {
    usePhone(tester);
    final fake = await pump(tester);
    fake.refusal = 'Permission denied';

    await tester.tap(find.text('A lead with a long enough name to wrap'));
    await tester.pumpAndSettle();
    await tester.tap(
      find.descendant(
        of: find.byType(BottomSheet),
        matching: find.text('New enquiry'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Permission denied'), findsOneWidget);
  });

  testWidgets('a lead in a stage is offered the other stages and No stage', (
    tester,
  ) async {
    usePhone(tester);
    await pump(tester);

    await tester.tap(find.text('New enquiry'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Asha Rao'));
    await tester.pumpAndSettle();

    final sheet = find.byType(BottomSheet);
    expect(
      find.descendant(of: sheet, matching: find.text('New enquiry')),
      findsNothing,
    );
    expect(
      find.descendant(of: sheet, matching: find.text('Admitted')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: sheet, matching: find.text('No stage')),
      findsOneWidget,
    );
  });

  for (final scale in [1.0, 1.3]) {
    testWidgets('moving a lead to No stage sends a null stage at $scale', (
      tester,
    ) async {
      usePhone(tester, textScale: scale);
      final fake = await pump(tester);

      await tester.tap(find.text('New enquiry'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Asha Rao'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.tap(
        find.descendant(
          of: find.byType(BottomSheet),
          matching: find.text('No stage'),
        ),
      );
      await tester.pumpAndSettle();

      expect(fake.moves, [('lead-1', null)]);
      expect(find.text('Moved to No stage.'), findsOneWidget);
    });
  }
}

const _admissions = LeadBoardData(
  pipelines: [LeadPipelineSummary(id: 'pipe-1', name: 'Admissions')],
  active: LeadPipelineSummary(id: 'pipe-1', name: 'Admissions'),
  lanes: [
    LeadBoardLane(
      id: '',
      name: 'No stage',
      count: 1,
      isUnstaged: true,
      cards: [
        LeadBoardCard(
          id: 'lead-unstaged',
          name: 'A lead with a long enough name to wrap',
          company: 'A company whose name is also far too long for one line',
          rating: 'WARM',
          owner: 'somebody.with.a.long.address@example.com',
          followUpOverdue: true,
        ),
      ],
    ),
    LeadBoardLane(
      id: 'st-new',
      name: 'New enquiry',
      count: 1,
      wipLimit: 5,
      cards: [LeadBoardCard(id: 'lead-1', name: 'Asha Rao', rating: 'HOT')],
    ),
    LeadBoardLane(id: 'st-admitted', name: 'Admitted'),
  ],
);

class _FakeBoard extends LeadBoardNotifier {
  _FakeBoard(this._data);

  final LeadBoardData _data;
  final List<(String, String?)> moves = [];
  String? refusal;

  @override
  Future<LeadBoardData> build() async => _data;

  @override
  Future<ApiResponse<Map<String, dynamic>>> moveLead({
    required String leadId,
    required String? stageId,
  }) async {
    moves.add((leadId, stageId));
    return refusal == null
        ? const ApiResponse(success: true, statusCode: 200)
        : ApiResponse(success: false, message: refusal, statusCode: 403);
  }
}
