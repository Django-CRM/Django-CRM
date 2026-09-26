import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/screens/tickets/approvals_inbox_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The approvals inbox offers each row only what the server says the viewer
/// may do with it: Approve and Reject on `can_act`, Withdraw on `can_cancel`
/// (the requester, or an org admin on anyone's request). "Cancel request" used to sit on every row of the Mine
/// tab, which never holds your own requests, so it always answered 403.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _Client client;
  setUp(() {
    client = _Client();
    ApiService().setClientForTesting(client);
  });
  tearDown(() => ApiService().setClientForTesting(http.Client()));

  Map<String, dynamic> row(
    String id, {
    bool canAct = false,
    bool own = false,
    bool? canCancel,
    String state = 'pending',
  }) => {
    'id': id,
    'state': state,
    'can_act': canAct,
    // The server reports it for the requester while pending; an admin case
    // passes it explicitly.
    'can_cancel': canCancel ?? (own && state == 'pending'),
    'is_own_request': own,
    'case_summary': {'id': 'c-$id', 'name': 'Ticket $id with a long subject'},
    'rule_summary': {'id': 'r1', 'name': 'Urgent close'},
    'requested_by': {'id': 'p1', 'email': 'someone@example.com'},
  };

  Future<void> pump(WidgetTester tester, {double textScale = 1.0}) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.light,
          home: const ApprovalsInboxScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets(
      'an actionable row offers Approve and Reject only (${scale}x)',
      (tester) async {
        client.approvals = [row('a', canAct: true)];
        await pump(tester, textScale: scale);
        expect(tester.takeException(), isNull);
        expect(find.text('Approve'), findsOneWidget);
        expect(find.text('Reject'), findsOneWidget);
        expect(find.text('Withdraw request'), findsNothing);
        expect(find.byTooltip('Cancel request'), findsNothing);
      },
    );
  }

  testWidgets('your own request offers Withdraw and no decision', (
    tester,
  ) async {
    client.approvals = [row('b', own: true)];
    await pump(tester, textScale: 1.3);
    expect(tester.takeException(), isNull);
    expect(find.text('Withdraw request'), findsOneWidget);
    expect(find.text('Approve'), findsNothing);
  });

  for (final scale in [1.0, 1.3]) {
    testWidgets('an admin may withdraw someone else\'s request (${scale}x)', (
      tester,
    ) async {
      client.approvals = [row('e', canAct: true, canCancel: true)];
      await pump(tester, textScale: scale);
      expect(tester.takeException(), isNull);
      expect(find.text('Approve'), findsOneWidget);
      expect(find.text('Withdraw request'), findsOneWidget);
    });
  }

  testWidgets('filing it is not the test: no can_cancel, no Withdraw', (
    tester,
  ) async {
    client.approvals = [row('f', own: true, canCancel: false)];
    await pump(tester);
    expect(find.text('Withdraw request'), findsNothing);
  });

  testWidgets('a row you can neither decide nor withdraw offers nothing', (
    tester,
  ) async {
    client.approvals = [row('c')];
    await pump(tester);
    expect(find.text('Approve'), findsNothing);
    expect(find.text('Withdraw request'), findsNothing);
  });

  testWidgets('a decided row offers nothing even if it was yours', (
    tester,
  ) async {
    client.approvals = [row('d', own: true, state: 'rejected')];
    await pump(tester);
    expect(find.text('Withdraw request'), findsNothing);
  });
}

class _Client extends http.BaseClient {
  List<Map<String, dynamic>> approvals = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode({'approvals': approvals}))),
      200,
      request: request,
    );
  }
}
