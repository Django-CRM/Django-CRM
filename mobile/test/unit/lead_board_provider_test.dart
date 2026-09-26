import 'dart:convert';

import 'package:bottle_crm/data/models/lead_board.dart';
import 'package:bottle_crm/providers/lead_board_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart' show Color;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The lead pipeline board, which exists because a vertical pack creates a
/// lead pipeline ("Admissions") that no screen used to show.
///
/// Three things are pinned here. The board takes two calls and the second is
/// keyed on the pipeline the first resolved. "No stage" comes first, and a
/// move back into it sends an explicit `stage_id: null`. And a refused move
/// leaves the board as it was and hands back the server's own sentence, or a
/// readable one for a 404.
const _admissions = 'pipe-admissions';
const _inbound = 'pipe-inbound';

const _pipelines =
    '''
{"pipelines": [
  {"id": "$_inbound", "name": "Inbound", "is_default": true},
  {"id": "$_admissions", "name": "Admissions", "is_default": false}
]}
''';

/// Stages deliberately out of order.
const _board = '''
{
  "mode": "pipeline",
  "columns": [
    {"id": "st-admitted", "name": "Admitted", "order": 6, "color": "#10B981",
     "wip_limit": null, "lead_count": 0, "leads": []},
    {"id": "st-new", "name": "New enquiry", "order": 1, "color": "not-a-colour",
     "wip_limit": 3, "lead_count": 140, "leads": [
       {"id": "lead-1", "full_name": "Asha Rao", "company_name": "Rao & Co",
        "rating": "HOT", "is_follow_up_overdue": true,
        "assigned_to": [{"id": "p1", "user_details": {"name": "", "email": "sam@example.com"}}]}
     ]}
  ],
  "unstaged": {"lead_count": 1, "leads": [
    {"id": "lead-2", "full_name": " ", "title": "Walk-in enquiry", "assigned_to": []}
  ]}
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
      final body = request.url.path.endsWith('/kanban/') ? _board : pipelines;
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

  group('reading the board', () {
    test('with no pipelines it stops at one call and says so', () async {
      client.pipelines = '{"pipelines": []}';

      final data = await container.read(leadBoardProvider.future);

      expect(data.hasNoPipelines, isTrue);
      expect(data.lanes, isEmpty);
      expect(client.gets, hasLength(1));
    });

    test(
      'the lanes come from a second call keyed on the first pipeline',
      () async {
        final data = await container.read(leadBoardProvider.future);

        expect(data.active?.id, _inbound);
        expect(client.gets, hasLength(2));
        expect(client.gets.last.url.path, '/api/leads/kanban/');
        expect(client.gets.last.url.queryParameters, {'pipeline_id': _inbound});
      },
    );

    test('choosing a pipeline refetches the board for that one', () async {
      await container.read(leadBoardProvider.future);

      await container.read(leadBoardProvider.notifier).select(_admissions);

      expect(container.read(leadBoardProvider).value?.active?.id, _admissions);
      expect(client.gets.last.url.queryParameters, {
        'pipeline_id': _admissions,
      });
    });

    test('"No stage" comes first, then the stages in their order', () async {
      final data = await container.read(leadBoardProvider.future);

      expect(data.lanes.map((l) => l.name), [
        'No stage',
        'New enquiry',
        'Admitted',
      ]);
      expect(data.lanes.first.isUnstaged, isTrue);
      expect(data.stages.map((l) => l.id), ['st-new', 'st-admitted']);
    });

    test('a lane capped below its true count says it is truncated', () async {
      final data = await container.read(leadBoardProvider.future);
      final lane = data.lanes[1];

      expect(lane.count, 140);
      expect(lane.cards, hasLength(1));
      expect(lane.isTruncated, isTrue);
      expect(lane.wipLimit, 3);
      expect(data.lanes[2].isTruncated, isFalse);
    });

    test('cards carry name, company, rating, owner and overdue', () async {
      final data = await container.read(leadBoardProvider.future);
      final card = data.lanes[1].cards.single;
      final unnamed = data.lanes[0].cards.single;

      expect(card.name, 'Asha Rao');
      expect(card.company, 'Rao & Co');
      expect(card.rating, 'HOT');
      expect(card.owner, 'sam@example.com');
      expect(card.followUpOverdue, isTrue);
      // A blank full name falls back to the lead's title.
      expect(unnamed.name, 'Walk-in enquiry');
      expect(unnamed.owner, isEmpty);
    });

    test('a colour that is not #RRGGBB is drawn grey', () async {
      final data = await container.read(leadBoardProvider.future);

      expect(data.lanes[1].color, const Color(0xFF6B7280));
      expect(data.lanes[2].color, const Color(0xFF10B981));
    });
  });

  group('moving a lead', () {
    test('it PATCHes the move endpoint with the stage and refreshes', () async {
      await container.read(leadBoardProvider.future);
      final before = client.gets.length;

      final response = await container
          .read(leadBoardProvider.notifier)
          .moveLead(leadId: 'lead-2', stageId: 'st-new');

      expect(response.success, isTrue);
      final patch = client.sent.lastWhere((r) => r.method == 'PATCH');
      expect(patch.url.path, '/api/leads/lead-2/move/');
      expect(jsonDecode(client.bodies[client.sent.indexOf(patch)]), {
        'stage_id': 'st-new',
      });
      expect(client.gets.length, before + 2);
    });

    test(
      'a refusal leaves the board alone and carries the server sentence',
      () async {
        await container.read(leadBoardProvider.future);
        final before = client.gets.length;
        client.writeStatus = 400;
        client.writeBody =
            '{"error": true, "errors": "This lead is in the Inbound pipeline. '
            'Move it to one of that pipeline\'s stages."}';

        final response = await container
            .read(leadBoardProvider.notifier)
            .moveLead(leadId: 'lead-1', stageId: 'st-other');

        expect(response.success, isFalse);
        expect(response.message, startsWith('This lead is in the Inbound'));
        expect(client.gets.length, before);
      },
    );

    test('"No stage" sends an explicit null, not an empty body', () async {
      await container.read(leadBoardProvider.future);
      final before = client.gets.length;

      final response = await container
          .read(leadBoardProvider.notifier)
          .moveLead(leadId: 'lead-1', stageId: null);

      expect(response.success, isTrue);
      final patch = client.sent.lastWhere((r) => r.method == 'PATCH');
      expect(patch.url.path, '/api/leads/lead-1/move/');
      final body = client.bodies[client.sent.indexOf(patch)];
      // The key must be present: the serializer refuses a body with neither
      // `stage_id` nor `status`, so omitting it is a 400, not an unstage.
      expect(body, '{"stage_id":null}');
      expect(jsonDecode(body), containsPair('stage_id', null));
      expect(client.gets.length, before + 2);
    });

    test('every lane but its own is a destination for a card', () async {
      final data = await container.read(leadBoardProvider.future);
      final unstaged = data.lanes[0];
      final newEnquiry = data.lanes[1];

      expect(data.destinationsFrom(unstaged).map((l) => l.name), [
        'New enquiry',
        'Admitted',
      ]);
      expect(data.destinationsFrom(newEnquiry).map((l) => l.name), [
        'No stage',
        'Admitted',
      ]);
      expect(unstaged.moveStageId, isNull);
      expect(newEnquiry.moveStageId, 'st-new');
    });

    test('a 404 becomes a sentence saying the lead cannot be moved', () async {
      await container.read(leadBoardProvider.future);
      final before = client.gets.length;
      client.writeStatus = 404;
      client.writeBody = '{"detail": "Not found."}';

      final response = await container
          .read(leadBoardProvider.notifier)
          .moveLead(leadId: 'lead-1', stageId: null);

      expect(response.success, isFalse);
      expect(response.statusCode, 404);
      expect(response.message, leadMoveNotAllowedMessage);
      expect(client.gets.length, before);
    });

    test('a 403 is a refusal too, not a crash', () async {
      await container.read(leadBoardProvider.future);
      client.writeStatus = 403;
      client.writeBody = '{"error": true, "errors": "Permission denied"}';

      final response = await container
          .read(leadBoardProvider.notifier)
          .moveLead(leadId: 'lead-1', stageId: 'st-admitted');

      expect(response.success, isFalse);
      expect(response.message, 'Permission denied');
    });
  });

  group('the stage on lead detail', () {
    test('names the stage and its pipeline', () {
      final stage = LeadStageRef.fromJson({
        'id': 'st-new',
        'name': 'New enquiry',
        'color': '#3B82F6',
        'stage_type': 'open',
        'pipeline': {'id': _admissions, 'name': 'Admissions'},
      });

      expect(stage?.name, 'New enquiry');
      expect(stage?.pipelineName, 'Admissions');
      expect(stage?.color, const Color(0xFF3B82F6));
    });

    test('is null when the lead is in no pipeline', () {
      expect(LeadStageRef.fromJson(null), isNull);
    });
  });
}
