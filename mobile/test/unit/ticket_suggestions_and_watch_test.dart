import 'dart:convert';

import 'package:bottle_crm/providers/solutions_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Two reads that named keys the API never sent: the article suggestions
/// (`results`, read as `suggestions` / `solutions`) and the viewer's watch
/// state (`is_current_user_watching`, which the API did not send until
/// 1.12.0). Each read the default, so the phone showed no suggestions and
/// always offered "Watch".
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _Client client;
  late ProviderContainer container;
  setUp(() {
    client = _Client();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });
  tearDown(() {
    container.dispose();
    ApiService().setClientForTesting(http.Client());
  });

  test('suggestions come from `results`', () async {
    client.body = {
      'results': [
        {'id': 's1', 'title': 'Reset a password'},
      ],
      'count': 1,
      'q': '',
    };
    final list = await container
        .read(solutionsProvider.notifier)
        .suggestionsFor('t1');
    expect(list.map((s) => s.id), ['s1']);
    expect(client.paths, contains('/api/cases/t1/solution-suggestions/'));
  });

  test('watch state is the server\'s answer about the viewer', () async {
    client.body = {
      'watchers': [
        {'id': 'w1'},
      ],
      'count': 1,
      'is_current_user_watching': true,
    };
    final w = await container.read(ticketsProvider.notifier).getWatchers('t1');
    expect(w!.isCurrentUserWatching, isTrue);
    expect(w.count, 1);

    client.body = {
      'watchers': [
        {'id': 'w1'},
      ],
      'count': 1,
      'is_current_user_watching': false,
    };
    final other = await container
        .read(ticketsProvider.notifier)
        .getWatchers('t1');
    expect(other!.isCurrentUserWatching, isFalse);
  });
}

class _Client extends http.BaseClient {
  Map<String, dynamic> body = {};
  final List<String> paths = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    paths.add(request.url.path);
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode(body))),
      200,
      request: request,
    );
  }
}
