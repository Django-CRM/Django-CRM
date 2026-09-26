import 'dart:convert';

import 'package:bottle_crm/data/models/account.dart';
import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Editing an account, a contact or a lead goes as PATCH.
///
/// The detail endpoints for all three are full replaces on PUT: they clear
/// every relation (`teams`, and for contacts and leads `assigned_to`, `tags`
/// and `contacts` too) whether or not the body mentions it, then re-add only
/// what the body carried. None of the phone's forms sends `teams`, and the
/// lead detail sheets send a single key, so every edit made from the phone
/// unlinked whatever it did not send. PATCH touches a relation only when its
/// key is present, which is what the web has always used.
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

  group('the account edit', () {
    final account = Account.fromJson({
      'id': 'a1',
      'name': 'Acme Corp',
      'country': 'IN',
      'assigned_to': [
        {
          'id': 'p1',
          'user_details': {'email': 'owner@acme.test'},
        },
      ],
      'tags': [
        {'id': 't1', 'name': 'VIP'},
      ],
      'teams': [
        {'id': 'team1', 'name': 'Field'},
      ],
      'contacts': [
        {'id': 'c1', 'first_name': 'Grace', 'last_name': 'Hopper'},
      ],
      'custom_fields': {'tier': 'gold'},
    });

    test('is a PATCH to the account, never a PUT', () async {
      final error = await container
          .read(accountsProvider.notifier)
          .updateAccount('a1', account.toPayload());

      expect(error, isNull);
      final write = client.sent[writeIndex()];
      expect(write.method, 'PATCH');
      expect(write.url.path, endsWith('/accounts/a1/'));
    });

    test('sends the payload unchanged', () async {
      await container
          .read(accountsProvider.notifier)
          .updateAccount('a1', account.toPayload());

      final sent = jsonDecode(client.bodies[writeIndex()]) as Map;
      expect(sent, account.toPayload());
      expect(sent.keys.toSet(), {
        'name',
        'email',
        'phone',
        'website',
        'industry',
        'number_of_employees',
        'annual_revenue',
        'currency',
        'address_line',
        'city',
        'state',
        'postcode',
        'country',
        'description',
        'assigned_to',
        'tags',
        'contacts',
        'custom_fields',
      });
      expect(sent['assigned_to'], ['p1']);
      expect(sent['tags'], ['t1']);
      expect(sent['contacts'], ['c1']);
      expect(sent['custom_fields'], {'tier': 'gold'});
    });

    test('carries no teams key, which is why it cannot be a PUT', () {
      // The account has a team. The form has no control for it, so the key
      // is absent, and absent under PUT means "none".
      expect(account.toPayload().containsKey('teams'), isFalse);
    });
  });

  test('the contact edit is a PATCH to the contact', () async {
    final error = await container.read(contactsProvider.notifier).updateContact(
      'c1',
      {'first_name': 'Grace', 'last_name': 'Hopper'},
    );

    expect(error, isNull);
    final write = client.sent[writeIndex()];
    expect(write.method, 'PATCH');
    expect(write.url.path, endsWith('/contacts/c1/'));
  });

  test(
    'the lead edit is a PATCH, so a one-key sheet changes one key',
    () async {
      final response = await container.read(leadsProvider.notifier).updateLead(
        'l1',
        {
          'tags': ['t1'],
        },
      );

      expect(response.success, isTrue);
      final i = writeIndex();
      expect(client.sent[i].method, 'PATCH');
      expect(client.sent[i].url.path, endsWith('/leads/l1/'));
      expect(jsonDecode(client.bodies[i]), {
        'tags': ['t1'],
      });
    },
  );
}
