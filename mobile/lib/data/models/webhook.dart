/// Outbound webhooks, as `/api/webhooks/` returns them.
///
/// The signing secret is never part of these models. The API sends it only in
/// the response to a create or a rotate, and the provider hands it straight to
/// the one dialog that shows it.
library;

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

  factory WebhookEventGroup.fromJson(Map<String, dynamic> json) {
    return WebhookEventGroup(
      module: json['module']?.toString() ?? '',
      label: json['label']?.toString() ?? '',
      events: (json['events'] as List? ?? const [])
          .map((e) => e.toString())
          .toList(growable: false),
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
    this.createdAt,
  });

  static const String formatJson = 'json';
  static const String formatSlack = 'slack';

  final String id;
  final String url;
  final String description;
  final List<String> events;
  final String format;
  final bool isActive;

  /// Why the server turned it off (it answered 410 Gone). Empty otherwise.
  final String disabledReason;

  /// `whsec_...abcd`. The full secret is never on a list or detail response.
  final String secretHint;
  final DateTime? createdAt;

  String get title => description.isNotEmpty ? description : url;

  String get formatLabel =>
      format == formatSlack ? 'Slack message' : 'Signed JSON';

  factory WebhookEndpoint.fromJson(Map<String, dynamic> json) {
    return WebhookEndpoint(
      id: json['id']?.toString() ?? '',
      url: json['url']?.toString() ?? '',
      description: json['description']?.toString() ?? '',
      events: (json['events'] as List? ?? const [])
          .map((e) => e.toString())
          .toList(growable: false),
      format: json['format']?.toString() ?? formatJson,
      isActive: json['is_active'] == true,
      disabledReason: json['disabled_reason']?.toString() ?? '',
      secretHint: json['secret_hint']?.toString() ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
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

  factory WebhookDelivery.fromJson(Map<String, dynamic> json) {
    final code = json['response_status'];
    final attempts = json['attempts'];
    return WebhookDelivery(
      id: json['id']?.toString() ?? '',
      event: json['event']?.toString() ?? '',
      status: json['status']?.toString() ?? pending,
      attempts: attempts is int ? attempts : 0,
      responseStatus: code is int ? code : null,
      error: json['error']?.toString() ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
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
