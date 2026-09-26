import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/providers/documents_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Which verb a document edit goes as, and what it carries.
///
/// `DocumentDetailView.put` clears `shared_to` and `teams` whatever the body
/// says and re-adds only active profiles; `patch` never touches either list.
/// So an edit that leaves sharing alone must be a PATCH with neither key, or
/// a share to a deactivated person is lost on every rename.
class _FakeClient extends http.BaseClient {
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    return http.StreamedResponse(
      Stream.value(utf8.encode('{"error": false}')),
      200,
      request: request,
    );
  }
}

void main() {
  late ProviderContainer container;
  late _FakeClient client;

  setUp(() {
    client = _FakeClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  /// The one request that is not a read.
  int writeIndex() {
    final writes = [
      for (var i = 0; i < client.sent.length; i++)
        if (client.sent[i].method != 'GET') i,
    ];
    expect(writes, hasLength(1));
    return writes.single;
  }

  test('no lists is a PATCH that names neither', () async {
    final res = await container
        .read(documentsProvider.notifier)
        .updateDocument('d1', title: 'Renamed', status: 'active');
    expect(res.success, isTrue);
    final i = writeIndex();
    expect(client.sent[i].method, 'PATCH');
    final body = jsonDecode(client.bodies[i]) as Map<String, dynamic>;
    expect(body, {'title': 'Renamed', 'status': 'active'});
  });

  test('both lists is a PUT that carries both', () async {
    await container
        .read(documentsProvider.notifier)
        .updateDocument(
          'd1',
          title: 'Renamed',
          status: 'active',
          sharedTo: const ['p1'],
          teams: const [],
        );
    final i = writeIndex();
    expect(client.sent[i].method, 'PUT');
    final body = jsonDecode(client.bodies[i]) as Map<String, dynamic>;
    expect(body['shared_to'], ['p1']);
    expect(body['teams'], isEmpty);
  });

  test('replacing the file with sharing untouched is a multipart PATCH '
      'without either list', () async {
    final dir = await Directory.systemTemp.createTemp('doc_verb');
    addTearDown(() => dir.delete(recursive: true));
    final file = File('${dir.path}/contract.pdf')..writeAsStringSync('pdf');

    await container
        .read(documentsProvider.notifier)
        .updateDocument(
          'd1',
          title: 'Renamed',
          status: 'active',
          filePath: file.path,
          fileName: 'contract.pdf',
        );
    final i = writeIndex();
    expect(client.sent[i].method, 'PATCH');
    expect(client.bodies[i], contains('name="title"'));
    expect(client.bodies[i], isNot(contains('name="shared_to"')));
    expect(client.bodies[i], isNot(contains('name="teams"')));
  });
}
