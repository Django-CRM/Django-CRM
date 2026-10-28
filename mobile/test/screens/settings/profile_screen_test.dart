import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/profile.dart';
import 'package:bottle_crm/data/models/access_token.dart';
import 'package:bottle_crm/providers/profile_provider.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/screens/settings/profile_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The profile screen is an edit form too, and it had the same hole as the
/// other five: back threw the edits away without asking. Its rule differs from
/// theirs, because the fields only exist while edit mode is on.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<void> openProfile(WidgetTester tester, {bool bare = false}) async {
    final router = GoRouter(
      initialLocation: '/more',
      routes: [
        GoRoute(path: '/more', builder: (_, _) => const Text('the more sheet')),
        GoRoute(path: '/profile', builder: (_, _) => const ProfileScreen()),
        GoRoute(
          path: '/more/profile/tokens',
          builder: (_, _) => const Text('your tokens'),
        ),
        GoRoute(
          path: '/more/profile/calendar-feed',
          builder: (_, _) => const Text('your calendar feed'),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          profileProvider.overrideWith(
            () => bare ? _FakeBareProfile() : _FakeProfile(),
          ),
          // The token count on this screen watches the self-scoped list. Left
          // real it would reach the network from a widget test.
          myAccessTokensProvider.overrideWith(_FakeMyTokens.new),
        ],
        child: MaterialApp.router(routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    router.push('/profile');
    await tester.pumpAndSettle();
  }

  Future<void> pressBack(WidgetTester tester) async {
    tester.state<NavigatorState>(find.byType(Navigator).last).maybePop();
    await tester.pumpAndSettle();
  }

  testWidgets('back after changing the name asks first', (tester) async {
    await openProfile(tester);

    await tester.tap(find.byTooltip('Edit profile'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'Ada King');
    await pressBack(tester);

    expect(find.text('Discard changes?'), findsOneWidget);
    expect(find.text('the more sheet'), findsNothing);
  });

  testWidgets('back while just reading leaves straight away', (tester) async {
    await openProfile(tester);

    await pressBack(tester);

    // The control. Most visits to this screen only look at it, and prompting
    // those would train people to dismiss the dialog without reading it.
    expect(find.text('Discard changes?'), findsNothing);
    expect(find.text('the more sheet'), findsOneWidget);
  });

  testWidgets('opening edit and changing nothing is not a change', (
    tester,
  ) async {
    await openProfile(tester);

    await tester.tap(find.byTooltip('Edit profile'));
    await tester.pumpAndSettle();
    await pressBack(tester);

    // The fields are pre-filled from the saved profile, so being in edit mode
    // is not by itself something to lose.
    expect(find.text('Discard changes?'), findsNothing);
    expect(find.text('the more sheet'), findsOneWidget);
  });

  group('the lead alert switches', () {
    setUp(() => _FakeProfile.saves.clear());

    testWidgets('are on by default and name what they do', (tester) async {
      await openProfile(tester);
      for (final label in [
        'Lead alerts by email',
        'Lead alerts on the phone',
      ]) {
        final row = find.widgetWithText(SwitchListTile, label);
        await tester.ensureVisible(row);
        await tester.pumpAndSettle();
        expect(tester.widget<SwitchListTile>(row).value, isTrue, reason: label);
      }
    });

    testWidgets('turning one off sends an explicit false for that one', (
      tester,
    ) async {
      await openProfile(tester);
      final row = find.widgetWithText(SwitchListTile, 'Lead alerts by email');
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      await tester.tap(row);
      await tester.pumpAndSettle();

      expect(_FakeProfile.saves, [(email: false, push: null)]);
      expect(tester.widget<SwitchListTile>(row).value, isFalse);
    });

    testWidgets('a refused change is put back and said', (tester) async {
      _FakeProfile.accept = false;
      addTearDown(() => _FakeProfile.accept = true);
      await openProfile(tester);
      final row = find.widgetWithText(
        SwitchListTile,
        'Lead alerts on the phone',
      );
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      await tester.tap(row);
      await tester.pumpAndSettle();

      expect(find.text('Could not change that setting'), findsOneWidget);
      expect(tester.widget<SwitchListTile>(row).value, isTrue);
    });
  });

  group('the fields the web profile page has had all along', () {
    testWidgets('names the teams you are on', (tester) async {
      await openProfile(tester);
      expect(find.text('Support, Onboarding'), findsOneWidget);
    });

    testWidgets('says when you last signed in', (tester) async {
      await openProfile(tester);
      expect(find.text('Last signed in'), findsOneWidget);
      expect(find.text('Not recorded'), findsNothing);
    });

    testWidgets('draws both absences rather than leaving them blank', (
      tester,
    ) async {
      await openProfile(tester, bare: true);
      expect(find.text('None'), findsOneWidget);
      expect(find.text('Not recorded'), findsOneWidget);
    });

    testWidgets('the token row leads to your own tokens, not the admin list', (
      tester,
    ) async {
      // The web row used to point at /settings/api-tokens, which 403s a
      // member. This one goes to the self-scoped screen.
      await openProfile(tester);
      final row = find.text('API tokens');
      // `ensureVisible`, not `scrollUntilVisible`: the row is already built,
      // just below the fold, and scrollUntilVisible drives the wrong
      // scrollable on a screen with more than one.
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      await tester.tap(row);
      await tester.pumpAndSettle();
      expect(find.text('your tokens'), findsOneWidget);
    });

    testWidgets('the calendar feed row leads to your own feed', (tester) async {
      await openProfile(tester);
      final row = find.text('Calendar feed');
      await tester.ensureVisible(row);
      await tester.pumpAndSettle();
      await tester.tap(row);
      await tester.pumpAndSettle();
      expect(find.text('your calendar feed'), findsOneWidget);
    });
  });
}

class _FakeProfile extends ProfileNotifier {
  static final saves = <({bool? email, bool? push})>[];
  static var accept = true;

  /// A fake, so a widget test stays off the network; the real PATCH is covered
  /// in `profile_lead_alerts_test.dart`.
  @override
  Future<bool> setLeadAlerts({bool? email, bool? push}) async {
    saves.add((email: email, push: push));
    if (!accept) return false;
    state = AsyncValue.data(
      state.value!.copyWith(leadAlertsEmail: email, leadAlertsPush: push),
    );
    return true;
  }

  @override
  Future<Profile> build() async => Profile(
    id: 'p1',
    user: const AuthUser(
      id: 'u1',
      email: 'user@example.com',
      name: 'Ada Lovelace',
    ),
    phone: '+1 555 0100',
    teams: const ['Support', 'Onboarding'],
    lastLogin: DateTime.utc(2026, 8, 6, 9, 30),
  );
}

/// Somebody on no teams who has never signed in, so both absences are drawn
/// rather than left blank.
class _FakeBareProfile extends ProfileNotifier {
  @override
  Future<Profile> build() async => const Profile(
    id: 'p1',
    user: AuthUser(id: 'u1', email: 'user@example.com', name: 'Ada Lovelace'),
  );
}

class _FakeMyTokens extends MyAccessTokensNotifier {
  @override
  Future<List<AccessToken>> build() async => const [];
}
