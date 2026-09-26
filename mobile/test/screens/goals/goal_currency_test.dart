import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/dashboard_data.dart';
import 'package:bottle_crm/data/models/sales_goal.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/dashboard_provider.dart';
import 'package:bottle_crm/providers/goals_provider.dart';
import 'package:bottle_crm/screens/dashboard/dashboard_screen.dart';
import 'package:bottle_crm/screens/goals/goal_form_screen.dart';
import 'package:bottle_crm/screens/goals/goal_history_screen.dart';
import 'package:bottle_crm/screens/goals/goals_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// A goal's target is one number, so a goal has one currency.
///
/// Progress used to add every won deal whatever its currency, and both clients
/// priced every goal in the org's currency. The API now counts a REVENUE goal
/// in its own currency and says which one, so each figure here has to be
/// printed in the currency the server counted it in, and the form has to let
/// an admin choose it.
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

  Future<void> pump(
    WidgetTester tester,
    Widget app, {
    double textScale = 1.0,
  }) async {
    usePhone(tester, textScale: textScale);
    await tester.pumpWidget(app);
    await tester.pumpAndSettle();
  }

  /// The form pushed on top of a parent route, so a successful save can pop.
  Widget routedForm(Widget screen) => MaterialApp.router(
    theme: AppTheme.light,
    routerConfig: GoRouter(
      initialLocation: '/here/edit',
      routes: [
        GoRoute(
          path: '/here',
          builder: (_, _) => const Scaffold(body: Text('goals list')),
          routes: [GoRoute(path: 'edit', builder: (_, _) => screen)],
        ),
      ],
    ),
  );

  Widget formApp(
    _CapturingGoals capture, {
    String? goalId,
    Map<String, dynamic>? goal,
    String orgCurrency = 'EUR',
  }) => ProviderScope(
    overrides: [
      authProvider.overrideWith(() => _FakeAuth(orgCurrency)),
      goalsProvider.overrideWith(() => capture),
      isOrgAdminProvider.overrideWithValue(true),
      if (goalId != null)
        goalProvider(
          goalId,
        ).overrideWith((ref) async => SalesGoal.fromJson(goal!)),
    ],
    child: routedForm(GoalFormScreen(goalId: goalId)),
  );

  /// Drag the form until [label] is built, then tap it. A ListView builds only
  /// what is near the viewport, so on a 390px phone the button below the fold
  /// is not in the tree yet.
  Future<void> save(WidgetTester tester, String label) async {
    final button = find.text(label);
    for (var i = 0; i < 10 && button.evaluate().isEmpty; i++) {
      await tester.drag(find.byType(ListView).first, const Offset(0, -320));
      await tester.pumpAndSettle();
    }
    await tester.ensureVisible(button);
    await tester.pumpAndSettle();
    await tester.tap(button);
    await tester.pumpAndSettle();
  }

  group('goal form currency', () {
    testWidgets('a new goal opens in the org currency and sends it', (
      tester,
    ) async {
      final capture = _CapturingGoals();
      await pump(tester, formApp(capture));

      expect(find.text('Currency'), findsOneWidget);
      expect(find.text('EUR'), findsOneWidget);

      await tester.enterText(find.widgetWithText(TextField, 'Goal name'), 'Q4');
      await tester.enterText(find.widgetWithText(TextField, 'Target'), '5000');
      await save(tester, 'Create goal');

      expect(capture.created?['currency'], 'EUR');
    });

    testWidgets('an admin can pick another currency', (tester) async {
      final capture = _CapturingGoals();
      await pump(tester, formApp(capture));

      await tester.tap(find.text('EUR'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('GBP').last);
      await tester.pumpAndSettle();

      await tester.enterText(find.widgetWithText(TextField, 'Goal name'), 'Q4');
      await tester.enterText(find.widgetWithText(TextField, 'Target'), '5000');
      await save(tester, 'Create goal');

      expect(capture.created?['currency'], 'GBP');
    });

    testWidgets('an existing goal opens in its own currency, not the org one', (
      tester,
    ) async {
      final capture = _CapturingGoals();
      await pump(
        tester,
        formApp(
          capture,
          goalId: 'g1',
          goal: const {
            'id': 'g1',
            'name': 'Q3 revenue',
            'goal_type': 'REVENUE',
            'currency': 'INR',
            'target_value': '250000.00',
            'period_type': 'QUARTERLY',
            'period_start': '2026-07-01',
            'period_end': '2026-09-30',
            'is_active': true,
          },
        ),
      );

      expect(find.text('INR'), findsOneWidget);
      await save(tester, 'Save changes');
      expect(capture.updated?['currency'], 'INR');
    });

    testWidgets('a count goal does not offer a currency', (tester) async {
      // Deals and activities are counts: a currency on one has nothing to
      // change, so offering the choice would suggest otherwise.
      final capture = _CapturingGoals();
      await pump(
        tester,
        formApp(
          capture,
          goalId: 'g2',
          goal: const {
            'id': 'g2',
            'name': 'New logos',
            'goal_type': 'DEALS_CLOSED',
            'currency': 'USD',
            'target_value': '8',
            'period_type': 'MONTHLY',
            'period_start': '2026-08-01',
            'period_end': '2026-08-31',
            'is_active': true,
          },
        ),
      );
      expect(find.text('Currency'), findsNothing);
    });

    testWidgets('fits a phone with the system font scaled up', (tester) async {
      await pump(tester, formApp(_CapturingGoals()), textScale: 1.5);
      expect(find.text('Currency'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  Widget goalsApp() => ProviderScope(
    overrides: [
      goalsProvider.overrideWith(_FakeGoals.new),
      isOrgAdminProvider.overrideWithValue(false),
    ],
    child: MaterialApp(theme: AppTheme.light, home: const GoalsScreen()),
  );

  group('goals list currency', () {
    testWidgets('prices each goal in its own currency', (tester) async {
      await pump(tester, goalsApp());
      // The EUR goal's row, not the org's dollar default.
      expect(find.textContaining('€90K of €250K'), findsOneWidget);
      expect(find.textContaining('£1K of £2K'), findsOneWidget);
    });

    testWidgets('keeps the currencies apart in the header totals', (
      tester,
    ) async {
      // One figure per currency, and the deals goal in none of them.
      await pump(tester, goalsApp());
      expect(find.text('€250K · £2K'), findsOneWidget);
      expect(find.text('€90K · £1K'), findsOneWidget);
    });

    testWidgets('prints a deals row on the board as a count', (tester) async {
      await pump(tester, goalsApp());
      await tester.scrollUntilVisible(
        find.text('Grace Hopper'),
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView),
              matching: find.byType(Scrollable),
            )
            .first,
      );
      expect(find.text('3 deals of 8 deals'), findsOneWidget);
      expect(find.text('€90K of €250K'), findsOneWidget);
    });

    testWidgets('fits with the system font scaled up', (tester) async {
      await pump(tester, goalsApp(), textScale: 1.5);
      expect(tester.takeException(), isNull);
    });
  });

  Widget historyApp() => ProviderScope(
    overrides: [
      goalHistoryProvider.overrideWith(
        (ref) async => [
          GoalHistoryPeriod.fromJson(const {
            'period_start': '2026-04-01',
            'period_end': '2026-06-30',
            'period_type': 'QUARTERLY',
            'goal_type': 'REVENUE',
            'currency': 'GBP',
            'goals_count': 1,
            'attained_count': 0,
            'target': 5000,
            'achieved': 2000,
            'percent': 40,
            'goals': [
              {
                'id': 'h1',
                'name': 'Q2 UK',
                'goal_type': 'REVENUE',
                'currency': 'GBP',
                'target_value': '5000.00',
                'progress_value': 2000,
                'progress_percent': 40,
              },
            ],
          }),
        ],
      ),
    ],
    child: MaterialApp(theme: AppTheme.light, home: const GoalHistoryScreen()),
  );

  group('goal history currency', () {
    testWidgets('prices a period in the currency it was counted in', (
      tester,
    ) async {
      await pump(tester, historyApp());
      expect(find.textContaining('£2K'), findsWidgets);
      expect(find.textContaining(r'$'), findsNothing);
    });

    testWidgets('fits with the system font scaled up', (tester) async {
      await pump(tester, historyApp(), textScale: 1.5);
      expect(tester.takeException(), isNull);
    });
  });

  testWidgets('the dashboard strip prices a goal in its own currency', (
    tester,
  ) async {
    await pump(
      tester,
      ProviderScope(
        overrides: [
          authProvider.overrideWith(() => _FakeAuth('USD')),
          dashboardProvider.overrideWith(
            () => _FakeDashboard(
              DashboardData(
                revenueMetrics: const RevenueMetrics(currency: 'USD'),
                goalSummary: [
                  DashboardGoal.fromJson(const {
                    'id': 'g1',
                    'name': 'Q3 Germany',
                    'goal_type': 'REVENUE',
                    'currency': 'EUR',
                    'target_value': 4000,
                    'progress_value': 1500,
                    'progress_percent': 37,
                    'status': 'on_track',
                  }),
                ],
              ),
            ),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const DashboardScreen(),
        ),
      ),
    );
    await tester.scrollUntilVisible(
      find.text('Q3 Germany'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.textContaining('€1.5K of €4K'), findsOneWidget);
  });
}

class _CapturingGoals extends GoalsNotifier {
  Map<String, dynamic>? created;
  Map<String, dynamic>? updated;

  @override
  Future<GoalsData> build() async => const GoalsData();

  @override
  Future<ApiResponse<Map<String, dynamic>>> createGoal(
    Map<String, dynamic> body,
  ) async {
    created = body;
    return ApiResponse(success: true, data: const {}, statusCode: 201);
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> updateGoal(
    String id,
    Map<String, dynamic> body,
  ) async {
    updated = body;
    return ApiResponse(success: true, data: const {}, statusCode: 200);
  }
}

class _FakeGoals extends GoalsNotifier {
  @override
  Future<GoalsData> build() async {
    final goals = [
      SalesGoal.fromJson(const {
        'id': 'g1',
        'name': 'Q3 Germany',
        'goal_type': 'REVENUE',
        'currency': 'EUR',
        'target_value': '250000.00',
        'period_type': 'QUARTERLY',
        'period_start': '2026-07-01',
        'period_end': '2026-09-30',
        'is_active': true,
        'progress_value': 90000,
        'progress_percent': 36,
        'status': 'behind',
      }),
      SalesGoal.fromJson(const {
        'id': 'g2',
        'name': 'Q3 UK',
        'goal_type': 'REVENUE',
        'currency': 'GBP',
        'target_value': '2000.00',
        'period_type': 'QUARTERLY',
        'period_start': '2026-07-01',
        'period_end': '2026-09-30',
        'is_active': true,
        'progress_value': 1000,
        'progress_percent': 50,
        'status': 'on_track',
      }),
      SalesGoal.fromJson(const {
        'id': 'g3',
        'name': 'New logos',
        'goal_type': 'DEALS_CLOSED',
        'currency': 'EUR',
        'target_value': '8',
        'period_type': 'MONTHLY',
        'period_start': '2026-08-01',
        'period_end': '2026-08-31',
        'is_active': true,
        'progress_value': 3,
        'progress_percent': 37,
        'status': 'on_track',
      }),
    ];
    return GoalsData(
      goals: goals,
      totals: goalTotals(goals, today: '2026-08-15'),
      leaderboard: [
        GoalLeaderRow.fromJson(const {
          'rank': 1,
          'goal_id': 'g1',
          'goal_type': 'REVENUE',
          'currency': 'EUR',
          'user': {'id': 'p1', 'name': 'Ada Lovelace'},
          'target': 250000.0,
          'achieved': 90000.0,
          'percent': 36,
        }),
        GoalLeaderRow.fromJson(const {
          'rank': 2,
          'goal_id': 'g3',
          'goal_type': 'DEALS_CLOSED',
          'currency': 'EUR',
          'user': {'id': 'p2', 'name': 'Grace Hopper'},
          'target': 8.0,
          'achieved': 3.0,
          'percent': 37,
        }),
      ],
    );
  }
}

class _FakeDashboard extends DashboardNotifier {
  _FakeDashboard(this.data);

  final DashboardData data;

  @override
  Future<DashboardData> build() async => data;

  @override
  Future<void> refresh() async {}
}

class _FakeAuth extends AuthNotifier {
  _FakeAuth(this.currency);

  final String currency;

  @override
  AuthState build() {
    final org = Organization(
      id: 'org-1',
      name: 'Test Org',
      role: 'ADMIN',
      defaultCurrency: currency,
    );
    return AuthState(
      user: const AuthUser(id: 'user-1', email: 'user@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}
