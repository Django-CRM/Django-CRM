import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/config/api_config.dart';
import 'package:bottle_crm/data/models/support_ticket.dart';
import 'package:bottle_crm/providers/support_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import '../helpers/fake_platform_file.dart';

Map<String, dynamic> ticketJson({
  List<Map<String, dynamic>> messages = const [],
}) => {
  'id': '4d60686d-bd3d-4e33-b114-ab36f194a6cd',
  'reference': 'SUP-4D60686D',
  'subject': 'Export failed',
  'category': 'technical',
  'category_label': 'Technical issue',
  'status': 'waiting_on_customer',
  'status_label': 'Waiting on you',
  'priority': 'normal',
  'priority_label': 'Normal',
  'message_count': messages.length,
  'assigned': true,
  'last_activity_at': '2026-08-08T02:00:00Z',
  'created_at': '2026-08-08T01:00:00Z',
  'messages': messages,
};

class RecordingClient extends http.BaseClient {
  http.BaseRequest? request;
  List<int>? requestBody;
  Map<String, dynamic> responseBody = ticketJson();
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
  late Directory tempDir;

  setUpAll(
    () => tempDir = Directory.systemTemp.createTempSync('support-test-'),
  );
  tearDownAll(() => tempDir.deleteSync(recursive: true));
  setUp(() {
    client = RecordingClient();
    ApiService().setClientForTesting(client);
  });
  tearDown(() {
    ApiService().clearAuth();
    ApiService().setRefreshCallback(null);
    ApiService().setClientForTesting(http.Client());
  });

  test('parses the customer-visible support contract', () {
    final ticket = SupportTicket.fromJson(
      ticketJson(
        messages: [
          {
            'id': 'm1',
            'author_label': 'BottleCRM Support',
            'author_type': 'staff',
            'body': 'Please try again.',
            'attachment_name': 'steps.pdf',
            'created_at': '2026-08-08T01:30:00Z',
          },
        ],
      ),
    );

    expect(ticket.status, SupportStatus.waitingOnCustomer);
    expect(ticket.category, SupportCategory.technical);
    expect(ticket.assigned, isTrue);
    expect(ticket.messages.single.isStaff, isTrue);
    expect(ticket.messages.single.attachmentName, 'steps.pdf');
  });

  test('create sends only customer-writable JSON fields', () async {
    await SupportRepository().create(
      subject: '  Export failed  ',
      category: SupportCategory.technical,
      body: '  The button returns an error.  ',
    );

    expect(client.request!.url.path, endsWith('/api/support/'));
    final body = jsonDecode(utf8.decode(client.requestBody!));
    expect(body.keys.toSet(), {'subject', 'category', 'body'});
    expect(body['subject'], 'Export failed');
    expect(body['category'], 'technical');
  });

  test('attachment create uses the support attachment field', () async {
    final file = File('${tempDir.path}/error.txt')..writeAsStringSync('error');
    await SupportRepository().create(
      subject: 'Export failed',
      category: SupportCategory.technical,
      body: 'Attached.',
      attachment: FakePlatformFile(
        name: 'error.txt',
        size: file.lengthSync(),
        path: file.path,
      ),
    );

    expect(client.request, isA<http.MultipartRequest>());
    final multipart = client.request as http.MultipartRequest;
    expect(multipart.files.single.field, 'attachment');
    expect(multipart.fields.keys.toSet(), {'subject', 'category', 'body'});
  });

  test('reply uses the selected ticket and no server-owned fields', () async {
    await SupportRepository().reply(
      ticketId: 'ticket-1',
      body: ' Still broken. ',
    );

    expect(
      client.request!.url.path,
      endsWith('/api/support/ticket-1/replies/'),
    );
    expect(jsonDecode(utf8.decode(client.requestBody!)), {
      'body': 'Still broken.',
    });
  });

  /// Settle the list provider and hand back its AsyncValue.
  ///
  /// Deliberately not `container.read(provider.future)`: that never completes
  /// for this notifier under the test binding. Nothing in the app awaits it
  /// either, the screens read the AsyncValue through `.when`, so this is the
  /// state the screen actually sees.
  Future<AsyncValue<SupportTicketListData>> settled(
    ProviderContainer container,
  ) async {
    container.listen(supportTicketsProvider, (_, _) {}, fireImmediately: true);
    for (var attempt = 0; attempt < 100; attempt++) {
      final value = container.read(supportTicketsProvider);
      if (!value.isLoading) return value;
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    return container.read(supportTicketsProvider);
  }

  test(
    'a 404 means this deployment has no support queue, not a failure',
    () async {
      // `/api/support/` is served by the enterprise platform_support app, so a
      // community backend has no such route. The list screen turns this into
      // the self-serve half of Help rather than an error state.
      client.statusCode = 404;
      client.responseBody = {'detail': 'Not found.'};
      final container = ProviderContainer();
      addTearDown(container.dispose);

      expect((await settled(container)).error, isA<SupportUnavailable>());
    },
  );

  test('any other failure stays an ordinary error', () async {
    // A deployment that does have support must see an outage as an outage.
    client.statusCode = 500;
    client.responseBody = {'detail': 'Server error.'};
    final container = ProviderContainer();
    addTearDown(container.dispose);

    final error = (await settled(container)).error;
    expect(error, isNotNull);
    expect(error, isNot(isA<SupportUnavailable>()));
  });

  test('support endpoint builders keep attachment downloads authenticated', () {
    expect(ApiConfig.supportTickets, endsWith('/api/support/'));
    expect(
      ApiConfig.supportMessageAttachment('m1'),
      endsWith('/api/support/messages/m1/attachment/'),
    );
  });

  test('private attachment bytes carry authorization in a header', () async {
    ApiService().setAccessToken('not-a-jwt');
    final response = await ApiService().getBytes(
      ApiConfig.supportMessageAttachment('m1'),
    );

    expect(response.success, isTrue);
    expect(client.request!.headers['Authorization'], 'Bearer not-a-jwt');
    expect(
      client.request!.url.query,
      isEmpty,
      reason: 'tokens never belong in URLs',
    );
  });
}
