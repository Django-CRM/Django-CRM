import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/deal_pipeline.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/screens/deals/deal_form_screen.dart';
import 'package:bottle_crm/widgets/common/common.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The discard prompt on this form used a hand-listed comparison that covered
/// nine of the fifteen fields a new deal submits. Name, amount and notes were
/// in it; stage, currency, close date, probability, type and source were not.
/// So a user could set up a whole deal through the pickers, press back, and
/// lose it while the form believed nothing had changed.
///
/// Probability stands in for that group here because it is the one of the six
/// a test can type into.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<void> openForm(WidgetTester tester) async {
    final router = GoRouter(
      initialLocation: '/deals',
      routes: [
        GoRoute(
          path: '/deals',
          builder: (_, _) => const Text('the deals list'),
        ),
        GoRoute(path: '/new', builder: (_, _) => const DealFormScreen()),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(() => _FakeAuth()),
          dealPipelinesProvider.overrideWith(() => _FakePipelines()),
        ],
        child: MaterialApp.router(theme: AppTheme.light, routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    router.push('/new');
    await tester.pumpAndSettle();
  }

  Future<void> pressBack(WidgetTester tester) async {
    tester.state<NavigatorState>(find.byType(Navigator).last).maybePop();
    await tester.pumpAndSettle();
  }

  testWidgets('back after editing a field the old check ignored asks first', (
    tester,
  ) async {
    await openForm(tester);

    await tester.enterText(
      find.widgetWithText(FloatingLabelInput, 'Probability (%)'),
      '85',
    );
    await pressBack(tester);

    expect(find.text('Discard changes?'), findsOneWidget);
    expect(find.text('the deals list'), findsNothing);
  });

  testWidgets('back on a form nobody touched still leaves at once', (
    tester,
  ) async {
    await openForm(tester);

    // The control, and it is the one at risk here: the create defaults fill in
    // a currency, a suggested close date and a probability after the widget is
    // built. A baseline captured before them would make every untouched form
    // look edited.
    await pressBack(tester);

    expect(find.text('Discard changes?'), findsNothing);
    expect(find.text('the deals list'), findsOneWidget);
  });

  testWidgets('typing and then undoing it is not a change', (tester) async {
    await openForm(tester);
    final field = find.widgetWithText(FloatingLabelInput, 'Probability (%)');

    await tester.enterText(field, '85');
    await tester.enterText(field, '10');
    await pressBack(tester);

    // 10 is the default for the opening stage, so the form is back where it
    // started. Comparing against a snapshot gets this right for free, where a
    // dirty flag would have latched.
    expect(find.text('Discard changes?'), findsNothing);
  });
}

/// The seeded pipeline, so a new deal opens in Prospecting at 10%.
class _FakePipelines extends DealPipelinesNotifier {
  @override
  Future<List<DealPipeline>> build() async => [DealPipeline.legacy];
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
