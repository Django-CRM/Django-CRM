import 'dart:convert';

import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/ticket_board_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The ticket board: status lanes by default, one pipeline's stages when the
/// picker chooses one.
///
/// Pinned here: which call each mode makes, that the always-empty Duplicate
/// lane never reaches the screen, the two move body shapes, and that a
/// refused move carries the server's own sentence (WIP limit, close gate)
/// rather than a generic one or a field label.
const _support = 'pipe-support';

const _pipelines =
    '''
{"pipelines": [{"id": "$_support", "name": "Support", "is_default": true}]}
''';

/// Status mode, columns deliberately out of order.
const _statusBoard = '''
{
  "mode": "status", "pipeline": null, "total_cases": 3,
  "columns": [
    {"id": "Closed", "name": "Closed", "order": 4, "color": "#22C55E",
     "is_status_column": true, "wip_limit": null, "case_count": 0, "cases": []},
    {"id": "Duplicate", "name": "Duplicate", "order": 6, "color": "#6B7280",
     "is_status_column": true, "wip_limit": null, "case_count": 0, "cases": []},
    {"id": "New", "name": "New", "order": 1, "color": "#3B82F6",
     "is_status_column": true, "wip_limit": null, "case_count": 140, "cases": [
       {"id": "case-1", "name": "Printer on fire", "status": "New",
        "priority": "Urgent", "account_name": "Acme",
        "assigned_to": [{"id": "p1", "user_details": {"email": "sam@example.com"}}],
        "is_sla_breached": true, "is_sla_at_risk": false},
       {"id": "case-2", "name": "  ", "status": "New", "priority": "Low",
        "account_name": null, "assigned_to": [],
        "is_sla_breached": false, "is_sla_at_risk": true}
     ]}
  ]
}
''';

const _pipelineBoard =
    '''
{
  "mode": "pipeline", "pipeline": {"id": "$_support", "name": "Support"},
  "total_cases": 1,
  "columns": [
    {"id": "st-triage", "name": "Triage", "order": 1, "color": "#3B82F6",
     "wip_limit": 3, "maps_to_status": null, "is_status_column": false,
     "case_count": 1, "cases": [
       {"id": "case-9", "name": "Pipeline ticket", "priority": "High",
        "assigned_to": [{"id": "p2", "user_details": {"name": "Ria", "email": "ria@example.com"}}]}
     ]},
    {"id": "st-done", "name": "Done", "order": 2, "color": "#22C55E",
     "wip_limit": null, "maps_to_status": "Closed", "is_status_column": false,
     "case_count": 0, "cases": []}
  ]
}
''';

