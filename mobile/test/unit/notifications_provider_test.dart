import 'dart:convert';

import 'package:bottle_crm/data/models/app_notification.dart';
import 'package:bottle_crm/providers/notifications_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The notification feed, module 14.
///
/// Three things are worth pinning.
///
/// **The stored link is data, not a destination.** `Notification.link` is a
/// column, and a column can hold anything that was ever written to it. It is
/// matched against the two ticket shapes and otherwise ignored, so no row can
/// send the app somewhere of its own choosing. Rows written before the
/// producer was fixed carry `/cases/<id>`, which no client has ever served,
/// which is why both spellings resolve.
///
/// **The badge is the server's count, not the page's.** `unread_count` is
/// computed over the whole feed while `results` is capped at the limit, so
/// counting rows would under-report exactly when there is most to report.
///
/// **Optimism has to be reversible.** Marking read clears the dot before the
/// server answers, so a refusal has to put it back or the screen settles on a
/// state the server never accepted.
class _FakeClient extends http.BaseClient {
  _FakeClient({this.body = '{}'});

  int status = 200;
  String body;
  final List<http.BaseRequest> sent = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    await request.finalize().toBytes();
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

/// Two unread and one read, with `unread_count` deliberately larger than the
/// unread rows present: the feed is capped, the count is not.
const _feed = '''
{
  "count": 3,
  "unread_count": 7,
  "results": [
    {
      "id": "n-mention",
      "verb": "case.mentioned",
      "actor": {"id": "u-1", "user_details": {"email": "ada@example.com", "profile_pic": null}},
      "entity_type": "Case",
      "entity_id": "case-9",
      "entity_name": "Printer on fire",
      "data": {"comment_excerpt": "can you take a look?"},
      "link": "/tickets/case-9",
      "read_at": null,
      "created_at": "2026-08-07T09:00:00Z"
    },
    {
      "id": "n-legacy",
      "verb": "case.commented",
      "actor": null,
      "entity_type": "Case",
      "entity_id": "case-4",
      "entity_name": "Old ticket",
      "data": {},
      "link": "/cases/case-4",
      "read_at": null,
      "created_at": "2026-08-06T09:00:00Z"
    },
    {
      "id": "n-read",
      "verb": "case.sla_breached",
      "actor": {"id": "u-2", "user_details": {"email": "grace@example.com", "profile_pic": null}},
      "entity_type": "Case",
      "entity_id": "case-1",
      "entity_name": "Handled",
      "data": {},
      "link": "/tickets/case-1",
      "read_at": "2026-08-06T10:00:00Z",
      "created_at": "2026-08-05T09:00:00Z"
    }
  ]
}
''';

void main() {
  late ProviderContainer container;
  late _FakeClient client;

  setUp(() {
    client = _FakeClient(body: _feed);
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  group('the link is parsed, never followed', () {
    test('a current /tickets/ link resolves', () {
      expect(_withLink('/tickets/case-9').ticketId, 'case-9');
    });

    test('product support resolves without becoming a CRM ticket', () {
      final notification = _withLink('/help/support-9');
      expect(notification.ticketId, isNull);
      expect(notification.destinationPath, '/help/support-9');
    });

    test('a legacy /support/ link resolves to the renamed help page', () {
      // Written before the page was renamed from support to help.
      final notification = _withLink('/support/support-9');
      expect(notification.ticketId, isNull);
      expect(notification.destinationPath, '/help/support-9');
    });

    test('a legacy /cases/ link resolves to the same ticket', () {
      // Written before the producer was fixed. No client serves `/cases`, so
      // leaving these unresolved would mean every old row opens nothing.
      expect(_withLink('/cases/case-9').ticketId, 'case-9');
    });

    test('a trailing slash is accepted', () {
      expect(_withLink('/tickets/case-9/').ticketId, 'case-9');
    });

    test('an absolute URL does not resolve', () {
      expect(_withLink('https://evil.example/tickets/case-9').ticketId, isNull);
    });

    test('a javascript: URL does not resolve', () {
      expect(_withLink('javascript:alert(1)').ticketId, isNull);
    });

    test('a deeper path does not resolve', () {
      // `/tickets/case-9/edit` is a different screen with different rules, and
      // a notification is not the thing that decides you may edit.
      expect(_withLink('/tickets/case-9/edit').ticketId, isNull);
    });

    test('a missing link does not resolve', () {
      expect(_withLink(null).ticketId, isNull);
    });
  });

  group('reading the feed', () {
    test('the badge is the server\'s count, not the rows fetched', () async {
      final feed = await container.read(notificationsProvider.future);

      expect(feed.items, hasLength(3));
      expect(feed.unread, hasLength(2));
      // 7, not 2. The feed is capped at the limit; the count is not.
      expect(feed.unreadCount, 7);
      expect(container.read(unreadNotificationCountProvider), 7);
    });

    test(
      'a notification with no actor keeps a null name for the system copy',
      () async {
        final feed = await container.read(notificationsProvider.future);

        expect(feed.items[0].actorName, 'ada@example.com');
        expect(feed.items[1].actorName, isNull);
      },
    );

    test('the comment excerpt is carried through as text', () async {
      final feed = await container.read(notificationsProvider.future);

      expect(feed.items[0].commentExcerpt, 'can you take a look?');
      expect(feed.items[1].commentExcerpt, isNull);
    });

    test(
      'rows with the dead prefix are counted once, not flagged per row',
      () async {
        final feed = await container.read(notificationsProvider.future);

        expect(feed.legacyLinkCount, 1);
      },
    );

    test('the badge is zero while nothing has loaded', () {
      expect(container.read(unreadNotificationCountProvider), 0);
    });

    test('a failure is a failure, not an empty feed', () async {
      final noRetry = ProviderContainer(retry: (_, _) => null);
      addTearDown(noRetry.dispose);
      client.status = 500;
      client.body = '{}';

      await expectLater(
        noRetry.read(notificationsProvider.future),
        throwsA(isA<Exception>()),
      );
    });
  });

  group('verbs', () {
    test('the two the backend produces get their own copy', () async {
      final feed = await container.read(notificationsProvider.future);

      expect(feed.items[0].verbPhrase, 'mentioned you on');
      expect(feed.items[0].isKnownVerb, isTrue);
      expect(feed.items[1].verbPhrase, 'commented on');
    });

    test(
      'one nothing dispatches reads as a sentence, not an identifier',
      () async {
        final feed = await container.read(notificationsProvider.future);

        // `case.sla_breached` has no producer. It renders rather than being
        // dropped, because a new producer ships before its copy does.
        expect(feed.items[2].verbPhrase, 'sla breached');
        expect(feed.items[2].isKnownVerb, isFalse);
      },
    );

    test('only verbs with active producers are declared', () {
      expect(producedVerbs, [
        'case.mentioned',
        'case.commented',
        'support.replied',
        'support.status_changed',
        'automation.message',
        'automation.failed',
        'automation.paused',
        'automation.limit',
        'project.budget',
        'estimate.accepted',
        'estimate.declined',
        'project.retainer_invoice',
        'lead.assigned',
        'lead.created',
        'lead.enquired',
        'lead.waiting',
      ]);
    });

    test('support updates are attributed to BottleCRM Support', () {
      final notification = AppNotification(
        id: 'support-1',
        verb: 'support.replied',
        createdAt: DateTime.utc(2026, 8, 8),
      );

      expect(notification.displayActorName, 'BottleCRM Support');
    });
  });

  group('marking read', () {
    test(
      'the dot clears before the server answers, and the badge drops',
      () async {
        await container.read(notificationsProvider.future);

        await container
            .read(notificationsProvider.notifier)
            .markRead('n-mention');

        final feed = container.read(notificationsProvider).value!;
        expect(feed.items[0].isUnread, isFalse);
        expect(feed.unreadCount, 6);
        final post = client.sent.firstWhere((r) => r.method == 'POST');
        expect(post.url.path, endsWith('/notifications/n-mention/read/'));
      },
    );

    test('a refusal puts the dot back', () async {
      await container.read(notificationsProvider.future);
      client.status = 404;
      client.body = '{"detail": "Not found."}';

      final response = await container
          .read(notificationsProvider.notifier)
          .markRead('n-mention');

      expect(response.success, isFalse);
      final feed = container.read(notificationsProvider).value!;
      expect(feed.items[0].isUnread, isTrue);
      expect(feed.unreadCount, 7);
    });

    test('marking an already-read row sends nothing', () async {
      await container.read(notificationsProvider.future);
      final before = client.sent.length;

      final response = await container
          .read(notificationsProvider.notifier)
          .markRead('n-read');

      expect(response.success, isTrue);
      expect(client.sent, hasLength(before));
    });

    test('a repeat does not drive the badge below what it counted', () async {
      await container.read(notificationsProvider.future);

      await container
          .read(notificationsProvider.notifier)
          .markRead('n-mention');
      await container
          .read(notificationsProvider.notifier)
          .markRead('n-mention');

      // 6, not 5: the second call found nothing left to clear. Counting the
      // ids rather than the rows actually cleared is how a badge goes negative.
      expect(container.read(notificationsProvider).value!.unreadCount, 6);
    });

    test('an id not in this feed sends nothing', () async {
      await container.read(notificationsProvider.future);
      final before = client.sent.length;

      await container
          .read(notificationsProvider.notifier)
          .markRead('someone-elses-id');

      expect(client.sent, hasLength(before));
    });
  });

  group('marking everything read', () {
    test('every unread row clears and the badge follows', () async {
      await container.read(notificationsProvider.future);

      await container.read(notificationsProvider.notifier).markAllRead();

      final feed = container.read(notificationsProvider).value!;
      expect(feed.unread, isEmpty);
      // Two rows cleared out of a counted seven. The other five are beyond the
      // page, and the server marks those too; the number is corrected on the
      // next load rather than guessed at here.
      expect(feed.unreadCount, 5);
      final post = client.sent.firstWhere((r) => r.method == 'POST');
      expect(post.url.path, endsWith('/notifications/read-all/'));
    });

    test('a refusal restores every dot', () async {
      await container.read(notificationsProvider.future);
      client.status = 500;
      client.body = '{}';

      final response = await container
          .read(notificationsProvider.notifier)
          .markAllRead();

      expect(response.success, isFalse);
      final feed = container.read(notificationsProvider).value!;
      expect(feed.unread, hasLength(2));
      expect(feed.unreadCount, 7);
    });

    test('with nothing unread it sends nothing', () async {
      client.body = '{"count": 0, "unread_count": 0, "results": []}';
      await container.read(notificationsProvider.future);
      final before = client.sent.length;

      final response = await container
          .read(notificationsProvider.notifier)
          .markAllRead();

      expect(response.success, isTrue);
      expect(client.sent, hasLength(before));
    });
  });

  _automationTests();
}

AppNotification _withLink(String? link) => AppNotification.fromJson({
  'id': 'n-1',
  'verb': 'case.mentioned',
  'link': link,
  'created_at': '2026-08-07T09:00:00Z',
});

const _id = '3f2b8c1e-7a4d-4e2b-9c1a-5d6e7f8a9b0c';

AppNotification _automation(
  String verb,
  Object? data, {
  String? link,
  String? actor,
}) => AppNotification.fromJson({
  'id': 'n-auto',
  'verb': verb,
  'actor': actor == null
      ? null
      : {
          'id': 'p-1',
          'user_details': {'email': actor, 'profile_pic': null},
        },
  'data': data,
  'link': link,
  'created_at': '2026-10-22T09:00:00Z',
});

void _automationTests() {
  group('automation notifications', () {
    test('a message reads as sent, with its rule as the source line', () {
      final n = _automation('automation.message', {
        'message': 'Kick off onboarding for Harbour Lane',
        'rule': 'Deal won: onboarding',
      }, actor: 'asha.rao@example.com');

      expect(n.message, 'Kick off onboarding for Harbour Lane');
      expect(n.ruleName, 'Deal won: onboarding');
      expect(n.automationText(), 'Kick off onboarding for Harbour Lane');
      expect(n.automationSource, 'From the rule "Deal won: onboarding"');
      expect(n.displayActorName, 'asha.rao@example.com');
    });

    test('the actor is read as the feed sends it', () {
      AppNotification withActor(Object? actor) => AppNotification.fromJson({
        'id': 'n-a',
        'verb': 'automation.message',
        'actor': actor,
        'created_at': '2026-10-22T09:00:00Z',
      });
      final cases = <Object?, String>{
        {
          'id': 'p-1',
          'user_details': {'email': 'asha@example.com', 'profile_pic': null},
        }: 'asha@example.com',
        {'id': 'p-1', 'user_details': null}: 'Someone',
        {'id': 'p-1', 'user_details': <String, dynamic>{}}: 'Someone',
        {
          'id': 'p-1',
          'user_details': {'email': false},
        }: 'Someone',
        {'id': 'p-1', 'email': 'top-level@example.com'}: 'Someone',
      };
      for (final e in cases.entries) {
        expect(withActor(e.key).displayActorName, e.value, reason: '${e.key}');
      }
      expect(withActor(null).actorName, isNull);
      expect(withActor(null).displayActorName, 'The system');
      expect(withActor('p-1').displayActorName, 'The system');
    });

    test('markup in a message stays the same string', () {
      const markup =
          '<img src=x onerror=alert(1)> <b>now</b> [open](javascript:x)';
      final n = _automation('automation.message', {
        'message': markup,
        'rule': '<b>Rule</b>',
      });

      expect(n.automationText(), markup);
      expect(n.automationSource, 'From the rule "<b>Rule</b>"');
    });

    test('a failed rule names the rule and the reason', () {
      final n = _automation('automation.failed', {
        'rule': 'Close it',
        'rule_id': _id,
        'reason': "That is not a stage of this deal's pipeline.",
      });

      expect(n.ruleName, 'Close it');
      expect(n.reason, "That is not a stage of this deal's pipeline.");
      expect(
        n.automationText(),
        'The rule "Close it" failed: '
        "That is not a stage of this deal's pipeline.",
      );
      expect(n.automationSource, isNull);
      expect(n.displayActorName, 'The system');
    });

    test('a pause gives the reason, then every rule it paused', () {
      final n = _automation('automation.paused', {
        'rules': ['Deal won: onboarding', 'New lead: rotate owners'],
        'reason':
            'Paused because the admin who switched it on left the '
            'organization.',
      });

      expect(n.ruleNames, ['Deal won: onboarding', 'New lead: rotate owners']);
      expect(
        n.automationText(),
        'Paused because the admin who switched it on left the organization. '
        'Paused: Deal won: onboarding, New lead: rotate owners.',
      );
    });

    test('the limit names the hour it started, in the zone asked for', () {
      final n = _automation('automation.limit', {
        'window_start': '2025-01-15T14:00:00+00:00',
      });

      expect(n.windowStart, DateTime.utc(2025, 1, 15, 14));
      expect(
        n.automationText(zone: 'UTC'),
        'Automation reached its limit of 1,000 runs in the hour from '
        "15 Jan 2025 14:00. The rest of that hour's runs were skipped.",
      );
      expect(
        n.automationText(zone: 'Asia/Kolkata'),
        contains('in the hour from 15 Jan 2025 19:30.'),
      );
    });

    test('the limit writes today and yesterday in lower case', () {
      final now = DateTime.utc(2026, 10, 22, 15);
      String? from(String at) => _automation('automation.limit', {
        'window_start': at,
      }).automationText(zone: 'UTC', now: now);

      expect(
        from('2026-10-22T14:00:00+00:00'),
        contains('in the hour from today 14:00.'),
      );
      expect(
        from('2026-10-21T14:00:00+00:00'),
        contains('in the hour from yesterday 14:00.'),
      );
      expect(
        from('2026-10-19T14:00:00+00:00'),
        contains('in the hour from 19 Oct 14:00.'),
      );
    });

    test('data of the wrong type gives no automation text', () {
      final cases = <(String, Object?)>[
        ('automation.message', null),
        ('automation.message', 'Kick off'),
        ('automation.message', {'message': 7, 'rule': 'R'}),
        (
          'automation.message',
          {
            'message': 'Kick off',
            'rule': ['R'],
          },
        ),
        ('automation.failed', {'rule': 'R', 'reason': null}),
        ('automation.failed', {'rule': 9, 'reason': 'Broke'}),
        ('automation.paused', {'rules': 'R', 'reason': 'Paused.'}),
        (
          'automation.paused',
          {
            'rules': ['R', 3],
            'reason': 'Paused.',
          },
        ),
        (
          'automation.paused',
          {
            'rules': ['R'],
            'reason': 5,
          },
        ),
        ('automation.limit', {'window_start': 1760000000}),
        ('automation.limit', {'window_start': 'not a date'}),
        ('automation.limit', <String, dynamic>{}),
      ];
      for (final (verb, data) in cases) {
        final n = _automation(verb, data);
        expect(n.automationText(), isNull, reason: '$verb $data');
        expect(n.automationSource, isNull, reason: '$verb $data');
      }
      final bad = _automation('automation.paused', {
        'rules': ['R', 3],
        'reason': 5,
        'message': ['x'],
        'rule': 1,
        'window_start': 2,
      });
      expect(bad.ruleNames, isNull);
      expect(bad.reason, isNull);
      expect(bad.message, isNull);
      expect(bad.ruleName, isNull);
      expect(bad.windowStart, isNull);
    });

    test('marking it read keeps what the row says', () {
      final n = _automation('automation.paused', {
        'rules': ['R'],
        'reason': 'Paused because the owner left.',
      });
      final read = n.copyWith(readAt: DateTime.utc(2026, 10, 22, 10));

      expect(read.isUnread, isFalse);
      expect(read.automationText(), n.automationText());
      expect(read.ruleNames, ['R']);
    });

    test('other verbs have no automation text', () {
      final n = _automation('case.mentioned', {
        'message': 'Kick off',
        'rule': 'R',
      });

      expect(n.automationText(), isNull);
      expect(n.automationSource, isNull);
    });

    test('record links open the record screens', () {
      final routes = {
        '/leads/$_id': '/leads/$_id',
        '/contacts/$_id': '/contacts/$_id',
        '/accounts/$_id': '/accounts/$_id',
        '/pipeline/$_id': '/deals/$_id',
        '/tasks/$_id': '/tasks/$_id',
        '/tickets/$_id': '/tickets/$_id',
        '/objects/property_unit/$_id': '/more/objects/property_unit/$_id',
        '/leads/${_id.toUpperCase()}': '/leads/${_id.toUpperCase()}',
      };
      for (final e in routes.entries) {
        expect(_withLink(e.key).destinationPath, e.value, reason: e.key);
      }
    });

    test('settings links open automation', () {
      expect(
        _withLink('/settings/automation').destinationPath,
        '/more/settings/automation',
      );
      expect(
        _withLink('/settings/automation/$_id').destinationPath,
        '/more/settings/automation/$_id',
      );
    });

    test('foreign, script and malformed links open nothing', () {
      for (final link in [
        'https://evil.example/leads/$_id',
        '//evil.example',
        '//evil.example/leads/$_id',
        'javascript:alert(1)',
        '/leads/not-a-uuid',
        '/pipeline/1',
        '/deals/$_id',
        '/objects/BAD/$_id',
        '/objects/x/$_id',
        '/objects/property/not-a-uuid',
        '/settings/automation/x',
        '/settings/automationx',
        '/settings/automation/$_id/runs',
        '/leads/$_id?next=//evil.example',
        '/leads/$_id/edit',
        '',
        '/leads/$_id/',
        '/pipeline/$_id/',
        '/objects/property/$_id/',
        '/settings/automation/',
        '/settings/automation/$_id/',
        '/LEADS/$_id',
        '/Settings/automation',
        '/leads/$_id/../x',
        '/leads/../settings/automation',
        '/leads/%2F%2Fevil.example',
        '/leads/${_id.replaceAll('-', '%2D')}',
        r'\\evil.example\leads\' + _id,
        '/leads\\$_id',
        '/\\evil.example/leads/$_id',
        ' /leads/$_id',
        '/leads/$_id ',
        ' /settings/automation ',
        '/leads/$_id\n',
        '/settings/automation\n',
        '/settings/automation/$_id\n',
        '\t/tasks/$_id',
      ]) {
        expect(_withLink(link).destinationPath, isNull, reason: link);
      }
    });
  });
}
