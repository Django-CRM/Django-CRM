import 'package:bottle_crm/data/models/account.dart';
import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/screens/accounts/account_detail_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The account panel's money, at a 390px phone.
///
/// Deals and invoices carry their own currency and there are no exchange
/// rates, so the server sends the money per currency. The panel shows one
/// figure per currency and never a sum.
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

  Widget app(Map<String, dynamic> rollups) => ProviderScope(
    overrides: [
      accountsProvider.overrideWith(() => _FakeAccounts(rollups)),
      isOrgAdminProvider.overrideWithValue(false),
      currentUserProvider.overrideWithValue(null),
    ],
    child: const MaterialApp(home: AccountDetailScreen(accountId: 'a1')),
  );

  Future<void> pump(
    WidgetTester tester,
    Widget widget, {
    double textScale = 1.0,
  }) async {
    usePhone(tester, textScale: textScale);
    await tester.pumpWidget(widget);
    await tester.pumpAndSettle();
  }

  const mixed = {
    'won_amount': null,
    'won_count': 2,
    'open_pipeline': null,
    'open_deal_count': 1,
    'overdue_amount': null,
    'open_tickets': 0,
    'by_currency': [
      {
        'currency': 'EUR',
        'won_amount': '300.00',
        'open_pipeline': '0',
        'overdue_amount': '70.00',
      },
      {
        'currency': 'USD',
        'won_amount': '1000.00',
        'open_pipeline': '50.00',
        'overdue_amount': '0',
      },
    ],
  };

  testWidgets('two currencies show a figure each, never a sum', (tester) async {
    await pump(tester, app(mixed));

    expect(find.textContaining('€300'), findsOneWidget);
    expect(find.textContaining('\$1'), findsOneWidget);
    expect(find.textContaining('1.3K'), findsNothing);
    expect(find.textContaining('\$50'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('two currencies fit with the system font scaled up', (
    tester,
  ) async {
    await pump(tester, app(mixed), textScale: 1.5);
    expect(tester.takeException(), isNull);
  });

  testWidgets('one currency reads as it always did', (tester) async {
    await pump(
      tester,
      app({
        'won_amount': '500.00',
        'won_count': 1,
        'open_pipeline': '0',
        'open_deal_count': 0,
        'overdue_amount': '0',
        'open_tickets': 0,
        'by_currency': [
          {
            'currency': 'EUR',
            'won_amount': '500.00',
            'open_pipeline': '0',
            'overdue_amount': '0',
          },
        ],
      }),
    );

    expect(find.text('€500'), findsOneWidget);
    // Nothing open: zero, in the account's own currency (USD here).
    expect(find.text('\$0.00'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class _FakeAccounts extends AccountsNotifier {
  _FakeAccounts(this.rollups);

  final Map<String, dynamic> rollups;

  @override
  Future<AccountsListData> build() async => const AccountsListData();

  @override
  Future<Account?> getAccount(String id) async => Account.fromJson({
    'id': id,
    'name': 'Northwind',
    'currency': 'USD',
    'rollups': rollups,
  });
}
