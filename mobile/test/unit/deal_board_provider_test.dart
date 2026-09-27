import 'dart:convert';

import 'package:bottle_crm/data/models/deal_board.dart';
import 'package:bottle_crm/data/models/deal_pipeline.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The mobile deal board reads `GET /opportunities/kanban/`, as the web board
/// does, instead of every page of the deal list (PARITY B7).
///
/// Pinned here: the board asks with the list's pipeline and filters; a card
/// moves only on an explicit `can_move: true`; a column capped below its true
/// count says so; a move goes through the move endpoint and changes column
/// at once, and a refused one leaves the board alone.
const _pipelines = '''
{"pipelines": [{"id": "p1", "name": "Sales", "is_default": true, "stages": [
  {"id": "s1", "code": "PROSPECTING", "label": "Prospecting", "order": 0, "kind": "open"},
  {"id": "s2", "code": "DEMO", "label": "Demo", "order": 1, "kind": "open"},
  {"id": "s3", "code": "SIGNED", "label": "Signed", "order": 2, "kind": "won"},
  {"id": "s4", "code": "LOST", "label": "Lost", "order": 3, "kind": "lost"}
]}]}
''';

const _board = '''
{
  "mode": "status",
  "pipeline": {"id": "p1", "name": "Sales", "is_default": true},
  "columns": [
    {"id": "PROSPECTING", "name": "Prospecting", "kind": "open", "item_count": 140,
     "items": [
       {"id": "d1", "name": "Acme", "stage": "PROSPECTING", "stage_kind": "open",
        "amount": "1200.00", "currency": "EUR", "probability": 10,
        "account": {"id": "a1", "name": "Acme Ltd"},
        "tags": [{"id": "t1", "name": "Hot"}],
        "line_items": [{"id": "l1", "name": "Seat", "quantity": "2.00",
                        "unit_price": "600.00", "discount_type": "", "discount_value": "0"}],
        "aging_status": "red", "days_in_stage": 40,
        "next_activity": {"id": "k1", "title": "Call back", "due_date": "2026-10-01"},
        "can_move": true},
       {"id": "d2", "name": "Globex", "stage": "PROSPECTING", "stage_kind": "open",
        "amount": "50.00", "currency": "USD", "probability": 10, "can_move": false},
       {"id": "d3", "name": "Initech", "stage": "PROSPECTING", "stage_kind": "open",
        "amount": "70.00", "currency": "USD", "probability": 10}
     ]},
    {"id": "DEMO", "name": "Demo", "kind": "open", "item_count": 0, "items": []},
    {"id": "SIGNED", "name": "Signed", "kind": "won", "item_count": 1, "items": [
       {"id": "d4", "name": "Won one", "stage": "SIGNED", "stage_kind": "won",
        "amount": "9000.00", "currency": "USD", "probability": 100, "can_move": true}
    ]},
    {"id": "LOST", "name": "Lost", "kind": "lost", "item_count": 0, "items": []}
  ],
  "total_items": 141
}
''';

class _FakeClient extends http.BaseClient {
  int moveStatus = 200;
  String moveBody = '{"error": false}';
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  Iterable<http.BaseRequest> get boardReads =>
      sent.where((r) => r.url.path == '/api/opportunities/kanban/');

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    final path = request.url.path;
    if (request.method == 'PATCH') return _reply(request, moveStatus, moveBody);
    if (path.endsWith('/pipelines/')) return _reply(request, 200, _pipelines);
    if (path.endsWith('/kanban/')) return _reply(request, 200, _board);
    return _reply(
      request,
      200,
      '{"opportunities": [], "opportunities_count": 0}',
    );
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

  DealPipelineStage stage(String code) => DealPipeline.fromJson(
    (jsonDecode(_pipelines)['pipelines'] as List).first as Map<String, dynamic>,
  ).stageByCode(code)!;

