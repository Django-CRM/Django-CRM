import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

/// A push as the app sees it: what the banner says and where a tap goes.
class PushMessage {
  const PushMessage({this.title, this.body, this.link});

  final String? title;
  final String? body;

  /// The record path the server sends (`/leads/<id>`). Never navigated to
  /// as-is: `AppNotification.destinationFor` parses it.
  final String? link;
}

/// Whether the user has let the app show notifications.
enum PushPermission { notDetermined, granted, denied }

/// The one seam to Firebase Cloud Messaging, so nothing else in the app (and
/// no test) touches the plugin. Android only: [instance] is [NoPushMessaging]
/// until `main` installs [FirebasePushMessaging] after Firebase starts.
abstract class PushMessaging {
  static PushMessaging instance = const NoPushMessaging();

  bool get supported;
  Future<PushPermission> permission();
  Future<PushPermission> requestPermission();

  /// This device's FCM token, or null when there is none. Callers must never
  /// log it.
  Future<String?> token();
  Future<void> deleteToken();
  Stream<String> get onTokenRefresh;
  Stream<PushMessage> get onForeground;
  Stream<PushMessage> get onOpened;

  /// The push that cold-started the app, if one did.
  Future<PushMessage?> initialMessage();
}

class NoPushMessaging implements PushMessaging {
  const NoPushMessaging();

  @override
  bool get supported => false;
  @override
  Future<PushPermission> permission() async => PushPermission.denied;
  @override
  Future<PushPermission> requestPermission() async => PushPermission.denied;
  @override
  Future<String?> token() async => null;
  @override
  Future<void> deleteToken() async {}
  @override
  Stream<String> get onTokenRefresh => const Stream.empty();
  @override
  Stream<PushMessage> get onForeground => const Stream.empty();
  @override
  Stream<PushMessage> get onOpened => const Stream.empty();
  @override
  Future<PushMessage?> initialMessage() async => null;
}

class FirebasePushMessaging implements PushMessaging {
  FirebasePushMessaging() : _fcm = FirebaseMessaging.instance;

  final FirebaseMessaging _fcm;

  @override
  bool get supported => true;

  PushPermission _of(AuthorizationStatus status) => switch (status) {
    AuthorizationStatus.authorized ||
    AuthorizationStatus.provisional => PushPermission.granted,
    AuthorizationStatus.denied ||
    AuthorizationStatus.deniedPermanently => PushPermission.denied,
    AuthorizationStatus.notDetermined => PushPermission.notDetermined,
  };

  @override
  Future<PushPermission> permission() async =>
      _of((await _fcm.getNotificationSettings()).authorizationStatus);

  @override
  Future<PushPermission> requestPermission() async =>
      _of((await _fcm.requestPermission()).authorizationStatus);

  @override
  Future<String?> token() async {
    try {
      return await _fcm.getToken();
    } catch (e) {
      debugPrint('Push: no FCM token (${e.runtimeType})');
      return null;
    }
  }

  @override
  Future<void> deleteToken() async {
    try {
      await _fcm.deleteToken();
    } catch (e) {
      debugPrint('Push: token delete failed (${e.runtimeType})');
    }
  }

  @override
  Stream<String> get onTokenRefresh => _fcm.onTokenRefresh;

  PushMessage _message(RemoteMessage m) => PushMessage(
    title: m.notification?.title,
    body: m.notification?.body,
    link: m.data['link'] is String ? m.data['link'] as String : null,
  );

  @override
  Stream<PushMessage> get onForeground =>
      FirebaseMessaging.onMessage.map(_message);

  @override
  Stream<PushMessage> get onOpened =>
      FirebaseMessaging.onMessageOpenedApp.map(_message);

  @override
  Future<PushMessage?> initialMessage() async {
    final m = await _fcm.getInitialMessage();
    return m == null ? null : _message(m);
  }
}
