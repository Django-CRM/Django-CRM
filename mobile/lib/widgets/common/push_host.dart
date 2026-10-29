import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/app_notification.dart';
import '../../providers/auth_provider.dart';
import '../../providers/notifications_provider.dart';
import '../../routes/app_router.dart';
import '../../services/device_registration.dart';
import '../../services/push_messaging.dart';

/// Wraps the app to react to pushes: a banner while the app is open, a
/// notification refresh on a message and on resume, and a tap (from the
/// background or a cold start) opening its record.
///
/// A push link is a server value, so it goes through
/// [AppNotification.destinationFor] like a notification row's: it can only
/// reach a known record screen.
class PushHost extends ConsumerStatefulWidget {
  const PushHost({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<PushHost> createState() => _PushHostState();
}

class _PushHostState extends ConsumerState<PushHost>
    with WidgetsBindingObserver {
  final _subscriptions = <StreamSubscription<PushMessage>>[];
  Timer? _bannerTimer;
  late final GoRouter _router = ref.read(appRouterProvider);
  String? _pending;

  /// The org that was current when the push arrived; null while the session
  /// was not ready yet (a cold start), in which case the first org it becomes
  /// ready for is the one.
  String? _pendingOrg;

  bool get _ready => _currentOrg != null;

  String? get _currentOrg {
    final auth = ref.read(authProvider);
    return auth.isAuthenticated ? auth.selectedOrganization?.id : null;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final push = PushMessaging.instance;
    _subscriptions
      ..add(push.onForeground.listen(_onForeground))
      ..add(push.onOpened.listen(_open));
    unawaited(
      push.initialMessage().then((m) {
        if (m != null && mounted) _open(m);
      }),
    );
    ref.listenManual(authProvider, (_, _) {
      _checkPendingOrg();
      _flush();
    });
    _router.routerDelegate.addListener(_flush);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _router.routerDelegate.removeListener(_flush);
    for (final s in _subscriptions) {
      s.cancel();
    }
    _bannerTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed || !_ready) return;
    // A permission granted in system settings is only noticed here; the
    // server side is an upsert.
    unawaited(DeviceRegistration.instance.register());
    _refreshNotifications();
  }

  void _refreshNotifications() =>
      unawaited(ref.read(notificationsProvider.notifier).quietRefresh());

  void _onForeground(PushMessage message) {
    if (!_ready) return;
    _refreshNotifications();
    final text = message.body ?? message.title;
    if (text == null || text.isEmpty) return;
    final destination = _enquiryPath(message.link);
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;

    void dismiss() {
      _bannerTimer?.cancel();
      messenger.hideCurrentMaterialBanner();
    }

    messenger.clearMaterialBanners();
    messenger.showMaterialBanner(
      MaterialBanner(
        backgroundColor: AppColors.surface,
        leading: Icon(LucideIcons.bellRing, color: AppColors.primary600),
        content: Text(text, style: AppTypography.body),
        actions: [
          TextButton(onPressed: dismiss, child: const Text('Dismiss')),
          if (destination != null)
            TextButton(
              onPressed: () {
                dismiss();
                _go(destination);
              },
              child: const Text('Open'),
            ),
        ],
      ),
    );
    _bannerTimer?.cancel();
    _bannerTimer = Timer(const Duration(seconds: 8), dismiss);
  }

  void _open(PushMessage message) {
    final destination = _enquiryPath(message.link);
    if (destination == null) return;
    _go(destination);
  }

  /// A lead alert's push opens the lead, or the contact once the lead is
  /// converted; nothing else.
  static String? _enquiryPath(String? link) {
    final path = AppNotification.destinationFor(link);
    if (path == null ||
        !RegExp(r'^/(?:leads|contacts)/[^/?#]+$').hasMatch(path)) {
      return null;
    }
    return path;
  }

  /// Drops a waiting push once the session it arrived in has ended or moved
  /// to another org.
  void _checkPendingOrg() {
    if (_pending == null) return;
    final org = _currentOrg;
    final bound = _pendingOrg;
    if (bound == null) {
      if (org != null) _pendingOrg = org;
    } else if (org != bound) {
      _pending = null;
      _pendingOrg = null;
    }
  }

  /// Pushes [path] once the session is signed in with an org and the router
  /// has left the splash, which would otherwise replace it.
  void _go(String path) {
    _pending = path;
    _pendingOrg = _currentOrg;
    _flush();
  }

  void _flush() {
    final path = _pending;
    if (path == null || !mounted || !_ready) return;
    _checkPendingOrg();
    if (_pending == null) return;
    final here = _router.routerDelegate.currentConfiguration.uri.path;
    if (here == AppRoutes.splash) return;
    _pending = null;
    _pendingOrg = null;
    // Not inline: this also runs from the router's own change notification.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_router.push(path));
    });
    WidgetsBinding.instance.scheduleFrame();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
