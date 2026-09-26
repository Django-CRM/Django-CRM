import 'dart:convert';

import 'package:bottle_crm/data/models/lead.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/providers/leads_provider.dart';
import 'package:bottle_crm/providers/saved_views_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Answers each request with the next queued reply and keeps what was sent.
class _QueueClient extends http.BaseClient {
  final List<(int, String)> replies = [];
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    bodies.add(utf8.decode(await request.finalize().toBytes()));
    final (status, body) = replies.removeAt(0);
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

const _viewId = '99999999-8888-7777-6666-555555555555';

String _list(List<Map<String, Object>> views, {int limit = 25}) =>
    jsonEncode({'saved_views': views, 'limit': limit});

final _hot = {
  'id': _viewId,
  'module': 'leads',
  'name': 'Hot',
  'filters': {
    'rating': ['HOT'],
    'status': ['assigned', 'in process'],
  },
};

void main() {
  late _QueueClient client;
  late ProviderContainer container;

  setUp(() {
    client = _QueueClient();
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  /// The provider is autoDispose, so a listener stands in for the open sheet.
  Future<SavedViewsNotifier> open([
    SavedViewList list = SavedViewList.leads,
  ]) async {
    container.listen(savedViewsProvider(list), (_, _) {});
    await container.read(savedViewsProvider(list).future);
    return container.read(savedViewsProvider(list).notifier);
  }

  SavedViewsState stateOf([SavedViewList list = SavedViewList.leads]) =>
      container.read(savedViewsProvider(list)).value!;

  group('the API', () {
    test("reads one list's views, asking by the API's module name", () async {
      client.replies.add((200, _list([_hot])));
      await open(SavedViewList.tickets);

      final url = client.sent.single.url;
      expect(url.path, endsWith('/api/saved-views/'));
      expect(url.queryParameters['module'], 'cases');
      final view = stateOf(SavedViewList.tickets).views.single;
      expect(view.name, 'Hot');
      expect(view.filters['status'], ['assigned', 'in process']);
    });

    test(
      'saves module, name and filters, never an owner, then rereads',
      () async {
        client.replies
          ..add((200, _list([])))
          ..add((201, jsonEncode(_hot)))
          ..add((200, _list([_hot])));
        final notifier = await open();

        final error = await notifier.save('  Hot ', {
          'rating': ['HOT'],
        });

        expect(error, isNull);
        expect(client.sent[1].method, 'POST');
        expect(jsonDecode(client.bodies[1]), {
          'module': 'leads',
          'name': 'Hot',
          'filters': {
            'rating': ['HOT'],
          },
        });
        expect(stateOf().views.single.id, _viewId);
      },
    );

    test('renames and deletes by id', () async {
      client.replies
        ..add((200, _list([_hot])))
        ..add((200, jsonEncode(_hot)))
        ..add((200, _list([_hot])))
        ..add((204, ''))
        ..add((200, _list([])));
      final notifier = await open();

      expect(await notifier.rename(_viewId, 'Warm'), isNull);
      expect(client.sent[1].method, 'PATCH');
      expect(client.sent[1].url.path, endsWith('/api/saved-views/$_viewId/'));
      expect(jsonDecode(client.bodies[1]), {'name': 'Warm'});

      expect(await notifier.remove(_viewId), isNull);
      expect(client.sent[3].method, 'DELETE');
      expect(stateOf().views, isEmpty);
    });

    test("a refusal reads as the API's own sentence", () async {
      client.replies
        ..add((200, _list([_hot])))
        ..add((
          400,
          '{"name": ["You already have a view with this name for this list."]}',
        ));
      final notifier = await open();

      expect(
        await notifier.save('hot', const {}),
        'You already have a view with this name for this list.',
      );
    });

    test(
      "another person's id (a 404) is gone, and the list is reread",
      () async {
        client.replies
          ..add((200, _list([_hot])))
          ..add((404, '{"detail": "No such saved view."}'))
          ..add((200, _list([])));
        final notifier = await open();

        expect(await notifier.remove(_viewId), 'That view no longer exists.');
        expect(stateOf().views, isEmpty);
      },
    );

    test('the cap comes from the API', () async {
      client.replies.add((200, _list([_hot], limit: 1)));
      await open();
      expect(stateOf().full, isTrue);
    });
  });

  group('shapes', () {
    test('savedViewFilters keeps only the keys, as text, without blanks', () {
      expect(
        savedViewFilters(
          {
            'search': ' ada ',
            'status': ['New', '', 'Pending'],
            'sort': 'due_date',
            'tags': <String>[],
            'rating': null,
          },
          {'search', 'status', 'tags', 'rating'},
          multi: {'status', 'tags'},
        ),
        {
          'search': ['ada'],
          'status': ['New', 'Pending'],
        },
      );
    });

    test('a view counts the values a screen cannot apply, not the keys', () {
      final view = SavedView.fromJson(_hot);
      // rating is not on the screen (1); status is read singly (2 kept as 1).
      expect(view.dropped({'status'}, const {}), 2);
      expect(view.dropped({'status'}, {'status'}), 1);
      expect(view.dropped({'status', 'rating'}, {'status'}), 0);
    });

    test('savedViewFilters keeps one value of a key read singly', () {
      expect(
        savedViewFilters(
          {
            'status': ['New', 'Pending'],
            'priority': ['High', 'Low'],
          },
          {'status', 'priority'},
          multi: {'status'},
        ),
        {
          'status': ['New', 'Pending'],
          'priority': ['High'],
        },
      );
    });

    test('firstSentence reads a DRF body without its field name', () {
      expect(
        firstSentence({
          'filters': ["assigned_to: 'x' is not a valid id."],
        }),
        "assigned_to: 'x' is not a valid id.",
      );
      expect(firstSentence({}), isNull);
      expect(firstSentence(null), isNull);
    });
  });

  group('a view round-trips through each list', () {
    test('leads', () async {
      final leads = ProviderContainer(
        overrides: [leadsProvider.overrideWith(_Leads.new)],
      );
      addTearDown(leads.dispose);
      final notifier = leads.read(leadsProvider.notifier);
      const filters = LeadFilters(
        search: 'ada',
        statuses: {LeadStatus.assigned, LeadStatus.inProcess},
        source: LeadSource.email,
        rating: LeadRating.hot,
        assignedToId: 'u1',
        tagId: 't1',
        nextFollowUp: '2026-09-26',
      );
      await notifier.setFilters(filters);
      final back = LeadFilters.fromQuery(
        savedViewFilters(
          notifier.filterQuery,
          {
            'search',
            'status',
            'source',
            'rating',
            'assigned_to',
            'tags',
            'next_follow_up',
          },
          multi: {'status'},
        ),
        assignedToLabel: 'Me',
        tagLabel: 'vip',
      );
      expect(back.search, 'ada');
      expect(back.statuses, filters.statuses);
      expect(back.source, LeadSource.email);
      expect(back.rating, LeadRating.hot);
      expect(back.assignedToId, 'u1');
      expect(back.assignedToLabel, 'Me');
      expect(back.tagId, 't1');
      expect(back.tagLabel, 'vip');
      expect(back.nextFollowUp, '2026-09-26');
    });

    test('leads drop a value the list does not offer', () {
      final back = LeadFilters.fromQuery({
        'status': ['nope'],
        'rating': ['LUKEWARM'],
        'open': ['true'],
      });
      expect(back.isEmpty, isTrue);
    });

    test('deals read the web search and every range', () {
      final back = DealFilters.fromQuery({
        'search': ['Acme'],
        'stage': ['QUALIFIED'],
        'assigned_to': ['u1', 'u2'],
        'tags': ['t1'],
        'created_at__gte': ['2026-01-02'],
        'closed_on__lte': ['2026-03-04'],
        'amount__gte': ['10.5'],
        'amount__lte': ['lots'],
        'rotten': ['true'],
      });
      expect(back.search, 'Acme');
      expect(back.stage, 'QUALIFIED');
      expect(back.assignedToIds, ['u1', 'u2']);
      expect(back.tagIds, ['t1']);
      expect(back.createdFrom, DateTime(2026, 1, 2));
      expect(back.closingTo, DateTime(2026, 3, 4));
      expect(back.amountMin, 10.5);
      expect(back.amountMax, isNull);
      expect(back.rottenOnly, isTrue);
      expect(
        DealFilters.fromQuery({
          'name': ['Beta'],
        }).search,
        'Beta',
      );
    });

    test('tickets: one status is single, several are the chip list', () {
      final filters = TicketListFilters(
        search: 'login',
        statusList: const ['New', 'Assigned', 'Pending'],
        priority: 'High',
        accountId: 'a1',
        caseType: 'Incident',
        assigneeIds: const ['u1'],
        tagIds: const ['t1'],
        slaBreached: true,
        createdAfter: DateTime.utc(2026, 1, 2),
      );
      final back = TicketListFilters.fromQuery(
        savedViewFilters(
          ticketListQuery(filters),
          {
            'search',
            'status',
            'priority',
            'account',
            'case_type',
            'assigned_to',
            'tags',
            'sla_breached',
            'created_at__gte',
            'created_at__lte',
          },
          multi: {'status', 'assigned_to', 'tags'},
        ),
      );
      expect(ticketListQuery(back), ticketListQuery(filters));
      expect(
        TicketListFilters.fromQuery({
          'status': ['Closed'],
        }).status,
        'Closed',
      );
      expect(back.watchingOnly, isFalse);
    });

    test("tickets: the web's open-queue and All views mean the same here", () {
      // The web saves its default queue as the three open statuses...
      final open = TicketListFilters.fromQuery({
        'status': ['New', 'Assigned', 'Pending'],
      });
      expect(open.statusList, ['New', 'Assigned', 'Pending']);
      expect(ticketListQuery(open)['status'], ['New', 'Assigned', 'Pending']);
      // ...and its All tab as no status, which is every status here too.
      final all = TicketListFilters.fromQuery({
        'priority': ['High'],
      });
      expect(ticketListQuery(all).containsKey('status'), isFalse);
    });
  });
}

/// The real notifier, minus the network: `filterQuery` is what is under test.
class _Leads extends LeadsNotifier {
  @override
  Future<LeadsListData> build() async => const LeadsListData(hasMore: false);

  @override
  Future<void> refresh() async {}
}
