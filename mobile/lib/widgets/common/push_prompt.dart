import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../services/device_registration.dart';
import '../../services/push_messaging.dart';

const _askedKey = 'push_prompt_asked';

/// Asks once, after the first sign-in, whether to allow notifications, with a
/// line on why before the system's own prompt. Nothing is asked when push is
/// not available in this build, the choice was already made, or the user has
/// seen this once (Not now is final until they turn the switch on in Profile).
Future<void> offerPushPrompt(BuildContext context) async {
  final push = PushMessaging.instance;
  if (!push.supported) return;
  if (await push.permission() != PushPermission.notDetermined) return;
  final prefs = await SharedPreferences.getInstance();
  if (prefs.getBool(_askedKey) ?? false) return;
  if (!context.mounted) return;

  final allow = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Get lead alerts'),
      content: const Text(
        'Allow notifications so a lead assigned to you reaches you at once.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Not now'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('Allow notifications'),
        ),
      ],
    ),
  );
  await prefs.setBool(_askedKey, true);
  if (allow == true) await requestPushPermission();
}

/// Shows the system prompt when the choice is still open, then registers this
/// phone if it was allowed. Also used when the Profile switch is turned on.
Future<void> requestPushPermission() async {
  final push = PushMessaging.instance;
  if (!push.supported) return;
  var permission = await push.permission();
  if (permission == PushPermission.notDetermined) {
    permission = await push.requestPermission();
  }
  if (permission == PushPermission.granted) {
    await DeviceRegistration.instance.register();
  }
}
