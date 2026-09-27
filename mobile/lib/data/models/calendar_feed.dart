/// The state of your task calendar feed, from `GET /api/profile/calendar-feed/`.
///
/// Never the URL: the API keeps only a hash of it, so the URL exists on the
/// phone only in the response to the request that created it.
library;

class CalendarFeed {
  const CalendarFeed({this.enabled = false, this.createdAt, this.lastUsedAt});

  final bool enabled;

  /// When the current URL was issued. Regenerating resets it.
  final DateTime? createdAt;

  /// When a calendar app last read the feed. Moved at most once an hour.
  final DateTime? lastUsedAt;

  factory CalendarFeed.fromJson(Map<String, dynamic> json) {
    DateTime? when(Object? value) =>
        value is String ? DateTime.tryParse(value)?.toLocal() : null;
    return CalendarFeed(
      enabled: json['enabled'] == true,
      createdAt: when(json['created_at']),
      lastUsedAt: when(json['last_used_at']),
    );
  }
}
