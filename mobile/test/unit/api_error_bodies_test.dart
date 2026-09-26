import 'dart:convert';

import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A refused write carries the server's reason back to the person.
///
/// PATCH and DELETE used to drop the body of a failed response, so a caller
/// reading the structured `errors` got nothing, while POST and PUT kept it.
/// And the deal form cast `errors` to a map, so a server that answered with a
/// plain-string `errors` surfaced as a Dart type error instead of its message.
void main() {
  const url = 'https://api.test/api/thing/1/';

  tearDown(() => ApiService().setClientForTesting(http.Client()));

  void answer(int status, Object body) {
    ApiService().setClientForTesting(
      MockClient((_) async => http.Response(jsonEncode(body), status)),
    );
  }

  group('a failed PATCH or DELETE keeps its body', () {
    const refusal = {
      'error': true,
      'errors': {
        'status': ['A ticket becomes Duplicate only by merging it.'],
      },
    };

    test('PATCH', () async {
      answer(400, refusal);
      final response = await ApiService().patch(url, {'status': 'Duplicate'});
      expect(response.success, isFalse);
      expect(response.data, refusal);
      expect(
        response.message,
        'Status: A ticket becomes Duplicate only by merging it.',
      );
    });

    test('DELETE', () async {
      answer(400, {'error': 'This stage still has 2 lead(s).'});
      final response = await ApiService().delete(url);
      expect(response.success, isFalse);
      expect(response.data, {'error': 'This stage still has 2 lead(s).'});
    });

    test('a successful DELETE with a list body is not a crash', () async {
      answer(200, ['unexpected']);
      final response = await ApiService().delete(url);
      expect(response.success, isTrue);
      expect(response.data, isNull);
    });
  });

  group('the deal form reads either shape of errors', () {
    final deal = Deal(
      id: '',
      title: 'Renewal',
      value: 100,
      stage: 'PROSPECTING',
      probability: 10,
      companyName: '',
      assignedTo: '',
      priority: Priority.medium,
      createdAt: DateTime(2026, 9, 1),
      updatedAt: DateTime(2026, 9, 1),
    );

    /// Every GET (the list the notifier loads) answers empty; every write
    /// answers [status] with [body].
    ProviderContainer containerAnswering(int status, Object body) {
      ApiService().setClientForTesting(
        MockClient((request) async {
          if (request.method == 'GET') return http.Response('{}', 200);
          return http.Response(jsonEncode(body), status);
        }),
      );
      final container = ProviderContainer();
      addTearDown(container.dispose);
      return container;
    }

    test('a plain-string errors is shown as the message', () async {
      final container = containerAnswering(400, {
        'error': true,
        'errors': 'Amount is required to win a deal.',
      });
      final notifier = container.read(dealsProvider.notifier);

      final created = await notifier.createDeal(deal);
      expect(created.success, isFalse);
      expect(created.error, 'Amount is required to win a deal.');

      final updated = await notifier.updateDeal('d1', deal);
      expect(updated.success, isFalse);
      expect(updated.error, 'Amount is required to win a deal.');
    });

    test('a field map is shown field by field', () async {
      final container = containerAnswering(400, {
        'error': true,
        'errors': {
          'name': ['This field is required.'],
        },
      });
      final result = await container
          .read(dealsProvider.notifier)
          .createDeal(deal);
      expect(result.success, isFalse);
      expect(result.error, 'Name: This field is required.');
    });
  });
}