class _FakeClient extends http.BaseClient {
  String pipelines = _pipelines;
  int writeStatus = 200;
  String writeBody = '{"error": false}';

  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  Iterable<http.BaseRequest> get gets => sent.where((r) => r.method == 'GET');

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    if (request.method == 'GET') {
      final String body;
      if (request.url.path.endsWith('/cases/kanban/')) {
        body = request.url.queryParameters.containsKey('pipeline_id')
            ? _pipelineBoard
            : _statusBoard;
      } else {
        body = pipelines;
      }
      return _reply(request, 200, body);
    }
    return _reply(request, writeStatus, writeBody);
  }

  http.StreamedResponse _reply(http.BaseRequest r, int status, String body) =>
      http.StreamedResponse(
        Stream.value(utf8.encode(body)),
        status,
        request: r,
      );
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

  Map<String, dynamic> lastPatchBody() {
    final patch = client.sent.lastWhere((r) => r.method == 'PATCH');
    return jsonDecode(client.bodies[client.sent.indexOf(patch)])
        as Map<String, dynamic>;
  }

  group('reading the board', () {
    test('status mode reads the kanban with no pipeline_id', () async {
      final data = await container.read(ticketBoardProvider.future);

      expect(data.active, isNull);
      expect(data.pipelines.map((p) => p.name), ['Support']);
      expect(client.gets.first.url.path, '/api/cases/pipelines/');
      expect(client.gets.last.url.path, '/api/cases/kanban/');
      expect(client.gets.last.url.queryParameters, isEmpty);
    });

    test('the Duplicate lane is dropped and the rest are in order', () async {
      final data = await container.read(ticketBoardProvider.future);

      expect(data.lanes.map((l) => l.id), ['New', 'Closed']);
      expect(
        data.lanes.any((l) => l.id == TicketStatus.duplicate.value),
        isFalse,
      );
    });

    test('choosing a pipeline sends its pipeline_id', () async {
      await container.read(ticketBoardProvider.future);

      await container.read(ticketBoardProvider.notifier).select(_support);

      final data = container.read(ticketBoardProvider).value!;
      expect(data.active?.id, _support);
      expect(client.gets.last.url.path, '/api/cases/kanban/');
      expect(client.gets.last.url.queryParameters, {'pipeline_id': _support});
      expect(data.lanes.map((l) => l.name), ['Triage', 'Done']);
      expect(data.lanes.first.wipLimit, 3);
      expect(data.lanes.first.isStatus, isFalse);
    });

    test('choosing "By status" again drops the pipeline_id', () async {
      await container.read(ticketBoardProvider.future);
      final notifier = container.read(ticketBoardProvider.notifier);
      await notifier.select(_support);

      await notifier.select(null);

      expect(container.read(ticketBoardProvider).value?.active, isNull);
      expect(client.gets.last.url.queryParameters, isEmpty);
    });

    test('cards carry name, account, assignee, priority and SLA', () async {
      final data = await container.read(ticketBoardProvider.future);
      final lane = data.lanes.first;
      final hot = lane.cards[0];
      final blank = lane.cards[1];

      expect(lane.count, 140);
      expect(lane.isTruncated, isTrue);
      expect(hot.name, 'Printer on fire');
      expect(hot.accountName, 'Acme');
      expect(hot.assignee, 'sam@example.com');
      expect(hot.priority, TicketPriority.urgent);
      expect(hot.slaBreached, isTrue);
      expect(blank.name, 'Untitled ticket');
      expect(blank.accountName, isEmpty);
      expect(blank.assignee, isEmpty);
      expect(blank.slaAtRisk, isTrue);
    });

    test('a card is offered every lane but its own', () async {
      final data = await container.read(ticketBoardProvider.future);

      expect(data.destinationsFrom(data.lanes.first).map((l) => l.id), [
        'Closed',
      ]);
    });
  });

  group('moving a ticket', () {
    test('status mode PATCHes {status} and refreshes', () async {
      final data = await container.read(ticketBoardProvider.future);
      final before = client.gets.length;

      final response = await container
          .read(ticketBoardProvider.notifier)
          .moveTicket(ticketId: 'case-1', target: data.lanes[1]);

      expect(response.success, isTrue);
      final patch = client.sent.lastWhere((r) => r.method == 'PATCH');
      expect(patch.url.path, '/api/cases/case-1/move/');
      expect(lastPatchBody(), {'status': 'Closed'});
      expect(client.gets.length, before + 2);
    });

    test('pipeline mode PATCHes {stage_id}', () async {
      await container.read(ticketBoardProvider.future);
      final notifier = container.read(ticketBoardProvider.notifier);
      await notifier.select(_support);
      final done = container.read(ticketBoardProvider).value!.lanes[1];

      final response = await notifier.moveTicket(
        ticketId: 'case-9',
        target: done,
      );

      expect(response.success, isTrue);
      expect(lastPatchBody(), {'stage_id': 'st-done'});
    });

    test('a full stage refuses with its own sentence, no label', () async {
      final data = await container.read(ticketBoardProvider.future);
      final before = client.gets.length;
      client.writeStatus = 400;
      client.writeBody =
          '{"error": "Stage \'Triage\' has reached its WIP limit of 3"}';

      final response = await container
          .read(ticketBoardProvider.notifier)
          .moveTicket(ticketId: 'case-1', target: data.lanes[1]);

      expect(response.success, isFalse);
      expect(response.message, "Stage 'Triage' has reached its WIP limit of 3");
      expect(client.gets.length, before);
    });

    test('the close gate refusal surfaces as the gate sentence', () async {
      final data = await container.read(ticketBoardProvider.future);
      client.writeStatus = 400;
      client.writeBody =
          '{"error": true, "errors": {"status": ["An approval is required '
          'before this case can be closed (rule: VIP)."]}}';

      final response = await container
          .read(ticketBoardProvider.notifier)
          .moveTicket(ticketId: 'case-1', target: data.lanes[1]);

      expect(response.success, isFalse);
      expect(
        response.message,
        'An approval is required before this case can be closed (rule: VIP).',
      );
    });

    test('a 403 keeps the server detail', () async {
      final data = await container.read(ticketBoardProvider.future);
      client.writeStatus = 403;
      client.writeBody =
          '{"detail": "You do not have permission to perform this action."}';

      final response = await container
          .read(ticketBoardProvider.notifier)
          .moveTicket(ticketId: 'case-1', target: data.lanes[1]);

      expect(response.success, isFalse);
      expect(response.statusCode, 403);
      expect(
        response.message,
        'You do not have permission to perform this action.',
      );
    });

    test('a 404 becomes a sentence saying the ticket cannot move', () async {
      final data = await container.read(ticketBoardProvider.future);
      client.writeStatus = 404;
      client.writeBody = '{"detail": "Not found."}';

      final response = await container
          .read(ticketBoardProvider.notifier)
          .moveTicket(ticketId: 'case-1', target: data.lanes[1]);

      expect(response.statusCode, 404);
      expect(response.message, ticketMoveNotAllowedMessage);
    });
  });
}
