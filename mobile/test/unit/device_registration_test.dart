import 'package:bottle_crm/core/app_info.dart';
import 'package:bottle_crm/services/device_registration.dart';
import 'package:bottle_crm/services/push_messaging.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_push.dart';

void main() {
  late FakeDeviceApi api;
  late DeviceRegistration registration;

  setUp(() {
    api = FakeDeviceApi();
    registration = DeviceRegistration(api.client);
  });

  tearDown(() => PushMessaging.instance = const NoPushMessaging());

  test('registers the token with the platform and the app version', () async {
    PushMessaging.instance = FakePush(
      currentPermission: PushPermission.granted,
    );

    await registration.register();

    expect(api.posts, [
      {'token': 'tok-1', 'platform': 'android', 'app_version': appVersion},
    ]);
  });

  test('sends nothing until notifications are allowed', () async {
    PushMessaging.instance = FakePush();

    await registration.register();

    expect(api.posts, isEmpty);
  });

  test('sends nothing when push is not available in this build', () async {
    await registration.register();
    await registration.unregister(forget: true);

    expect(api.posts, isEmpty);
    expect(api.deletes, isEmpty);
  });

  test('a refreshed token is registered when signed in with an org', () async {
    final push = FakePush(currentPermission: PushPermission.granted);
    PushMessaging.instance = push;
    var signedIn = false;
    registration.watchRefresh(() => signedIn);

    push.refreshes.add('tok-2');
    await Future<void>.delayed(Duration.zero);
    expect(api.posts, isEmpty);

    signedIn = true;
    push.refreshes.add('tok-3');
    await Future<void>.delayed(Duration.zero);
    expect(api.posts.single['token'], 'tok-3');
  });

  test(
    'unregister deletes the token the server holds, not a newer one',
    () async {
      final push = FakePush(currentPermission: PushPermission.granted);
      PushMessaging.instance = push;
      await registration.register();
      push.currentToken = 'rotated';

      await registration.unregister();

      expect(api.deletes, [
        {'token': 'tok-1'},
      ]);
      expect(push.tokenDeleted, isFalse);
    },
  );

  test('sign-out also drops the FCM token', () async {
    final push = FakePush(currentPermission: PushPermission.granted);
    PushMessaging.instance = push;

    await registration.unregister(forget: true);

    expect(api.deletes.single['token'], 'tok-1');
    expect(push.tokenDeleted, isTrue);
  });

  group('signing out', () {
    test('a token refresh while signing out registers nothing', () async {
      final push = FakePush(currentPermission: PushPermission.granted);
      PushMessaging.instance = push;
      registration.watchRefresh(() => true);

      await registration.unregister(forget: true);
      push.refreshes.add('tok-after-delete');
      await Future<void>.delayed(Duration.zero);

      expect(api.posts, isEmpty);
      expect(api.deletes.single['token'], 'tok-1');
    });

    test('nothing registers until the sign-out has finished', () async {
      final push = FakePush(currentPermission: PushPermission.granted);
      PushMessaging.instance = push;

      await registration.unregister(forget: true);
      await registration.register();
      expect(api.posts, isEmpty);

      registration.finishSignOut();
      await registration.register();
      expect(api.posts.single['token'], 'tok-1');
    });

    test(
      'a hung delete does not hold sign-out, the local token goes',
      () async {
        api = FakeDeviceApi(hang: true);
        registration = DeviceRegistration(
          api.client,
          deleteTimeout: const Duration(milliseconds: 50),
        );
        final push = FakePush(currentPermission: PushPermission.granted);
        PushMessaging.instance = push;

        await registration.unregister(forget: true);

        expect(push.tokenDeleted, isTrue);
      },
    );

    test('a refused delete still drops the local token', () async {
      api = FakeDeviceApi(status: 500);
      registration = DeviceRegistration(api.client);
      final push = FakePush(currentPermission: PushPermission.granted);
      PushMessaging.instance = push;

      await registration.unregister(forget: true);

      expect(push.tokenDeleted, isTrue);
    });
  });
}
