import 'dart:convert';

import 'package:bottle_crm/providers/duplicates_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The G19 duplicate calls: what each one sends, and how each failure reads.
///
/// The API is the only judge of who may see or merge what; these pin that the
/// client sends only the module's own fields and turns every refusal into a
/// sentence instead of a crash or a silent success.
class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{"duplicates": []}';
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

const _hit = '''
{"id": "c2", "name": "Ann Lee", "email": "ann@x.com", "phone": null,
 "matched_on": ["email", "phone"], "can_delete": true}
''';

void main() {
  late _FakeClient client;
  late DuplicatesApi api;

  setUp(() {
    client = _FakeClient();
    ApiService().setClientForTesting(client);
    api = DuplicatesApi();
  });

  test('module paths parse, and nothing else does', () {
    expect(DuplicateModule.parse('contacts'), DuplicateModule.contacts);
    expect(DuplicateModule.parse('invoices'), isNull);
    expect(DuplicateModule.parse(null), isNull);
  });

  test('matched rules read as a phrase', () {
    expect(matchedLabel(['email']), 'email');
    expect(matchedLabel(['email', 'phone', 'name']), 'email, phone and name');
  });

  group('the create-form check', () {
    test("sends only the module's own non-blank fields", () async {
      client.body = '{"duplicates": [$_hit]}';
      final hits = await api.check(DuplicateModule.contacts, {
        'email': ' ann@x.com ',
        'phone': '',
        'name': 'not a contact field',
      });

      expect(client.sent.single.method, 'POST');
      expect(client.sent.single.url.path, '/api/contacts/duplicates/');
      // In the body, never the URL, which access logs record.
      expect(client.sent.single.url.query, isEmpty);
      expect(jsonDecode(client.bodies.single), {'email': 'ann@x.com'});
      expect(hits.single.name, 'Ann Lee');
      expect(hits.single.phone, '');
      expect(hits.single.matchedLabelText, 'email and phone');
      expect(hits.single.canDelete, isTrue);
    });

    test('asks nothing when there is nothing to match on', () async {
      expect(await api.check(DuplicateModule.accounts, {'name': ' '}), isEmpty);
      expect(client.sent, isEmpty);
    });

    test('a failed check is no hits, not an error', () async {
      client.status = 429;
      client.body = '{"detail": "Request was throttled."}';
      expect(await api.check(DuplicateModule.leads, {'email': 'a@x.com'}), []);
    });
  });

  test(
    'a record check carries whether the record itself may be deleted',
    () async {
      client.body = '{"can_delete": false, "duplicates": [$_hit]}';
      final found = await api.forRecord(DuplicateModule.leads, 'l1');
      expect(client.sent.single.url.path, '/api/leads/l1/duplicates/');
      expect(found.canDelete, isFalse);
      expect(found.hits, hasLength(1));
    },
  );

  group('merging', () {
    test('posts the loser to the keeper and succeeds quietly', () async {
      client.body = '{"error": false, "message": "Merged", "id": "k1"}';
      final failure = await api.merge(DuplicateModule.accounts, 'k1', 'l1');
      expect(failure, isNull);
      expect(client.sent.single.method, 'POST');
      expect(client.sent.single.url.path, '/api/accounts/k1/merge/');
      expect(jsonDecode(client.bodies.single), {'merge_id': 'l1'});
    });

    test('a hidden or missing record reads as one sentence', () async {
      client.status = 404;
      client.body = '{"detail": "No Account matches the given query."}';
      final failure = await api.merge(DuplicateModule.accounts, 'k1', 'l1');
      expect(failure, contains('no longer exists, or you do not have access'));
    });

    test("a refusal carries the server's own reason", () async {
      client.status = 403;
      client.body =
          '{"error": true, "errors": "You may not delete the record being merged away, so you cannot merge it."}';
      final failure = await api.merge(DuplicateModule.contacts, 'k1', 'l1');
      expect(failure, contains('You may not delete'));
    });
  });

  test('a side the caller cannot open is a not-found error', () async {
    client.status = 404;
    client.body = '{"detail": "No Contact matches the given query."}';
    await expectLater(
      api.side(DuplicateModule.contacts, 'x'),
      throwsA(
        isA<MergeSideError>().having((e) => e.notFound, 'notFound', isTrue),
      ),
    );
  });
}
