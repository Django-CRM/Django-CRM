import 'dart:convert';

import 'package:bottle_crm/data/models/web_form.dart';
import 'package:bottle_crm/providers/web_forms_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{}';
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    final bytes = await request.finalize().toBytes();
    bodies.add(utf8.decode(bytes));
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

void main() {
  group('WebFormsNotifier', () {
    late ProviderContainer container;
    late _FakeClient client;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
    });

    tearDown(() => container.dispose());

    Future<WebFormsState> readForms() {
      container.listen(webFormsProvider, (_, _) {});
      return container.read(webFormsProvider.future);
    }

    test(
      'reads the totals the server computed, not the page it returned',
      () async {
        // The page holds one row while the org holds thirteen forms. Counting
        // `results` would report "1 published of 1" on a settings screen.
        client.body = '''
      {"count": 13,
       "results": [
         {"id": "f1", "name": "Contact us", "is_published": true,
          "submission_count": 4, "field_count": 3}
       ],
       "totals": {"count": 13, "published": 5, "submissions_30d": 40,
                  "spam_30d": 7}}
      ''';
        final state = await readForms();
        expect(state.forms.single.name, 'Contact us');
        expect(state.totals.count, 13);
        expect(state.totals.published, 5);
        expect(state.totals.drafts, 8);
        expect(state.totals.submissions30d, 40);
        expect(state.totals.spam30d, 7);
        expect(state.truncated, isTrue);
      },
    );

    test('is not truncated when the page holds everything', () async {
      client.body =
          '{"count": 1, "results": [{"id": "f1", "name": "One"}], '
          '"totals": {"count": 1, "published": 0}}';
      final state = await readForms();
      expect(state.truncated, isFalse);
    });

    test('survives a response with no totals block', () async {
      client.body = '{"count": 0, "results": []}';
      final state = await readForms();
      expect(state.totals.count, 0);
      expect(state.totals.drafts, 0);
    });

    test(
      'a refused publish returns the server message rather than swallowing it',
      () async {
        client.body = '{"count": 0, "results": [], "totals": {}}';
        await readForms();

        client.status = 400;
        client.body =
            '{"error": true, '
            '"errors": "Add an email field before publishing."}';
        final message = await container
            .read(webFormsProvider.notifier)
            .publish('f1');

        // The instruction IS the response. A generic "could not publish" would
        // throw away the only thing the person needs to know.
        expect(message, contains('Add an email field'));
      },
    );

    test('a refused write does not flip local state', () async {
      client.body = '''
      {"count": 1,
       "results": [{"id": "f1", "name": "Contact us", "is_published": false}],
       "totals": {"count": 1, "published": 0}}
      ''';
      final before = await readForms();
      expect(before.forms.single.isPublished, isFalse);

      client.status = 403;
      client.body = '{"error": true, "errors": "Admin access required"}';
      await container.read(webFormsProvider.notifier).publish('f1');

      // Nothing optimistic: a form shown as live when the server refused is an
      // admin believing an endpoint is open when it is closed, or the reverse.
      final after = await container.read(webFormsProvider.future);
      expect(after.forms.single.isPublished, isFalse);
    });

    test(
      'the update sends PUT and omits the secret when none was typed',
      () async {
        client.body = '{"count": 0, "results": [], "totals": {}}';
        await readForms();

        final form = WebForm.fromJson({
          'id': 'f1',
          'name': 'Contact us',
          'captcha_provider': 'turnstile',
          'has_captcha_secret': true,
        });
        client.bodies.clear();
        client.sent.clear();
        await container
            .read(webFormsProvider.notifier)
            .updateWebForm('f1', form.toJson());

        expect(client.sent.first.method, 'PUT');
        final sent = jsonDecode(client.bodies.first) as Map<String, dynamic>;
        expect(sent.containsKey('captcha_secret'), isFalse);
        expect(sent.containsKey('is_published'), isFalse);
        expect(sent['name'], 'Contact us');
      },
    );

    test('the create sends only a name', () async {
      client.body = '{"count": 0, "results": [], "totals": {}}';
      await readForms();

      client.bodies.clear();
      client.body = '{"id": "new-id", "name": "Newsletter"}';
      final id = await container
          .read(webFormsProvider.notifier)
          .createWebForm('Newsletter');

      expect(id, 'new-id');
      final sent = jsonDecode(client.bodies.first) as Map<String, dynamic>;
      expect(sent.keys.toSet(), {'name'});
    });
  });
}
