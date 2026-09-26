import 'package:bottle_crm/data/models/invoice_report.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/invoice_extras_provider.dart';
import 'package:bottle_crm/screens/invoices/invoice_reports_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The reports screen in an org that invoices in two currencies, rendered at a
/// 390px phone. Every amount must be in the picked currency, never a sum.
void main() {
  const reports = InvoiceReports(
    dashboard: InvoiceDashboard(
      invoiceCount: 3,
      money: {
        'EUR': DashboardMoney(totalInvoiced: 800),
        'USD': DashboardMoney(totalInvoiced: 10000),
      },
    ),
    aging: {},
  );

  Future<void> pump(
    WidgetTester tester, {
    double textScale = 1.0,
    InvoiceReports data = reports,
  }) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          invoiceReportsProvider.overrideWith((ref) async => data),
          selectedOrgProvider.overrideWithValue(null),
        ],
        child: const MaterialApp(home: InvoiceReportsScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('shows one currency at a time and switches on tap', (
    tester,
  ) async {
    await pump(tester);
    expect(tester.takeException(), isNull);

    // No org currency set, so the first one invoiced in is shown.
    expect(find.text('€800.00'), findsOneWidget);
    expect(find.textContaining('10,800'), findsNothing);

    await tester.tap(find.widgetWithText(ChoiceChip, 'USD'));
    await tester.pumpAndSettle();
    expect(find.text('\$10,000.00'), findsOneWidget);
    expect(find.text('€800.00'), findsNothing);
  });

  testWidgets('the picker fits with the system font scaled up', (tester) async {
    await pump(tester, textScale: 1.5);
    expect(tester.takeException(), isNull);
    expect(
      tester.getSize(find.widgetWithText(ChoiceChip, 'EUR')).height,
      greaterThanOrEqualTo(44),
    );
  });

  testWidgets('a currency the app does not list is named, never shown as \$', (
    tester,
  ) async {
    await pump(
      tester,
      data: const InvoiceReports(
        dashboard: InvoiceDashboard(
          invoiceCount: 1,
          money: {'NZD': DashboardMoney(totalInvoiced: 800)},
        ),
        aging: {},
      ),
    );
    expect(tester.takeException(), isNull);

    expect(find.text('NZD 800.00'), findsOneWidget);
    expect(find.text('\$800.00'), findsNothing);
  });
}
