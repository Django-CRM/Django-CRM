import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api/schema.g.dart' as api;
import '../data/models/app_notification.dart';
import '../services/api_service.dart';

export '../services/api_service.dart' show ApiResponse;

/// The signed-in user's feed, plus the badge count.
///
/// `unreadCount` is the server's count over the whole feed, not over the rows
/// fetched. The two differ as soon as there are more unread items than the
/// page limit, and the badge has to be the real number or it under-reports
/// exactly when it matters most.
class NotificationFeed {
  const NotificationFeed({
    this.items = const [],
    this.unreadCount = 0,
    this.total = 0,
  });

  final List<AppNotification> items;
  final int unreadCount;
  final int total;

  List<AppNotification> get unread => items.where((n) => n.isUnread).toList();

  /// Rows still carrying the dead `/cases/` prefix, written before the
  /// producer was fixed. Surfaced once as a footnote rather than per row: it
  /// is true of every affected row, so a badge on each says nothing.
  int get legacyLinkCount =>
      items.where((n) => n.link?.startsWith('/cases/') ?? false).length;

  NotificationFeed copyWith({
    List<AppNotification>? items,
    int? unreadCount,
    int? total,
  }) => NotificationFeed(
    items: items ?? this.items,
    unreadCount: unreadCount ?? this.unreadCount,
    total: total ?? this.total,
  );
}

const int _feedLimit = 50;

class NotificationsNotifier extends AsyncNotifier<NotificationFeed> {
  final ApiService _apiService = ApiService();

  @override
  Future<NotificationFeed> build() => _fetch();

  Future<NotificationFeed> _fetch() async {
    final response = await _apiService.get(
      ApiConfig.notifications(limit: _feedLimit),
    );
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load notifications.');
    }
    final data = api.NotificationListResponse.fromJson(response.data!);
    final items = [
      for (final row in data.results ?? const <api.Notification>[])
        AppNotification.fromJson(row.json),
    ];
    return NotificationFeed(
      items: items,
      unreadCount: data.unreadCount ?? items.where((n) => n.isUnread).length,
      total: data.count ?? items.length,
    );
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Fetch again without clearing what is on screen: the badge reads zero
  /// while the feed loads, so a push or a resume must not flash it. A failed
  /// fetch keeps the old feed.
  Future<void> quietRefresh() async {
    final next = await AsyncValue.guard(_fetch);
    if (next.hasValue) state = next;
  }

  /// Mark one notification read, optimistically.
  ///
  /// The dot clears at once and is put back if the server refuses, so the
  /// screen never settles on a state the server did not accept. Optimism is
  /// what makes tapping a notification usable: the tap also opens the ticket,
  /// and waiting for a round-trip before navigating would stall that.
  Future<ApiResponse<Map<String, dynamic>>> markRead(String id) async {
    final current = state.value;
    if (current == null) {
      return const ApiResponse(
        success: false,
        message: 'Nothing loaded yet.',
        statusCode: 0,
      );
    }
    final index = current.items.indexWhere((n) => n.id == id);
    if (index < 0 || !current.items[index].isUnread) {
      // Already read, or not in this feed. The endpoint is idempotent, so
      // re-posting would be harmless, but it would also be pointless.
      return const ApiResponse(success: true, statusCode: 200);
    }

    state = AsyncValue.data(_withRead({id}, current));

    try {
      final response = await _apiService.post(
        ApiConfig.notificationRead(id),
        const {},
      );
      if (!response.success) state = AsyncValue.data(current);
      return response;
    } catch (e) {
      state = AsyncValue.data(current);
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Mark every unread notification read, optimistically, with the same
  /// put-it-back-on-failure rule.
  Future<ApiResponse<Map<String, dynamic>>> markAllRead() async {
    final current = state.value;
    if (current == null || current.unread.isEmpty) {
      return const ApiResponse(success: true, statusCode: 200);
    }

    state = AsyncValue.data(
      _withRead(current.unread.map((n) => n.id).toSet(), current),
    );

    try {
      final response = await _apiService.post(
        ApiConfig.notificationsReadAll,
        const {},
      );
      if (!response.success) state = AsyncValue.data(current);
      return response;
    } catch (e) {
      state = AsyncValue.data(current);
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// The feed with [ids] marked read and the badge reduced by however many of
  /// them were actually unread. Counting the ids alone would let a repeated
  /// mark drive the badge below zero.
  NotificationFeed _withRead(Set<String> ids, NotificationFeed feed) {
    final now = DateTime.now();
    var cleared = 0;
    final items = feed.items.map((n) {
      if (!ids.contains(n.id) || !n.isUnread) return n;
      cleared++;
      return n.copyWith(readAt: now);
    }).toList();
    return feed.copyWith(
      items: items,
      unreadCount: (feed.unreadCount - cleared).clamp(0, feed.unreadCount),
    );
  }
}

final notificationsProvider =
    AsyncNotifierProvider<NotificationsNotifier, NotificationFeed>(
      NotificationsNotifier.new,
    );

/// The bell badge. Zero while the feed is loading or has failed, because a
/// badge is a claim about how many there are and "unknown" is not a number.
final unreadNotificationCountProvider = Provider<int>((ref) {
  return ref.watch(notificationsProvider).value?.unreadCount ?? 0;
});
