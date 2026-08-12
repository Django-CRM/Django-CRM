import 'dart:convert';

import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Bulk ticket actions: mobile data layer only (the UI bulk bar is a later
/// task). Both methods must POST the raw request the backend expects and hand
/// the response envelope back untouched, so the UI task can summarize
/// `{error, updated/deleted, results}` for itself.
class RecordingClient extends http.BaseClient {
  http.BaseRequest? request;
  List<int>? requestBody;
  Map<String, dynamic> responseBody = const {
    'cases': [],
    'cases_count': 0,
    'offset': null,
  };
  int statusCode = 200;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest incoming) async {
    request = incoming;
    requestBody = await incoming.finalize().toBytes();
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(responseBody))),
      statusCode,
      headers: {'content-type': 'application/json'},
      request: incoming,
    );
  }
}

void main() {
  late RecordingClient client;
  late ProviderContainer container;

  setUp(() async {
    client = RecordingClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
    // Let the list's own initial fetch (the provider's build()) settle
    // before issuing a bulk call, so that GET cannot race the POST below on
    // the shared RecordingClient.
    await container.read(ticketsProvider.future);
  });

  tearDown(() {
    container.dispose();
    ApiService().clearAuth();
    ApiService().setRefreshCallback(null);
    ApiService().setClientForTesting(http.Client());
  });

  test('bulkUpdate posts ids and fields to the bulk update endpoint', () async {
    client.responseBody = {
      'error': false,
      'updated': 2,
      'results': [
        {'id': 'a', 'success': true},
        {'id': 'b', 'success': true},
      ],
    };

    final response = await container
        .read(ticketsProvider.notifier)
        .bulkUpdate(['a', 'b'], {'priority': 'Urgent'});

    expect(client.request!.method, 'POST');
    expect(client.request!.url.toString(), ApiConfig.casesBulkUpdate);
    expect(jsonDecode(utf8.decode(client.requestBody!)), {
      'ids': ['a', 'b'],
      'fields': {'priority': 'Urgent'},
    });
    // The envelope comes back untouched: the UI task summarizes it, this
    // layer does not.
    expect(response.success, isTrue);
    expect(response.data, client.responseBody);
  });

  test('bulkDelete posts ids to the bulk delete endpoint', () async {
    client.responseBody = {
      'error': false,
      'deleted': 1,
      'results': [
        {'id': 'a', 'success': true},
      ],
    };

    final response = await container
        .read(ticketsProvider.notifier)
        .bulkDelete(['a']);

    expect(client.request!.method, 'POST');
    expect(client.request!.url.toString(), ApiConfig.casesBulkDelete);
    expect(jsonDecode(utf8.decode(client.requestBody!)), {
      'ids': ['a'],
    });
    expect(response.success, isTrue);
    expect(response.data, client.responseBody);
  });
}
