import 'dart:convert';

import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/approval.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/tickets/ticket_approval_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Approval to close on the ticket screen, the same gates the web's
/// `approval.js` applies: every action follows a fact the server sent
/// (`approval_rule`, `comment_permission`, each row's `can_act` and
/// `can_cancel`), never the viewer's role.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const rule = ApprovalRuleSummary(id: 'r1', name: 'Urgent close');

  Approval row({
    String id = 'ap1',
    ApprovalState state = ApprovalState.pending,
    bool canAct = false,
    bool canCancel = false,
    String ruleId = 'r1',
  }) => Approval(
    id: id,
    state: state,
    canAct: canAct,
    canCancel: canCancel,
    ruleSummary: ApprovalRuleSummary(id: ruleId, name: 'Urgent close'),
  );

  group('ticketApprovalView', () {
    test('hidden when no rule gates the ticket and nobody asked', () {
      final v = ticketApprovalView(
        const [],
        null,
        canWrite: true,
        isOpen: true,
      );
      expect(v.show, isFalse);
      expect(v.canRequest, isFalse);
    });

    test('a request is offered only with a rule, write access and an open '
        'ticket', () {
      expect(
        ticketApprovalView(
          const [],
          rule,
          canWrite: true,
          isOpen: true,
        ).canRequest,
        isTrue,
      );
      expect(
        ticketApprovalView(
          const [],
          rule,
          canWrite: false,
          isOpen: true,
        ).canRequest,
        isFalse,
      );
      expect(
        ticketApprovalView(
          const [],
          rule,
          canWrite: true,
          isOpen: false,
        ).canRequest,
        isFalse,
      );
      expect(
        ticketApprovalView(
          [row(state: ApprovalState.rejected)],
          null,
          canWrite: true,
          isOpen: true,
        ).canRequest,
        isFalse,
      );
    });

    test('no second request while one is pending or approved for the rule', () {
      for (final state in [ApprovalState.pending, ApprovalState.approved]) {
        expect(
          ticketApprovalView(
            [row(state: state)],
            rule,
            canWrite: true,
            isOpen: true,
          ).canRequest,
          isFalse,
        );
      }
      for (final state in [ApprovalState.rejected, ApprovalState.cancelled]) {
        expect(
          ticketApprovalView(
            [row(state: state)],
            rule,
            canWrite: true,
            isOpen: true,
          ).canRequest,
          isTrue,
        );
      }
      // An approval under an older rule does not satisfy today's gate.
      expect(
        ticketApprovalView(
          [row(state: ApprovalState.approved, ruleId: 'r0')],
          rule,
          canWrite: true,
          isOpen: true,
        ).canRequest,
        isTrue,
      );
    });

    test('nothing is offered when the list failed to load', () {
      final v = ticketApprovalView(null, rule, canWrite: true, isOpen: true);
      expect(v.failed, isTrue);
      expect(v.canRequest || v.canDecide || v.canWithdraw, isFalse);
    });

    test('decide follows can_act, withdraw follows can_cancel', () {
      expect(
        ticketApprovalView(
          [row(canAct: true)],
          rule,
          canWrite: true,
          isOpen: true,
        ).canDecide,
        isTrue,
      );
      expect(
        ticketApprovalView(
          [row()],
          rule,
          canWrite: true,
          isOpen: true,
        ).canDecide,
        isFalse,
      );
      expect(
        ticketApprovalView(
          [row(canCancel: true)],
          rule,
          canWrite: true,
          isOpen: true,
        ).canWithdraw,
        isTrue,
      );
      expect(
        ticketApprovalView(
          [row(canCancel: true, state: ApprovalState.rejected)],
          rule,
          canWrite: true,
          isOpen: true,
        ).canWithdraw,
        isFalse,
      );
    });

    test('the model reads can_act, can_cancel and is_own_request', () {
      final a = Approval.fromJson(const {
        'id': 'x',
        'state': 'pending',
        'can_act': true,
        'can_cancel': true,
        'is_own_request': true,
      });
      expect(a.canAct, isTrue);
      expect(a.canCancel, isTrue);
      expect(a.isOwnRequest, isTrue);
      final b = Approval.fromJson(const {'id': 'y', 'state': 'pending'});
      expect(b.canAct, isFalse);
      expect(b.canCancel, isFalse);
      expect(b.isOwnRequest, isFalse);
    });
  });

  group('TicketApprovalPanel', () {
    late _Client client;

    setUp(() {
      client = _Client();
      ApiService().setClientForTesting(client);
    });
    tearDown(() => ApiService().setClientForTesting(http.Client()));

    void phone(WidgetTester tester, double textScale) {
      tester.view.devicePixelRatio = 3.0;
      tester.view.physicalSize = const Size(390 * 3, 844 * 3);
      tester.platformDispatcher.textScaleFactorTestValue = textScale;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    }

    Future<void> pump(
      WidgetTester tester, {
      ApprovalRuleSummary? approvalRule = rule,
      bool canWrite = true,
      double textScale = 1.0,
    }) async {
      phone(tester, textScale);
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light,
            home: Scaffold(
              body: SingleChildScrollView(
                child: TicketApprovalPanel(
                  ticketId: 't1',
                  approvalRule: approvalRule,
                  canWrite: canWrite,
                  isOpen: true,
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    Map<String, dynamic> pending({
      bool canAct = false,
      bool own = false,
      bool canCancel = false,
    }) => {
      'id': 'ap1',
      'state': 'pending',
      'can_cancel': canCancel || own,
      'note': 'Customer asked for a refund, please check before closing.',
      'can_act': canAct,
      'is_own_request': own,
      'created_at': '2026-09-26T09:00:00Z',
      'requested_by': {
        'id': 'p1',
        'email': 'a.rather.long.address.for.a.rep@example.com',
      },
      'rule_summary': {'id': 'r1', 'name': 'Urgent close'},
    };

    for (final scale in [1.0, 1.3]) {
      testWidgets('an approver sees Approve and Reject at 390px, ${scale}x', (
        tester,
      ) async {
        client.approvals = [pending(canAct: true)];
        await pump(tester, textScale: scale);
        expect(tester.takeException(), isNull);
        expect(find.text('Approve'), findsOneWidget);
        expect(find.text('Reject'), findsOneWidget);
        expect(find.text('Withdraw request'), findsNothing);
        expect(find.text('Request approval'), findsNothing);
        expect(client.paths.single, contains('case=t1'));
      });
    }

    testWidgets('a requester sees only Withdraw on their own request', (
      tester,
    ) async {
      client.approvals = [pending(own: true)];
      await pump(tester, textScale: 1.3);
      expect(tester.takeException(), isNull);
      expect(find.text('Withdraw request'), findsOneWidget);
      expect(find.text('Approve'), findsNothing);
      expect(find.text('Reject'), findsNothing);
    });

    testWidgets('an admin sees Withdraw on someone else\'s request', (
      tester,
    ) async {
      client.approvals = [pending(canCancel: true)];
      await pump(tester, textScale: 1.3);
      expect(tester.takeException(), isNull);
      expect(find.text('Withdraw request'), findsOneWidget);
      expect(find.text('Approve'), findsNothing);
    });

    testWidgets('someone outside the pool sees the state and no buttons', (
      tester,
    ) async {
      client.approvals = [pending()];
      await pump(tester);
      expect(find.text('Pending'), findsOneWidget);
      expect(find.byType(OutlinedButton), findsNothing);
      expect(find.byType(FilledButton), findsNothing);
    });

    testWidgets('a gated ticket with no request offers one to a writer', (
      tester,
    ) async {
      await pump(tester, textScale: 1.3);
      expect(tester.takeException(), isNull);
      expect(
        find.text('Closing this ticket needs approval under Urgent close.'),
        findsOneWidget,
      );
      expect(find.text('Request approval'), findsOneWidget);
    });

    testWidgets('but not to a reader who may not write to it', (tester) async {
      await pump(tester, canWrite: false);
      expect(find.text('Request approval'), findsNothing);
    });

    testWidgets('nothing at all on a ticket no rule gates', (tester) async {
      await pump(tester, approvalRule: null);
      expect(find.text('APPROVAL'), findsNothing);
    });
  });
}

/// Answers the approvals list with [approvals] and records what was asked.
class _Client extends http.BaseClient {
  List<Map<String, dynamic>> approvals = [];
  final List<String> paths = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    paths.add(request.url.toString());
    return http.StreamedResponse(
      Stream.value(utf8.encode(jsonEncode({'approvals': approvals}))),
      200,
      request: request,
    );
  }
}
