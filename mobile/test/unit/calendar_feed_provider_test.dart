import 'dart:convert';

import 'package:bottle_crm/providers/calendar_feed_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Turning the calendar feed on or off shows the server's own reason when it
/// refuses, as the web does, so a throttled regenerate says when to try again
/// instead of a bare "could not". The fixed sentence stays for a success
/// that somehow carries no URL.
class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{"enabled": false, "created_at": null, "last_used_at": null}';

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final isRead = request.method == 'GET';
    return http.StreamedResponse(
      Stream.value(
        utf8.encode(
          isRead
              ? '{"enabled": false, "created_at": null, "last_used_at": null}'
              : body,
        ),
      ),
      isRead ? 200 : status,
      request: request,
    );
  }
}

void main() {
  const throttled =
      '{"detail": "Request was throttled. Expected available in 3540 seconds."}';

  late _FakeClient client;
  late ProviderContainer container;

  setUp(() {
    client = _FakeClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  Future<CalendarFeedNotifier> feed() async {
    await container.read(calendarFeedProvider.future);
    return container.read(calendarFeedProvider.notifier);
  }

  group('issue', () {
    test('a throttled request shows the server\'s reason', () async {
      client
        ..status = 429
        ..body = throttled;
      final result = await (await feed()).issue();
      expect(
        result.error,
        'Request was throttled. Expected available in 3540 seconds.',
      );
      expect(result.url, isNull);
    });

    test('a success with no URL falls back to the fixed sentence', () async {
      client
        ..status = 200
        ..body = '{"enabled": true}';
      final result = await (await feed()).issue();
      expect(result.error, 'Could not create the calendar feed.');
      expect(result.url, isNull);
    });

    test('a success returns the URL and no error', () async {
      client
        ..status = 201
        ..body =
            '{"enabled": true, "created_at": "2026-09-27T10:00:00Z", '
            '"last_used_at": null, "url": "https://x.test/feed.ics"}';
      final result = await (await feed()).issue();
      expect(result.error, isNull);
      expect(result.url, 'https://x.test/feed.ics');
      expect(container.read(calendarFeedProvider).value?.enabled, isTrue);
    });
  });

  group('disable', () {
    test('a throttled request shows the server\'s reason', () async {
      client
        ..status = 429
        ..body = throttled;
      expect(
        await (await feed()).disable(),
        'Request was throttled. Expected available in 3540 seconds.',
      );
    });

    test('a success returns no error', () async {
      client
        ..status = 204
        ..body = '';
      expect(await (await feed()).disable(), isNull);
    });
  });
}
