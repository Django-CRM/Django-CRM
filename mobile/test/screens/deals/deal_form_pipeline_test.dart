import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/screens/deals/deal_form_screen.dart';
import 'package:bottle_crm/widgets/common/common.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The deal form picks a pipeline, then a stage of that pipeline, and sends
/// both. Switching pipeline lands on the new one's first open stage, because
/// the server refuses a pipeline change that names no stage there. A won stage
/// asks for an amount by kind, whatever the stage is called.
final _sales = DealPipeline.fromJson({
  'id': 'p1',
  'name': 'Sales',
  'is_default': true,
  'stages': [
    {'id': 's1', 'code': 'PROSPECTING', 'label': 'Prospecting', 'kind': 'open'},
    {
      'id': 's2',
      'code': 'SIGNED',
      'label': 'Signed',
      'kind': 'won',
      'order': 1,
    },
    {'id': 's3', 'code': 'LOST', 'label': 'Lost', 'kind': 'lost', 'order': 2},
  ],
});

final _partners = DealPipeline.fromJson({
  'id': 'p2',
  'name': 'Partners',
  'stages': [
    {
      'id': 't1',
      'code': 'INTRO_CALL',
      'label': 'Intro call',
      'kind': 'open',
      'expected_days': 5,
    },
    {'id': 't2', 'code': 'WON', 'label': 'Won', 'kind': 'won', 'order': 1},
    {'id': 't3', 'code': 'GONE', 'label': 'Gone', 'kind': 'lost', 'order': 2},
  ],
});

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeDeals deals;

  setUp(() => deals = _FakeDeals());

  Future<void> pumpForm(
    WidgetTester tester, {
    List<DealPipeline>? pipelines,
    double textScale = 1.0,
  }) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final router = GoRouter(
      initialLocation: '/deals',
      routes: [
        GoRoute(path: '/deals', builder: (_, _) => const Text('the list')),
        GoRoute(path: '/new', builder: (_, _) => const DealFormScreen()),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(() => _FakeAuth()),
          dealsProvider.overrideWith(() => deals),
          dealPipelinesProvider.overrideWith(
            () => _FakePipelines(pipelines ?? [_sales, _partners]),
          ),
        ],
        child: MaterialApp.router(theme: AppTheme.light, routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    router.push('/new');
    await tester.pumpAndSettle();
  }

  Future<void> tapField(WidgetTester tester, String text) async {
    await tester.ensureVisible(find.text(text));
    await tester.pumpAndSettle();
    await tester.tap(find.text(text));
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits 390px at $scale with the pipeline and stage pickers', (
      tester,
    ) async {
      await pumpForm(tester, textScale: scale);

      expect(tester.takeException(), isNull);
      expect(find.text('Pipeline'), findsOneWidget);
      expect(find.text('Sales'), findsOneWidget);
      expect(find.text('Prospecting'), findsOneWidget);
    });
  }

  testWidgets('with one pipeline there is no pipeline picker', (tester) async {
    await pumpForm(tester, pipelines: [_sales]);

    expect(find.text('Pipeline'), findsNothing);
    expect(find.text('Prospecting'), findsOneWidget);
  });

  testWidgets('switching pipeline resets the stage and sends both', (
    tester,
  ) async {
    await pumpForm(tester);

    await tapField(tester, 'Sales');
    await tester.tap(find.text('Partners'));
    await tester.pumpAndSettle();

    expect(find.text('Intro call'), findsOneWidget);
    expect(find.text('Prospecting'), findsNothing);

    await tester.enterText(
      find.widgetWithText(FloatingLabelInput, 'Deal Name *'),
      'Reseller deal',
    );
    await tester.tap(find.text('Create Deal'));
    await tester.pumpAndSettle();

    final sent = deals.created.single;
    expect(sent['pipeline'], 'p2');
    expect(sent['stage'], 'INTRO_CALL');
    expect(find.text('the list'), findsOneWidget);
  });

  testWidgets('a won stage without an amount is caught before sending', (
    tester,
  ) async {
    await pumpForm(tester);

    await tapField(tester, 'Prospecting');
    await tester.tap(find.text('Signed'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(FloatingLabelInput, 'Deal Name *'),
      'Big one',
    );
    await tester.tap(find.text('Create Deal'));
    await tester.pumpAndSettle();

    expect(find.text('A deal in Signed needs an amount.'), findsOneWidget);
    expect(deals.created, isEmpty);
  });
}

class _FakePipelines extends DealPipelinesNotifier {
  _FakePipelines(this.pipelines);

  final List<DealPipeline> pipelines;

  @override
  Future<List<DealPipeline>> build() async => pipelines;
}

class _FakeDeals extends DealsNotifier {
  final List<Map<String, dynamic>> created = [];

  @override
  Future<DealsListData> build() async => const DealsListData();

  @override
  Future<({bool success, String? error, Deal? deal})> createDeal(
    Deal deal,
  ) async {
    created.add(deal.toJson());
    return (success: true, error: null, deal: null);
  }
}

class _FakeAuth extends AuthNotifier {
  @override
  AuthState build() {
    const org = Organization(
      id: 'org-1',
      name: 'Test Org',
      role: 'ADMIN',
      defaultCurrency: 'USD',
    );
    return AuthState(
      user: const AuthUser(id: 'user-1', email: 'user@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}
