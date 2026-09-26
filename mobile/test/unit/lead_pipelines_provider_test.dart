import 'dart:convert';

import 'package:bottle_crm/data/models/lead_pipeline.dart';
import 'package:bottle_crm/providers/lead_pipelines_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Lead pipeline administration against the real request shapes.
///
/// What is pinned: every write goes to the URL and carries the body the
/// backend reads, a refusal comes back as the server's sentence (not
/// "Error: ..." and not raw JSON), a reorder sends every stage id in the new
/// order, and a refused write changes nothing on screen.
const _pipelines = '''
{"pipelines": [
  {"id": "p1", "name": "Inbound", "description": "Web and phone",
   "is_default": true, "is_active": true, "stage_count": 3, "lead_count": 7},
  {"id": "p2", "name": "Admissions", "description": null,
   "is_default": false, "is_active": true, "stage_count": 0, "lead_count": 0}
]}
''';

/// Stages deliberately out of order, and one still mapped to "converted".
const _detail = '''
{"id": "p1", "name": "Inbound", "description": "Web and phone",
 "is_default": true, "is_active": true, "stage_count": 3, "lead_count": 7,
 "stages": [
   {"id": "s3", "name": "Won", "order": 2, "color": "#22C55E",
    "stage_type": "won", "maps_to_status": "converted", "win_probability": 100,
    "wip_limit": null, "lead_count": 0},
   {"id": "s1", "name": "New", "order": 0, "color": "#3B82F6",
    "stage_type": "open", "maps_to_status": "assigned", "win_probability": 0,
    "wip_limit": null, "lead_count": 5},
   {"id": "s2", "name": "Qualified", "order": 1, "color": "#F59E0B",
    "stage_type": "open", "maps_to_status": null, "win_probability": 25,
    "wip_limit": 4, "lead_count": 2}
 ]}
''';

class _FakeClient extends http.BaseClient {
  /// Canned replies keyed by "METHOD /path". Anything not listed is a 200
  /// with an empty object.
  final Map<String, (int, String)> replies = {
    'GET /api/leads/pipelines/': (200, _pipelines),
    'GET /api/leads/pipelines/p1/': (200, _detail),
  };

  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  Iterable<http.BaseRequest> get writes => sent.where((r) => r.method != 'GET');
  int get gets => sent.where((r) => r.method == 'GET').length;

  Map<String, dynamic> lastWriteBody() =>
      jsonDecode(bodies[sent.indexOf(writes.last)]) as Map<String, dynamic>;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    final (status, body) =
        replies['${request.method} ${request.url.path}'] ?? (200, '{}');
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
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

  Future<List<LeadPipeline>> readList() {
    container.listen(leadPipelinesProvider, (_, _) {});
    return container.read(leadPipelinesProvider.future);
  }

  Future<LeadPipelineDetail> readDetail() {
    container.listen(leadPipelineDetailProvider('p1'), (_, _) {});
    return container.read(leadPipelineDetailProvider('p1').future);
  }

  LeadPipelinesNotifier list() =>
      container.read(leadPipelinesProvider.notifier);
  LeadPipelineDetailNotifier detail() =>
      container.read(leadPipelineDetailProvider('p1').notifier);

