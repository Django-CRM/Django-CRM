import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/calendar_feed.dart';
import '../services/api_service.dart';

/// Your own task calendar feed.
///
/// `/api/profile/calendar-feed/` is self-scoped server-side and refuses every
/// API token, so this can only manage the signed-in member's own feed.
class CalendarFeedNotifier extends AsyncNotifier<CalendarFeed> {
  final ApiService _api = ApiService();

  @override
  Future<CalendarFeed> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<CalendarFeed> _fetch() async {
    final response = await _api.get(ApiConfig.calendarFeed);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load your calendar feed');
    }
    return CalendarFeed.fromJson(response.data!);
  }

  /// Turn the feed on, or replace its URL. Returns the new URL, the one time
  /// it exists anywhere outside the server's hash, or the error to show. A
  /// refusal shows the server's own message (a 429 says when to try again),
  /// as the web does; the fixed sentence covers a success with no URL.
  Future<({String? error, String? url})> issue() async {
    final response = await _api.post(ApiConfig.calendarFeed, const {});
    final url = response.data?['url'];
    if (!response.success || url is! String || url.isEmpty) {
      return (
        error: response.message ?? 'Could not create the calendar feed.',
        url: null,
      );
    }
    state = AsyncValue.data(CalendarFeed.fromJson(response.data!));
    return (error: null, url: url);
  }

  /// Turn the feed off. Returns null on success, or the error to show: the
  /// server's own message, as for [issue].
  Future<String?> disable() async {
    final response = await _api.delete(ApiConfig.calendarFeed);
    if (!response.success) {
      return response.message ?? 'Could not turn the calendar feed off.';
    }
    state = const AsyncValue.data(CalendarFeed());
    return null;
  }
}

final calendarFeedProvider =
    AsyncNotifierProvider<CalendarFeedNotifier, CalendarFeed>(
      CalendarFeedNotifier.new,
    );
