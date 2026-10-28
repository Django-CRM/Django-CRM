import 'dart:async';

import 'package:flutter/foundation.dart';

import '../config/api_config.dart';
import '../core/app_info.dart';
import 'api_service.dart';
import 'push_messaging.dart';

/// Tells the server which phone to push lead alerts to.
///
/// The FCM token is a credential for pushing to this device, so it is sent
/// only to the API and never logged. Every method swallows failure: a missed
/// registration costs one alert channel, and must never block a sign-in, an
/// org switch or a sign-out.
class DeviceRegistration {
  DeviceRegistration(
    this._api, {
    this.deleteTimeout = const Duration(seconds: 5),
  });

  /// The shared instance; a test replaces it with one over a fake client.
  static DeviceRegistration instance = DeviceRegistration(ApiService());

  final ApiService _api;

  /// How long sign-out waits for the server to drop the device. The local
  /// FCM token goes either way.
  final Duration deleteTimeout;
  StreamSubscription<String>? _refreshes;

  /// The token last sent for the current org, so a delete names the token the
  /// server holds even if FCM has rotated it since.
  String? _registered;

  /// True from the start of a sign-out until it has finished. Deleting the FCM
  /// token makes Android issue a new one at once, and registering that during
  /// the sign-out would leave a device row for a user who is leaving.
  bool _signingOut = false;

  /// Re-registers when FCM rotates the token. [signedIn] says whether the
  /// session has an org, since the API needs one.
  void watchRefresh(bool Function() signedIn) {
    _refreshes?.cancel();
    _refreshes = PushMessaging.instance.onTokenRefresh.listen((token) {
      if (!_signingOut && signedIn()) unawaited(register(token: token));
    });
  }

  /// Registers this device for the current org, when the user has allowed
  /// notifications.
  Future<void> register({String? token}) async {
    final push = PushMessaging.instance;
    if (!push.supported || _signingOut) return;
    try {
      if (await push.permission() != PushPermission.granted) return;
      final value = token ?? await push.token();
      // Sign-out may have started while permission and token were awaited.
      if (value == null || value.isEmpty || _signingOut) return;
      final response = await _api.post(ApiConfig.devices, {
        'token': value,
        'platform': 'android',
        'app_version': appVersion,
      });
      if (response.success) _registered = value;
    } catch (e) {
      debugPrint('Push: registration failed (${e.runtimeType})');
    }
  }

  /// Removes this device from the current org. [forget] also drops the FCM
  /// token, for sign-out: the next sign-in gets a fresh one. Call
  /// [finishSignOut] once local sign-out is done.
  Future<void> unregister({bool forget = false}) async {
    final push = PushMessaging.instance;
    if (!push.supported) return;
    if (forget) _signingOut = true;
    try {
      final value = _registered ?? await push.token();
      if (value != null && value.isNotEmpty) {
        await _api
            .delete(ApiConfig.devices, body: {'token': value})
            .timeout(deleteTimeout);
      }
    } catch (e) {
      debugPrint('Push: unregister failed (${e.runtimeType})');
    }
    _registered = null;
    if (forget) await push.deleteToken();
  }

  /// Ends the sign-out guard, so the next sign-in registers again.
  void finishSignOut() => _signingOut = false;
}
