import 'dart:async';
import 'dart:convert';

import 'package:bottle_crm/providers/profile_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  late ProviderContainer container;
  late List<Map<String, dynamic>> patches;
  late List<Completer<int>> answers;

  setUp(() {
    patches = [];
    answers = [];
    ApiService().setClientForTesting(
      MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(
            jsonEncode({
              'id': 'p1',
              'user': {'id': 'u1', 'email': 'a@b.co', 'name': 'Ada'},
              'lead_alerts_email': true,
              'lead_alerts_push': true,
            }),
            200,
          );
        }
        patches.add(jsonDecode(request.body) as Map<String, dynamic>);
        final answer = Completer<int>();
        answers.add(answer);
        return http.Response('{}', await answer.future);
      }),
    );
    container = ProviderContainer();
  });

  tearDown(() {
    container.dispose();
    ApiService().setClientForTesting(http.Client());
  });

  test('sends explicit booleans for the switch that changed', () async {
    await container.read(profileProvider.future);
    final pending = container
        .read(profileProvider.notifier)
        .setLeadAlerts(email: false);
    await Future<void>.delayed(Duration.zero);
    answers.single.complete(200);

    expect(await pending, isTrue);
    expect(patches.single, {'lead_alerts_email': false});
    expect(container.read(profileProvider).value!.leadAlertsEmail, isFalse);
  });

  test('a refusal puts back only its own switch', () async {
    await container.read(profileProvider.future);
    final notifier = container.read(profileProvider.notifier);

    final first = notifier.setLeadAlerts(email: false);
    await Future<void>.delayed(Duration.zero);
    final second = notifier.setLeadAlerts(push: false);
    await Future<void>.delayed(Duration.zero);

    answers[0].complete(400);
    expect(await first, isFalse);
    answers[1].complete(200);
    expect(await second, isTrue);

    final profile = container.read(profileProvider).value!;
    expect(profile.leadAlertsEmail, isTrue);
    expect(profile.leadAlertsPush, isFalse);
  });
}
