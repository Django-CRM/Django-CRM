import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/deal.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/widgets/cards/deal_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// G31: a deal card shows the deal's next open task, or flags that it has
/// none. The server counts only tasks this user can open; the card renders
/// what it is sent, and nothing at all when the response never computed it
/// (the deal detail), so a deal is never flagged on a guess.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Deal deal(
    String id, {
    Object? next = const _Absent(),
    String kind = 'open',
  }) => Deal.fromJson({
    'id': id,
    'name': 'Multi-site renewal for the regional hospital group, phase two',
    'stage': kind == 'open' ? 'DEMO_BOOKED' : 'SIGNED',
    'stage_label': 'Demo booked',
    'stage_kind': kind,
    'amount': '125000',
    'aging_status': 'green',
    'account': {'id': 'a1', 'name': 'Northern Regional Hospitals Trust'},
    if (next is! _Absent) 'next_activity': next,
  });

  final longTask = {
    'id': 't1',
    'title':
        'Send the revised proposal with the three-year pricing option and '
        'the procurement pack the trust asked for',
    'due_date': '2099-10-01',
  };

  Future<void> pump(WidgetTester tester, double textScale) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [usersProvider.overrideWithValue(const [])],
        child: MaterialApp(
          theme: AppTheme.light,
          home: Scaffold(
            body: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                DealCard(deal: deal('scheduled', next: longTask)),
                DealCard(deal: deal('none', next: null)),
                DealCard(
                  deal: deal(
                    'late',
                    next: {
                      'id': 't2',
                      'title': 'Chase',
                      'due_date': '2000-01-01',
                    },
                  ),
                ),
                DealCard(
                  deal: deal(
                    'undated',
                    next: {'id': 't3', 'title': 'Call', 'due_date': null},
                  ),
                ),
                DealCard(deal: deal('unknown')),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits 390px at $scale with a long task title', (tester) async {
      await pump(tester, scale);

      expect(tester.takeException(), isNull);
      expect(find.byType(DealNextStep), findsNWidgets(4));
      expect(find.textContaining('Send the revised proposal'), findsOneWidget);
      expect(find.text('No next step'), findsOneWidget);
      expect(find.text('Call · no date'), findsOneWidget);
    });
  }

  testWidgets('no task and an overdue task read in danger, a plan does not', (
    tester,
  ) async {
    await pump(tester, 1.0);

    Color colorOf(Finder f) => tester.widget<Text>(f).style!.color!;
    expect(colorOf(find.text('No next step')), AppColors.danger600);
    expect(colorOf(find.text('Chase · 1 Jan')), AppColors.danger600);
    expect(
      colorOf(find.textContaining('Send the revised proposal')),
      AppColors.textSecondary,
    );
  });

  for (final kind in ['won', 'lost']) {
    testWidgets('a $kind deal shows no next step, flag or task', (
      tester,
    ) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [usersProvider.overrideWithValue(const [])],
          child: MaterialApp(
            theme: AppTheme.light,
            home: Scaffold(
              body: ListView(
                children: [
                  // The stage code is not a won/lost code: the kind decides.
                  DealCard(deal: deal('none', next: null, kind: kind)),
                  DealCard(
                    deal: deal('task', next: longTask, kind: kind),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byType(DealNextStep), findsNothing);
      expect(find.text('No next step'), findsNothing);
    });
  }

  test('parses the list payload, and knows when it was not sent', () {
    final scheduled = deal('a', next: longTask);
    expect(scheduled.nextActivityKnown, isTrue);
    expect(scheduled.nextActivity!.id, 't1');
    expect(scheduled.nextActivity!.dueDate, DateTime(2099, 10, 1));

    final none = deal('b', next: null);
    expect(none.nextActivityKnown, isTrue);
    expect(none.nextActivity, isNull);

    final unknown = deal('c');
    expect(unknown.nextActivityKnown, isFalse);

    // A board move keeps it: moving a deal does not change its tasks.
    final moved = scheduled.copyWith(stage: 'SIGNED');
    expect(moved.nextActivityKnown, isTrue);
    expect(moved.nextActivity!.title, scheduled.nextActivity!.title);
  });
}

class _Absent {
  const _Absent();
}
