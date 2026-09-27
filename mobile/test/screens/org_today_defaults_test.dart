import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/data/models/sales_goal.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/goals_provider.dart';
import 'package:bottle_crm/providers/invoice_extras_provider.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/screens/goals/goal_form_screen.dart';
import 'package:bottle_crm/screens/invoices/new_estimate_screen.dart';
import 'package:bottle_crm/screens/invoices/new_invoice_screen.dart';
import 'package:bottle_crm/services/org_date.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../support/org_zone.dart';

/// A new invoice, estimate or goal opens dated by the org's day, not the
/// phone's. The web does the same with `todayIn(data.org.timezone)`.
///
/// The org's zone is picked so that its day differs from the phone's at the
/// moment the test runs: Kiritimati (UTC+14) and UTC-12 are 26 hours apart,
/// so at every instant at least one of them is on another date than the
/// phone. A form still reading the phone's clock fails here.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late FakeAuthApi api;
  late String zone;
  late DateTime orgDay;

  setUp(() async {
    final phoneDay = DateUtils.dateOnly(DateTime.now());
    zone = [
      'Pacific/Kiritimati',
      'Etc/GMT+12',
    ].firstWhere((z) => todayIn(z) != phoneDay);
    api = await useOrgZone(zone);
    orgDay = todayIn(zone);
    expect(orgDay, isNot(phoneDay));
  });

  tearDown(() => api.uninstall());

  void usePhone(WidgetTester tester, double textScale) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<void> pump(
    WidgetTester tester,
    Widget screen, {
    required double textScale,
    List overrides = const [],
  }) async {
    usePhone(tester, textScale);
    final router = GoRouter(
      initialLocation: '/start',
      routes: [
        GoRoute(path: '/start', builder: (_, _) => const Text('started')),
        GoRoute(path: '/form', builder: (_, _) => screen),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(_FakeAuth.new),
          isOrgAdminProvider.overrideWithValue(true),
          accountsLookupProvider.overrideWith(_FakeAccounts.new),
          contactsLookupProvider.overrideWith(_FakeContacts.new),
          opportunityEntityOptionsProvider.overrideWithValue(
            const AsyncValue.data(<EntityLookup>[]),
          ),
          productsProvider.overrideWith(_FakeProducts.new),
          ...overrides.cast(),
        ],
        child: MaterialApp.router(theme: AppTheme.light, routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    router.push('/form');
    await tester.pumpAndSettle();
  }

  final shown = DateFormat('d MMM yyyy');

  for (final scale in [1.0, 1.3]) {
    testWidgets('a new invoice is issued on the org day, 390px x$scale', (
      tester,
    ) async {
      await pump(tester, const NewInvoiceScreen(), textScale: scale);

      expect(find.text(shown.format(orgDay)), findsOneWidget);
      expect(
        find.text(shown.format(DateUtils.dateOnly(DateTime.now()))),
        findsNothing,
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('a new estimate runs 30 days from the org day, 390px '
        'x$scale', (tester) async {
      await pump(tester, const NewEstimateScreen(), textScale: scale);

      expect(find.text(shown.format(orgDay)), findsOneWidget);
      expect(find.text(shown.format(addDays(orgDay, 30))), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a new goal starts on the org day and sends it, 390px '
        'x$scale', (tester) async {
      final goals = _CapturingGoals();
      await pump(
        tester,
        const GoalFormScreen(),
        textScale: scale,
        overrides: [goalsProvider.overrideWith(() => goals)],
      );

      final start = goalToday(orgDay);
      final end = goalToday(
        DateTime(orgDay.year, orgDay.month + 1, orgDay.day),
      );
      expect(find.text(start), findsOneWidget);
      expect(find.text(end), findsOneWidget);
      expect(tester.takeException(), isNull);

      await tester.enterText(find.widgetWithText(TextField, 'Goal name'), 'Q');
      await tester.enterText(find.widgetWithText(TextField, 'Target'), '10');
      final create = find.text('Create goal');
      for (var i = 0; i < 10 && create.evaluate().isEmpty; i++) {
        await tester.drag(find.byType(ListView).first, const Offset(0, -320));
        await tester.pumpAndSettle();
      }
      await tester.ensureVisible(create);
      await tester.pumpAndSettle();
      await tester.tap(create);
      await tester.pumpAndSettle();

      expect(goals.created?['period_start'], start);
      expect(goals.created?['period_end'], end);
    });
  }
}

class _FakeAuth extends AuthNotifier {
  @override
  AuthState build() {
    const org = Organization(
      id: 'org-1',
      name: 'Acme',
      role: 'ADMIN',
      isOrganizationAdmin: true,
      defaultCurrency: 'USD',
    );
    return const AuthState(
      user: AuthUser(id: 'user-1', email: 'user@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}

class _FakeAccounts extends AccountsLookupNotifier {
  @override
  Future<List<AccountLookup>> build() async => const [
    AccountLookup(id: 'acc-1', name: 'Northwind'),
  ];
}

class _FakeContacts extends ContactsLookupNotifier {
  @override
  Future<List<ContactLookup>> build() async => const [];
}

class _FakeProducts extends ProductsNotifier {
  @override
  Future<List<Product>> build() async => const [];
}

class _CapturingGoals extends GoalsNotifier {
  Map<String, dynamic>? created;

  @override
  Future<GoalsData> build() async => const GoalsData();

  @override
  Future<ApiResponse<Map<String, dynamic>>> createGoal(
    Map<String, dynamic> body,
  ) async {
    created = body;
    return ApiResponse(success: true, data: const {}, statusCode: 201);
  }
}
