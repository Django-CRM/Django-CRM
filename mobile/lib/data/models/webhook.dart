/// Outbound webhooks, as `/api/webhooks/` returns them.
///
/// The signing secret is never part of these models. The API sends it only in
/// the response to a create or a rotate, and the provider hands it straight to
/// the one dialog that shows it.
library;

import '../api/schema.g.dart' as api;

/// One module's events, as the checkbox group on the form draws them.
class WebhookEventGroup {
  const WebhookEventGroup({
    required this.module,
    required this.label,
    required this.events,
  });

  final String module;
  final String label;
  final List<String> events;

  /// Reads a `WebhookEventGroup`, one entry of the list's `event_catalogue`.
  factory WebhookEventGroup.fromJson(Map<String, dynamic> json) {
    final w = api.WebhookEventGroup.fromJson(json);
    return WebhookEventGroup(
      module: w.module ?? '',
      label: w.label ?? '',
      events: w.events ?? const [],
    );
  }
}

class WebhookEndpoint {
  const WebhookEndpoint({
    required this.id,
    required this.url,
    this.description = '',
    this.events = const [],
    this.format = formatJson,
    this.isActive = true,
    this.disabledReason = '',
    this.secretHint = '',
    this.createdByName = '',
    this.createdByEmail = '',
    this.createdAt,
  });

  static const String formatJson = 'json';
  static const String formatSlack = 'slack';
  static const String formatTeams = 'teams';

  final String id;
  final String url;
  final String description;
  final List<String> events;
  final String format;
  final bool isActive;

  /// Why the server paused it: it answered 410 Gone, or the admin who
  /// created it stopped being an admin, was deactivated, or left the org.
  /// Empty otherwise. Any admin can turn it back on.
  final String disabledReason;

  /// Who answers for it: whoever added it, or the admin who last turned it
  /// back on. Both empty once that user has been deleted.
  final String createdByName;
  final String createdByEmail;

  /// The creator as one line: name and email, whichever are known.
  String get creatorLabel {
    if (createdByName.isNotEmpty && createdByEmail.isNotEmpty) {
      return '$createdByName ($createdByEmail)';
    }
    if (createdByName.isNotEmpty) return createdByName;
    if (createdByEmail.isNotEmpty) return createdByEmail;
    return 'a removed user';
  }

  /// `whsec_...abcd`. The full secret is never on a list or detail response.
  final String secretHint;
  final DateTime? createdAt;

  String get title => description.isNotEmpty ? description : url;

  String get formatLabel => switch (format) {
    formatSlack => 'Slack message',
    formatTeams => 'Teams message',
    _ => 'Signed JSON',
  };

  /// Reads a `WebhookEndpoint`.
  factory WebhookEndpoint.fromJson(Map<String, dynamic> json) {
    final w = api.WebhookEndpoint.fromJson(json);
    return WebhookEndpoint(
      id: w.id ?? '',
      url: w.url ?? '',
      description: w.description ?? '',
      events: w.events ?? const [],
      format: w.format ?? formatJson,
      isActive: w.isActive ?? false,
      disabledReason: w.disabledReason ?? '',
      secretHint: w.secretHint ?? '',
      createdByName: w.createdBy?.name ?? '',
      createdByEmail: w.createdBy?.email ?? '',
      createdAt: DateTime.tryParse(w.createdAt ?? ''),
    );
  }
}

class WebhookDelivery {
  const WebhookDelivery({
    required this.id,
    required this.event,
    required this.status,
    this.attempts = 0,
    this.responseStatus,
    this.error = '',
    this.createdAt,
  });

  static const String pending = 'pending';
  static const String succeeded = 'succeeded';
  static const String failed = 'failed';

  final String id;
  final String event;
  final String status;
  final int attempts;
  final int? responseStatus;
  final String error;
  final DateTime? createdAt;

  /// Only a settled delivery can be sent again; the API refuses a pending one.
  bool get canRedeliver => status != pending;

  String get statusLabel => switch (status) {
    succeeded => 'Delivered',
    failed => 'Failed',
    _ => 'Pending',
  };

  /// Reads a `WebhookDelivery`.
  factory WebhookDelivery.fromJson(Map<String, dynamic> json) {
    final w = api.WebhookDelivery.fromJson(json);
    return WebhookDelivery(
      id: w.id ?? '',
      event: w.event ?? '',
      status: w.status ?? pending,
      attempts: w.attempts ?? 0,
      responseStatus: w.responseStatus,
      error: w.error ?? '',
      createdAt: DateTime.tryParse(w.createdAt ?? ''),
    );
  }
}

/// `ticket.comment_added` reads as "Comment added".
String webhookEventLabel(String name) {
  final action = name.contains('.') ? name.split('.').last : name;
  if (action.isEmpty) return name;
  final words = action.replaceAll('_', ' ');
  return '${words[0].toUpperCase()}${words.substring(1)}';
}
