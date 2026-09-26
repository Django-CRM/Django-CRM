import 'dart:convert';
import 'dart:io';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/providers/accounts_provider.dart';
import 'package:bottle_crm/providers/contacts_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/screens/accounts/account_detail_screen.dart';
import 'package:bottle_crm/screens/contacts/contact_detail_screen.dart';
import 'package:bottle_crm/screens/deals/deal_detail_screen.dart';
import 'package:bottle_crm/screens/leads/lead_detail_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// "Not found" only when the server said 404.
///
/// The API answers a record this user may not open exactly as one that does
/// not exist, so a 404 reads "not found, or no access". Being offline or a
/// server error says nothing about the record, so it reads "could not load",
/// with Retry, never "not found".
class _Client extends http.BaseClient {
  /// An HTTP status to answer every request with, or null to fail offline.
  int? status;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    await request.finalize().toBytes();
    if (status == null) throw const SocketException('offline');
    return http.StreamedResponse(
      Stream.value(utf8.encode('{"detail": "x"}')),
      status!,
      request: request,
    );
  }
}

typedef _Fetch =
    Future<Object?> Function(ProviderContainer c, void Function() notFound);

final Map<String, _Fetch> _fetchers = {
  'lead': (c, nf) =>
      c.read(leadsProvider.notifier).getLeadDetail('x', onNotFound: nf),
  'deal': (c, nf) =>
      c.read(dealsProvider.notifier).getDealDetail('x', onNotFound: nf),
  'account': (c, nf) =>
      c.read(accountsProvider.notifier).getAccount('x', onNotFound: nf),
  'contact': (c, nf) =>
      c.read(contactsProvider.notifier).getContact('x', onNotFound: nf),
};

final Map<String, Widget> _screens = {
  'lead': const LeadDetailScreen(leadId: 'x'),
  'deal': const DealDetailScreen(dealId: 'x'),
  'account': const AccountDetailScreen(accountId: 'x'),
  'contact': const ContactDetailScreen(contactId: 'x'),
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late _Client client;

  setUp(() {
    client = _Client();
    ApiService().setClientForTesting(client);
  });

  // (label, status): null is offline, which the service reports as status 0.
  const outcomes = <(String, int?, bool)>[
    ('404', 404, true),
    ('500', 500, false),
    ('offline', null, false),
  ];

  for (final entry in _fetchers.entries) {
    for (final (label, status, isNotFound) in outcomes) {
      test(
        '${entry.key}: $label ${isNotFound ? 'counts' : 'does not count'} as not found',
        () async {
          client.status = status;
          final container = ProviderContainer();
          addTearDown(container.dispose);
          var fired = false;

          final result = await entry.value(container, () => fired = true);

          expect(result, isNull);
          expect(fired, isNotFound);
        },
      );
    }
  }

  for (final entry in _screens.entries) {
    for (final (label, status, isNotFound) in outcomes) {
      testWidgets('${entry.key} screen: $label', (tester) async {
        client.status = status;
        tester.view.devicePixelRatio = 3.0;
        tester.view.physicalSize = const Size(390 * 3, 844 * 3);
        tester.platformDispatcher.textScaleFactorTestValue = 1.3;
        addTearDown(tester.view.reset);
        addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);

        await tester.pumpWidget(
          ProviderScope(
            child: MaterialApp(theme: AppTheme.light, home: entry.value),
          ),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        final access = find.textContaining('have access to');
        final couldNot = find.textContaining('Could not load');
        expect(access, isNotFound ? findsWidgets : findsNothing);
        expect(couldNot, isNotFound ? findsNothing : findsWidgets);
        expect(find.text('Retry'), findsOneWidget);
      });
    }
  }
}