  group('the pipeline list', () {
    test('reads names, counts and the default flag', () async {
      final pipelines = await readList();

      expect(pipelines.map((p) => p.name), ['Inbound', 'Admissions']);
      expect(pipelines.first.isDefault, isTrue);
      expect(pipelines.first.stageCount, 3);
      expect(pipelines.first.leadCount, 7);
      expect(pipelines.first.description, 'Web and phone');
      // A null description is an empty one, not the string "null".
      expect(pipelines.last.description, '');
    });

    test('create posts the name and the default-stages choice', () async {
      await readList();
      client.replies['POST /api/leads/pipelines/'] = (201, '{"id": "p9"}');
      final before = client.gets;

      final error = await list().createPipeline(
        leadPipelinePayload(
          name: '  Partners ',
          description: '',
          createDefaultStages: false,
        ),
      );

      expect(error, isNull);
      expect(client.writes.last.method, 'POST');
      expect(client.writes.last.url.path, '/api/leads/pipelines/');
      expect(client.lastWriteBody(), {
        'name': 'Partners',
        'description': '',
        'create_default_stages': false,
      });
      expect(client.gets, before + 1);
    });

    test('rename PUTs to the pipeline without the create-only key', () async {
      await readList();

      final error = await list().updatePipeline(
        'p1',
        leadPipelinePayload(name: 'Inbound leads', description: 'All of them'),
      );

      expect(error, isNull);
      expect(client.writes.last.method, 'PUT');
      expect(client.writes.last.url.path, '/api/leads/pipelines/p1/');
      expect(client.lastWriteBody(), {
        'name': 'Inbound leads',
        'description': 'All of them',
      });
    });

    test('delete sends DELETE and reloads on a 204', () async {
      await readList();
      client.replies['DELETE /api/leads/pipelines/p2/'] = (204, '');
      final before = client.gets;

      final error = await list().deletePipeline('p2');

      expect(error, isNull);
      expect(client.writes.last.method, 'DELETE');
      expect(client.writes.last.url.path, '/api/leads/pipelines/p2/');
      expect(client.gets, before + 1);
    });

    test('a pipeline that holds leads is refused with the count', () async {
      await readList();
      client.replies['DELETE /api/leads/pipelines/p1/'] = (
        400,
        '{"error": "This pipeline still has 3 lead(s). Move them out of its '
            'stages first."}',
      );
      final before = client.gets;

      final error = await list().deletePipeline('p1');

      // Not "Error: This pipeline...", which is how ApiService words a body
      // it drops on a failed DELETE.
      expect(
        error,
        'This pipeline still has 3 lead(s). Move them out of its stages first.',
      );
      expect(client.gets, before);
      expect(container.read(leadPipelinesProvider).value, hasLength(2));
    });

    test(
      'a member is refused with a sentence, not "Permission denied"',
      () async {
        await readList();
        client.replies['POST /api/leads/pipelines/'] = (
          403,
          '{"error": "Only admins can create pipelines"}',
        );
        client.replies['DELETE /api/leads/pipelines/p1/'] = (
          403,
          '{"error": "Permission denied"}',
        );

        expect(
          await list().createPipeline(
            leadPipelinePayload(name: 'X', description: ''),
          ),
          leadPipelineAdminOnlyMessage,
        );
        expect(await list().deletePipeline('p1'), leadPipelineAdminOnlyMessage);
      },
    );
  });

