import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/webhook.dart';
import '../services/api_service.dart';

/// Outbound webhooks, mirroring `/settings/webhooks` on the web.
///
/// Every route is admin-only server-side (`webhooks/views.py`), reads
/// included. The screens gate on `isOrgAdminProvider` so a member never issues
/// a request that is certain to 403; that is a courtesy, not the boundary.
///
/// Mutations refresh rather than patch local state, and return the API's own
/// sentence on failure so a refusal (a private URL, an unknown event, the
/// ten-endpoint cap) reads as what it is.

class WebhooksState {
  const WebhooksState({
    this.endpoints = const [],
    this.catalogue = const [],
    this.limit = 0,
  });

  final List<WebhookEndpoint> endpoints;
  final List<WebhookEventGroup> catalogue;
  final int limit;

  bool get atLimit => limit > 0 && endpoints.length >= limit;

  WebhookEndpoint? byId(String id) {
    for (final e in endpoints) {
      if (e.id == id) return e;
    }
    return null;
  }
}

/// What the create and edit form collected.
typedef WebhookDraft = ({
  String url,
  String description,
  String format,
  List<String> events,
});

/// The body for a create or edit. Only these four keys are ever sent: the
/// secret is minted by the server and `org` comes from the JWT.
Map<String, dynamic> webhookPayload(WebhookDraft draft) => {
  'url': draft.url.trim(),
  'description': draft.description.trim(),
  'format': draft.format,
  'events': draft.events,
};

/// A result that may carry a secret, shown once by the caller.
typedef SecretResult = ({String? secret, String? error});

class WebhooksNotifier extends AsyncNotifier<WebhooksState> {
  final ApiService _api = ApiService();

  @override
  Future<WebhooksState> build() => _fetch();

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<WebhooksState> _fetch() async {
    final response = await _api.get(ApiConfig.webhooks);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load webhooks');
    }
    final body = response.data!;
    final limit = body['limit'];
    return WebhooksState(
      endpoints: listFromEnvelope(body, const [
        'endpoints',
      ]).map(WebhookEndpoint.fromJson).toList(growable: false),
      catalogue: listFromEnvelope(body, const [
        'event_catalogue',
      ]).map(WebhookEventGroup.fromJson).toList(growable: false),
      limit: limit is int ? limit : 0,
    );
  }

  Future<SecretResult> create(WebhookDraft draft) async {
    final response = await _api.post(ApiConfig.webhooks, webhookPayload(draft));
    if (!response.success || response.data == null) {
      return (
        secret: null,
        error: response.message ?? 'Could not add the webhook',
      );
    }
    final secret = response.data!['secret']?.toString();
    await refresh();
    return (secret: secret, error: null);
  }

  Future<String?> save(String id, WebhookDraft draft) =>
      _patch(id, webhookPayload(draft), 'Could not save the webhook');

  /// Only `is_active` is sent, so a switch can never carry a stale form.
  Future<String?> setActive(String id, bool active) =>
      _patch(id, {'is_active': active}, 'Could not change the webhook');

  Future<String?> _patch(
    String id,
    Map<String, dynamic> body,
    String fallback,
  ) async {
    final response = await _api.patch(ApiConfig.webhook(id), body);
    if (!response.success) return response.message ?? fallback;
    await refresh();
    return null;
  }

  Future<String?> remove(String id) async {
    final response = await _api.delete(ApiConfig.webhook(id));
    if (!response.success) {
      return response.message ?? 'Could not delete the webhook';
    }
    await refresh();
    return null;
  }

  Future<String?> sendTest(String id) async {
    final response = await _api.post(ApiConfig.webhookTest(id), const {});
    return response.success
        ? null
        : response.message ?? 'Could not send a test';
  }

  Future<SecretResult> rotate(String id) async {
    final response = await _api.post(
      ApiConfig.webhookRotateSecret(id),
      const {},
    );
    if (!response.success || response.data == null) {
      return (
        secret: null,
        error: response.message ?? 'Could not rotate the secret',
      );
    }
    final secret = response.data!['secret']?.toString();
    await refresh();
    return (secret: secret, error: null);
  }

  Future<String?> redeliver(String deliveryId) async {
    final response = await _api.post(
      ApiConfig.webhookRedeliver(deliveryId),
      const {},
    );
    return response.success ? null : response.message ?? 'Could not redeliver';
  }
}

final webhooksProvider = AsyncNotifierProvider<WebhooksNotifier, WebhooksState>(
  WebhooksNotifier.new,
);

/// Deliveries per page of an endpoint's log, the same as the web.
const int webhookDeliveryPageSize = 20;

class WebhookDeliveryPage {
  const WebhookDeliveryPage({this.deliveries = const [], this.count = 0});

  final List<WebhookDelivery> deliveries;
  final int count;
}

/// One page of an endpoint's delivery log, keyed by endpoint id and offset.
final webhookDeliveriesProvider =
    FutureProvider.family<WebhookDeliveryPage, ({String id, int offset})>((
      ref,
      key,
    ) async {
      final response = await ApiService().get(
        ApiConfig.webhookDeliveries(key.id),
        queryParams: {
          'limit': '$webhookDeliveryPageSize',
          'offset': '${key.offset}',
        },
      );
      if (!response.success || response.data == null) {
        throw Exception(response.message ?? 'Failed to load deliveries');
      }
      final body = response.data!;
      final deliveries = listFromEnvelope(body, const [
        'results',
      ]).map(WebhookDelivery.fromJson).toList(growable: false);
      final count = body['count'];
      return WebhookDeliveryPage(
        deliveries: deliveries,
        count: count is int ? count : deliveries.length,
      );
    });
