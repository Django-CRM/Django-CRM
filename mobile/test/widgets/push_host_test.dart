import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/notifications_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/services/device_registration.dart';
import 'package:bottle_crm/services/push_messaging.dart';
import 'package:bottle_crm/widgets/common/push_host.dart';
import 'package:bottle_crm/widgets/common/push_prompt.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../support/fake_push.dart';

const _id = '3f2b8c1e-5a4d-4e8b-9c3a-1d2e3f4a5b6c';

class _SignedIn extends AuthNotifier {
  @override
  AuthState build() => const AuthState(
    isAuthenticated: true,
    selectedOrganization: Organization(id: 'o1', name: 'Acme'),
  );
}

class _Switchable extends AuthNotifier {
  static AuthState start = const AuthState(
    isAuthenticated: true,
    selectedOrganization: Organization(id: 'o1', name: 'Acme'),
  );

  @override
  AuthState build() => start;

  void become(AuthState next) => state = next;
}

class _Feed extends NotificationsNotifier {
  static var quiet = 0;

  @override
  Future<NotificationFeed> build() async => const NotificationFeed();

  @override
  Future<void> quietRefresh() async => quiet++;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late FakePush push;
  late DeviceRegistration saved;
  late FakeDeviceApi api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    _Feed.quiet = 0;
    _Switchable.start = const AuthState(
      isAuthenticated: true,
      selectedOrganization: Organization(id: 'o1', name: 'Acme'),
    );
    api = FakeDeviceApi();
    saved = DeviceRegistration.instance;
    DeviceRegistration.instance = DeviceRegistration(api.client);
  });

  tearDown(() {
    PushMessaging.instance = const NoPushMessaging();
    DeviceRegistration.instance = saved;
  });

  Future<GoRouter> pumpHost(WidgetTester tester) async {
    final router = GoRouter(
      routes: [
        GoRoute(
          path: '/',
          builder: (_, _) => const Scaffold(body: Text('home')),
        ),
        GoRoute(
          path: '/leads/:id',
          builder: (_, s) =>
              Scaffold(body: Text('lead ${s.pathParameters['id']}')),
        ),
        GoRoute(
          path: '/contacts/:id',
          builder: (_, s) =>
              Scaffold(body: Text('contact ${s.pathParameters['id']}')),
        ),
        GoRoute(
          path: '/accounts/:id',
          builder: (_, s) =>
              Scaffold(body: Text('account ${s.pathParameters['id']}')),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(_SignedIn.new),
          notificationsProvider.overrideWith(_Feed.new),
          appRouterProvider.overrideWithValue(router),
        ],
        child: MaterialApp.router(
          routerConfig: router,
          builder: (context, child) => PushHost(child: child!),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return router;
  }

  testWidgets('a push while open shows a banner and refreshes the count', (
    tester,
  ) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.foreground.add(
      const PushMessage(
        title: 'BottleCRM',
        body: 'New lead assigned: Maria Lopez (Google ads)',
        link: '/leads/$_id',
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('New lead assigned: Maria Lopez (Google ads)'), findsOne);
    expect(_Feed.quiet, 1);

    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    expect(find.text('lead $_id'), findsOneWidget);
  });

  testWidgets('tapping a push from the background opens the lead', (
    tester,
  ) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.opened.add(const PushMessage(link: '/leads/$_id'));
    await tester.pumpAndSettle();

    expect(find.text('lead $_id'), findsOneWidget);
  });

  testWidgets('a repeat enquiry push opens the contact', (tester) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.opened.add(const PushMessage(link: '/contacts/$_id'));
    await tester.pumpAndSettle();

    expect(find.text('contact $_id'), findsOneWidget);
  });

  testWidgets('a push linking to any other record opens nothing', (
    tester,
  ) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.opened.add(const PushMessage(link: '/accounts/$_id'));
    await tester.pumpAndSettle();

    expect(find.text('home'), findsOneWidget);
  });

  testWidgets('the push that started the app opens the lead', (tester) async {
    push = FakePush(cold: const PushMessage(link: '/leads/$_id'));
    PushMessaging.instance = push;
    await pumpHost(tester);

    expect(find.text('lead $_id'), findsOneWidget);
  });

  testWidgets('a link that is not a known record opens nothing', (
    tester,
  ) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.opened.add(const PushMessage(link: 'https://evil.example/x'));
    await tester.pumpAndSettle();

    expect(find.text('home'), findsOneWidget);
  });

  testWidgets('resuming the app refreshes the notification count', (
    tester,
  ) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    expect(_Feed.quiet, 1);
  });

  group('a tapped push that has to wait', () {
    const acme = AuthState(
      isAuthenticated: true,
      selectedOrganization: Organization(id: 'o1', name: 'Acme'),
    );
    const globex = AuthState(
      isAuthenticated: true,
      selectedOrganization: Organization(id: 'o2', name: 'Globex'),
    );

    // The router sits on the splash, so a tap is held until it leaves.
    Future<GoRouter> pumpOnSplash(WidgetTester tester) async {
      final router = GoRouter(
        initialLocation: AppRoutes.splash,
        routes: [
          GoRoute(
            path: AppRoutes.splash,
            builder: (_, _) => const Scaffold(body: Text('splash')),
          ),
          GoRoute(
            path: '/home',
            builder: (_, _) => const Scaffold(body: Text('home')),
          ),
          GoRoute(
            path: '/leads/:id',
            builder: (_, s) =>
                Scaffold(body: Text('lead ${s.pathParameters['id']}')),
          ),
        ],
      );
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            authProvider.overrideWith(_Switchable.new),
            notificationsProvider.overrideWith(_Feed.new),
            appRouterProvider.overrideWithValue(router),
          ],
          child: MaterialApp.router(
            routerConfig: router,
            builder: (context, child) => PushHost(child: child!),
          ),
        ),
      );
      await tester.pumpAndSettle();
      return router;
    }

    _Switchable auth(WidgetTester tester) =>
        ProviderScope.containerOf(
              tester.element(find.byType(PushHost)),
            ).read(authProvider.notifier)
            as _Switchable;

    testWidgets('is dropped when the user signs out', (tester) async {
      push = FakePush();
      PushMessaging.instance = push;
      final router = await pumpOnSplash(tester);

      push.opened.add(const PushMessage(link: '/leads/$_id'));
      await tester.pumpAndSettle();
      auth(tester).become(const AuthState.initial());
      await tester.pumpAndSettle();
      auth(tester).become(acme);
      router.go('/home');
      await tester.pumpAndSettle();

      expect(find.text('lead $_id'), findsNothing);
      expect(find.text('home'), findsOneWidget);
    });

    testWidgets('is dropped when the org changes', (tester) async {
      push = FakePush();
      PushMessaging.instance = push;
      final router = await pumpOnSplash(tester);

      push.opened.add(const PushMessage(link: '/leads/$_id'));
      await tester.pumpAndSettle();
      auth(tester).become(globex);
      await tester.pumpAndSettle();
      router.go('/home');
      await tester.pumpAndSettle();

      expect(find.text('lead $_id'), findsNothing);
    });

    testWidgets('still opens when nothing changed', (tester) async {
      push = FakePush();
      PushMessaging.instance = push;
      final router = await pumpOnSplash(tester);

      push.opened.add(const PushMessage(link: '/leads/$_id'));
      await tester.pumpAndSettle();
      router.go('/home');
      await tester.pumpAndSettle();

      expect(find.text('lead $_id'), findsOneWidget);
    });

    testWidgets('one that started the app waits for the org, then opens', (
      tester,
    ) async {
      push = FakePush(cold: const PushMessage(link: '/leads/$_id'));
      PushMessaging.instance = push;
      _Switchable.start = const AuthState.initial();
      final router = await pumpOnSplash(tester);
      auth(tester).become(acme);
      router.go('/home');
      await tester.pumpAndSettle();

      expect(find.text('lead $_id'), findsOneWidget);
    });
  });

  testWidgets('only a lead link is followed', (tester) async {
    push = FakePush();
    PushMessaging.instance = push;
    await pumpHost(tester);

    push.opened.add(const PushMessage(link: '/settings/automation'));
    await tester.pumpAndSettle();

    expect(find.text('home'), findsOneWidget);
  });

  testWidgets('resuming registers the device once notifications are on', (
    tester,
  ) async {
    push = FakePush(currentPermission: PushPermission.granted);
    PushMessaging.instance = push;
    await pumpHost(tester);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    expect(api.posts.single['token'], 'tok-1');
  });

  group('the permission prompt', () {
    Future<void> pumpPrompt(WidgetTester tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => offerPushPrompt(context),
                  child: const Text('go'),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
    }

    testWidgets('says why, then asks, then registers', (tester) async {
      push = FakePush();
      PushMessaging.instance = push;
      await pumpPrompt(tester);

      expect(
        find.textContaining('a lead assigned to you reaches you at once'),
        findsOneWidget,
      );
      expect(push.asked, 0);

      await tester.tap(find.text('Allow notifications'));
      await tester.pumpAndSettle();

      expect(push.asked, 1);
      expect(api.posts.single['token'], 'tok-1');
    });

    testWidgets('Not now asks nothing and is not asked again', (tester) async {
      push = FakePush();
      PushMessaging.instance = push;
      await pumpPrompt(tester);

      await tester.tap(find.text('Not now'));
      await tester.pumpAndSettle();
      expect(push.asked, 0);
      expect(api.posts, isEmpty);

      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
      expect(find.text('Get lead alerts'), findsNothing);
    });

    testWidgets('is not shown once the choice is made', (tester) async {
      push = FakePush(currentPermission: PushPermission.denied);
      PushMessaging.instance = push;
      await pumpPrompt(tester);

      expect(find.text('Get lead alerts'), findsNothing);
    });

    testWidgets('is not shown when push is unavailable', (tester) async {
      await pumpPrompt(tester);

      expect(find.text('Get lead alerts'), findsNothing);
    });
  });
}