  group('one pipeline', () {
    test('stages come back in their order', () async {
      final data = await readDetail();

      expect(data.pipeline.name, 'Inbound');
      expect(data.stages.map((s) => s.id), ['s1', 's2', 's3']);
      expect(data.stages[1].mapsToStatus, isNull);
      expect(data.stages[1].winProbability, 25);
      expect(data.stages[0].leadCount, 5);
    });

    test('a deleted pipeline reads as gone, not as an error code', () async {
      client.replies['GET /api/leads/pipelines/p1/'] = (
        404,
        '{"detail": "Not found."}',
      );
      // Riverpod retries a failed build by default, which would keep the
      // future pending; the screen shows the first error either way.
      final noRetry = ProviderContainer(retry: (_, _) => null);
      addTearDown(noRetry.dispose);
      noRetry.listen(leadPipelineDetailProvider('p1'), (_, _) {});

      await expectLater(
        noRetry.read(leadPipelineDetailProvider('p1').future),
        throwsA(
          predicate((e) => '$e'.contains('This pipeline no longer exists.')),
        ),
      );
    });

    test('a new stage is posted without an order, so it goes last', () async {
      await readDetail();
      client.replies['POST /api/leads/pipelines/p1/stages/'] = (201, '{}');

      final error = await detail().createStage(
        leadStagePayload(
          name: 'Demo booked',
          stageType: 'open',
          mapsToStatus: null,
          winProbability: 40,
        ),
      );

      expect(error, isNull);
      expect(client.writes.last.url.path, '/api/leads/pipelines/p1/stages/');
      final body = client.lastWriteBody();
      expect(body, {
        'name': 'Demo booked',
        'stage_type': 'open',
        'maps_to_status': null,
        'win_probability': 40,
      });
      expect(body.containsKey('order'), isFalse);
      // "None" is sent as an explicit null, not left out.
      expect(body.containsKey('maps_to_status'), isTrue);
    });

    test('an edit PUTs to the stage', () async {
      await readDetail();

      final error = await detail().updateStage(
        's2',
        leadStagePayload(
          name: 'Qualified',
          stageType: 'open',
          mapsToStatus: 'in process',
          winProbability: 30,
        ),
      );

      expect(error, isNull);
      expect(client.writes.last.method, 'PUT');
      expect(client.writes.last.url.path, '/api/leads/stages/s2/');
      expect(client.lastWriteBody()['maps_to_status'], 'in process');
    });

    test('a duplicate stage name comes back as the field sentence', () async {
      await readDetail();
      client.replies['PUT /api/leads/stages/s2/'] = (
        400,
        '{"error": true, "errors": {"name": '
            '["This pipeline already has a stage with that name."]}}',
      );

      final error = await detail().updateStage(
        's2',
        leadStagePayload(
          name: 'New',
          stageType: 'open',
          mapsToStatus: null,
          winProbability: 0,
        ),
      );

      expect(error, 'This pipeline already has a stage with that name.');
    });

    test('a stage that holds leads is refused with the count', () async {
      await readDetail();
      client.replies['DELETE /api/leads/stages/s1/'] = (
        400,
        '{"error": "This stage still has 5 lead(s). Move them to another '
            'stage first."}',
      );

      final error = await detail().deleteStage('s1');

      expect(
        error,
        'This stage still has 5 lead(s). Move them to another stage first.',
      );
      expect(client.writes.last.url.path, '/api/leads/stages/s1/');
    });

    test('an empty stage is deleted and the pipeline reloaded', () async {
      await readDetail();
      client.replies['DELETE /api/leads/stages/s3/'] = (204, '');
      final before = client.gets;

      final error = await detail().deleteStage('s3');

      expect(error, isNull);
      expect(client.gets, greaterThan(before));
    });

    test('a reorder sends every stage id once, in the new order', () async {
      await readDetail();

      final error = await detail().moveStage(2, 0);

      expect(error, isNull);
      expect(client.writes.last.method, 'POST');
      expect(
        client.writes.last.url.path,
        '/api/leads/pipelines/p1/stages/reorder/',
      );
      expect(client.lastWriteBody(), {
        'stage_ids': ['s3', 's1', 's2'],
      });
      expect(
        container
            .read(leadPipelineDetailProvider('p1'))
            .value
            ?.stages
            .map((s) => s.id),
        ['s3', 's1', 's2'],
      );
    });

    test('a refused reorder reloads the server order and says why', () async {
      await readDetail();
      client.replies['POST /api/leads/pipelines/p1/stages/reorder/'] = (
        400,
        '{"error": "Send every stage of this pipeline exactly once, in the '
            'new order."}',
      );

      final error = await detail().moveStage(0, 1);

      expect(error, startsWith('Send every stage of this pipeline'));
      expect(
        container
            .read(leadPipelineDetailProvider('p1'))
            .value
            ?.stages
            .map((s) => s.id),
        ['s1', 's2', 's3'],
      );
    });

    test('a move past either end sends nothing', () async {
      await readDetail();

      expect(await detail().moveStage(0, -1), isNull);
      expect(await detail().moveStage(2, 3), isNull);
      expect(client.writes, isEmpty);
    });

    test('a member reordering is refused with a sentence', () async {
      await readDetail();
      client.replies['POST /api/leads/pipelines/p1/stages/reorder/'] = (
        403,
        '{"error": "Permission denied"}',
      );

      expect(await detail().moveStage(0, 1), leadPipelineAdminOnlyMessage);
    });
  });

  group('the stage pickers and checks', () {
    test('a stored status outside the list stays selectable', () {
      final options = leadStageStatusOptions('converted');

      expect(options.keys, [
        null,
        'assigned',
        'in process',
        'recycled',
        'closed',
        'converted',
      ]);
      expect(options['converted'], 'Converted');
      // A new stage is never offered "converted".
      expect(leadStageStatusOptions(null).containsKey('converted'), isFalse);
      expect(leadStageTypeOptions('open').keys, ['open', 'won', 'lost']);
    });

    test('win probability must be a whole number from 0 to 100', () {
      String? check(String p) =>
          leadStageDraftProblem(name: 'Stage', winProbability: p);

      expect(check('0'), isNull);
      expect(check('100'), isNull);
      expect(check('101'), isNotNull);
      expect(check('-1'), isNotNull);
      expect(check(''), isNotNull);
      expect(check('2.5'), isNotNull);
    });

    test('names are required and capped at the model length', () {
      expect(leadStageDraftProblem(name: '  ', winProbability: '0'), isNotNull);
      expect(
        leadStageDraftProblem(name: 'x' * 101, winProbability: '0'),
        isNotNull,
      );
      expect(leadPipelineNameProblem(''), isNotNull);
      expect(leadPipelineNameProblem('x' * 256), isNotNull);
      expect(leadPipelineNameProblem('Partners'), isNull);
    });
  });
}