  group('reading', () {
    test(
      'asks the kanban endpoint with the list\'s pipeline and filters',
      () async {
        final deals = container.read(dealsProvider.notifier);
        await container.read(dealsProvider.future);
        await deals.setFilters(
          deals.filters.copyWith(
            search: 'acme',
            amountMin: 100.0,
            rottenOnly: true,
            assignedToIds: ['u1', 'u2'],
          ),
        );
        await container.read(dealBoardProvider.future);

        final read = client.boardReads.last.url;
        expect(read.queryParameters['pipeline'], 'p1');
        expect(read.queryParameters['name'], 'acme');
        expect(read.queryParameters['amount__gte'], '100.0');
        expect(read.queryParameters['rotten'], 'true');
        expect(read.queryParametersAll['assigned_to'], ['u1', 'u2']);
        // Never paged: the board is one call however many deals there are.
        expect(read.queryParameters.containsKey('offset'), isFalse);
      },
    );

    test('a filter change reads the board again', () async {
      final deals = container.read(dealsProvider.notifier);
      await container.read(dealBoardProvider.future);
      final before = client.boardReads.length;

      await deals.setFilters(deals.filters.copyWith(search: 'globex'));
      await container.read(dealBoardProvider.future);

      expect(client.boardReads.length, before + 1);
      expect(client.boardReads.last.url.queryParameters['name'], 'globex');
    });

    test('only an explicit can_move true lets a card move', () async {
      final board = await container.read(dealBoardProvider.future);
      final cards = board.column('PROSPECTING').cards;

      expect(cards.map((c) => c.canMove), [true, false, false]);
      expect(board.canMove('d1'), isTrue);
      expect(board.canMove('d2'), isFalse);
      expect(board.canMove('d3'), isFalse);
      expect(board.canMove('nope'), isFalse);
    });

    test('a column capped below its true count is truncated', () async {
      final board = await container.read(dealBoardProvider.future);

      expect(board.column('PROSPECTING').itemCount, 140);
      expect(board.column('PROSPECTING').isTruncated, isTrue);
      expect(board.column('DEMO').isTruncated, isFalse);
      // A stage the board did not send is an empty column, not an error.
      expect(board.column('NEW').cards, isEmpty);
    });

    test('cards keep what the deal card shows', () async {
      final board = await container.read(dealBoardProvider.future);
      final deal = board.column('PROSPECTING').cards.first.deal;

      expect(deal.title, 'Acme');
      expect(deal.companyName, 'Acme Ltd');
      expect(deal.value, 1200);
      expect(deal.currency.value, 'EUR');
      expect(deal.labels, ['Hot']);
      expect(deal.products, hasLength(1));
      expect(deal.agingStatus, 'red');
      expect(deal.nextActivityKnown, isTrue);
      expect(deal.nextActivity?.title, 'Call back');
    });

    test('the summary counts open columns by their true size', () async {
      await container.read(dealBoardProvider.future);
      final figures = container.read(dealBoardSummaryProvider);

      expect(figures.active, 140);
      // Capped, so the money is a floor and says so.
      expect(figures.summary.loadedSubset, isTrue);
      // The won deal is not in the pipeline figure.
      expect(figures.summary.buckets.map((b) => b.currency.value).toSet(), {
        'EUR',
        'USD',
      });
    });
  });

  group('moving', () {
    test(
      'a move patches the move endpoint and changes column at once',
      () async {
        final board = await container.read(dealBoardProvider.future);
        final deal = board.column('PROSPECTING').cards.first.deal;

        final result = await container
            .read(dealBoardProvider.notifier)
            .moveDeal(deal, stage('DEMO'));

        expect(result.success, isTrue);
        final patch = client.sent.lastWhere((r) => r.method == 'PATCH');
        expect(patch.url.path, '/api/opportunities/d1/move/');
        expect(jsonDecode(client.bodies.last), {'column_id': 'DEMO'});
        final after = container.read(dealBoardProvider).value!;
        expect(after.column('PROSPECTING').itemCount, 139);
        expect(after.column('DEMO').cards.single.deal.id, 'd1');
        expect(after.column('DEMO').cards.single.canMove, isTrue);
        expect(after.column('DEMO').itemCount, 1);
      },
    );

    test('a refused move leaves the board as it was', () async {
      client.moveStatus = 404;
      client.moveBody = '{"detail": "Not found."}';
      final board = await container.read(dealBoardProvider.future);
      final deal = board.column('PROSPECTING').cards.first.deal;

      final result = await container
          .read(dealBoardProvider.notifier)
          .moveDeal(deal, stage('DEMO'));

      expect(result.success, isFalse);
      final after = container.read(dealBoardProvider).value!;
      expect(after.column('PROSPECTING').itemCount, 140);
      expect(after.column('DEMO').cards, isEmpty);
    });
  });

  test('a card from a payload without can_move cannot move', () {
    final card = DealBoardCard.fromJson({'id': 'x', 'name': 'x'});
    expect(card.canMove, isFalse);
  });
}
