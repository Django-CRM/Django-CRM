import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/deal.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/widgets/cards/deal_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The aging badge on a deal card, at a phone's width and with large text.
///
/// The words are the server's verdict (`aging_status`), never a threshold the
/// phone works out, and they match the web board's. An overflow is a thrown
/// FlutterError, so `takeException()` being null is the layout assertion.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Deal deal(String status, int days) => Deal.fromJson({
    'id': 'd-$status',
    'name': 'Multi-site renewal for the regional hospital group, phase two',
    'stage': 'DEMO_BOOKED',
    'stage_label': 'Demo booked',
    'stage_kind': 'open',
    'amount': '125000',
    'aging_status': status,
    'days_in_stage': days,
    'account': {'id': 'a1', 'name': 'Northern Regional Hospitals Trust'},
  });

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
                DealCard(deal: deal('yellow', 12)),
                DealCard(deal: deal('red', 130)),
                DealCard(deal: deal('green', 2)),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits 390px at $scale, one badge per aging deal', (
      tester,
    ) async {
      await pump(tester, scale);

      expect(tester.takeException(), isNull);
      expect(find.text('Past expected · 12d'), findsOneWidget);
      expect(find.text('Stalled · 130d'), findsOneWidget);
      expect(find.textContaining('· 2d'), findsNothing);
    });
  }

  testWidgets('stalled reads in danger colours, past expected in warning', (
    tester,
  ) async {
    await pump(tester, 1.0);

    Color colorOf(String text) =>
        tester.widget<Text>(find.text(text)).style!.color!;
    expect(colorOf('Stalled · 130d'), AppColors.danger600);
    expect(colorOf('Past expected · 12d'), AppColors.warning700);
  });
}
