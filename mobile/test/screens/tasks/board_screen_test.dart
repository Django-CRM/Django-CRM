import 'package:bottle_crm/data/models/board.dart';
import 'package:bottle_crm/data/models/lead.dart' show Priority;
import 'package:bottle_crm/providers/board_provider.dart';
import 'package:bottle_crm/screens/tasks/board_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The kanban board with cards on it, rendered at a real phone width.
///
/// A card carries a priority-coloured left accent and rounded corners. Flutter
/// refuses to paint a `Border` whose sides differ when it also has a
/// `borderRadius`, and that refusal is a thrown FlutterError, so
/// `takeException()` being null is the assertion. It also catches an overflow
/// at 390px or at a large text scale.
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

  for (final scale in [1.0, 1.6]) {
    testWidgets('cards paint on a 390px phone at text scale $scale', (
      tester,
    ) async {
      usePhone(tester, textScale: scale);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [boardProvider.overrideWith(_FakeBoard.new)],
          child: const MaterialApp(home: BoardScreen()),
        ),
      );
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Launch plan'), findsOneWidget);
      expect(find.text('Draft the announcement'), findsOneWidget);
      expect(find.text('Book the venue'), findsOneWidget);
      // Each card keeps its priority accent.
      for (final priority in [Priority.high, Priority.low]) {
        expect(
          find.byWidgetPredicate(
            (w) => w is ColoredBox && w.color == priority.color,
          ),
          findsOneWidget,
        );
      }
    });
  }
}

class _FakeBoard extends BoardNotifier {
  @override
  Future<BoardData> build() async => BoardData(
    boards: const [BoardSummary(id: 'b1', name: 'Launch plan')],
    active: const BoardSummary(id: 'b1', name: 'Launch plan'),
    lanes: [
      BoardLane(
        id: 'l1',
        name: 'To Do',
        order: 0,
        cards: [
          BoardCard(
            id: 'c1',
            laneId: 'l1',
            title: 'Draft the announcement',
            order: 0,
            priority: Priority.high,
            description: 'A description long enough to wrap onto a second line',
            dueDate: DateTime(2026, 10, 1),
            isOverdue: true,
            accountName: 'Acme',
            assignees: const ['Ada Lovelace'],
          ),
          const BoardCard(
            id: 'c2',
            laneId: 'l1',
            title: 'Book the venue',
            order: 1,
            priority: Priority.low,
          ),
        ],
      ),
    ],
  );
}
