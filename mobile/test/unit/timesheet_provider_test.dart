import 'dart:convert';

import 'package:bottle_crm/data/models/time_entry.dart';
import 'package:bottle_crm/data/models/timesheet.dart';
import 'package:bottle_crm/providers/timesheet_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/org_date.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// The weekly timesheet, module 18 and the first screen mobile has that reads
/// across tickets rather than down one.
///
/// Three things are worth pinning.
///
/// **`case` arrives in two shapes.** A ticket's own entry list sends a bare id;
/// the timesheet expands it to `{id, name}`, because a week spanning six
/// tickets needs names. `TimeEntry.fromJson` used to do `json['case']
/// ?.toString()`, which on the object form yields the literal text
/// `{id: ..., name: ...}` and a tap that opens nothing.
///
/// **A running total must tick and a stopped one must not.** The screen adds
/// the minutes it has been open to the server's live figure. Adding them to a
/// settled entry would inflate last Tuesday every thirty seconds.
///
/// **The week is state, not a parameter.** Navigating weeks changes one
/// provider and the fetch follows it, so the request has to carry the dates
/// the user picked.
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

/// Monday has a stopped billed entry and a stopped internal one; Tuesday has a
/// timer still running; Wednesday is empty and still present.
const _week = '''
{
  "profile_id": "profile-1",
  "profile": {"id": "profile-1", "name": "Ada"},
  "start": "2026-08-03",
  "end": "2026-08-09",
  "total_minutes": 150,
  "billable_minutes": 120,
  "running_count": 1,
  "server_now": "2026-08-04T09:00:00Z",
  "days": [
    {
      "date": "2026-08-03",
      "total_minutes": 150,
      "billable_minutes": 120,
      "entries": [
        {
          "id": "entry-billed",
          "case": {"id": "case-9", "name": "Printer on fire"},
          "profile": {"id": "profile-1"},
          "started_at": "2026-08-03T09:00:00Z",
          "ended_at": "2026-08-03T11:00:00Z",
          "duration_minutes": 120,
          "billable": true,
          "description": "Diagnosis",
          "invoice": {"id": "inv-2", "invoice_number": "INV-0002"},
          "is_running": false
        },
        {
          "id": "entry-internal",
          "case": {"id": "case-4", "name": "Internal cleanup"},
          "profile": {"id": "profile-1"},
          "started_at": "2026-08-03T14:00:00Z",
          "ended_at": "2026-08-03T14:30:00Z",
          "duration_minutes": 30,
          "billable": false,
          "invoice": null,
          "is_running": false
        }
      ]
    },
    {
      "date": "2026-08-04",
      "total_minutes": 12,
      "billable_minutes": 12,
      "entries": [
        {
          "id": "entry-running",
          "case": {"id": "case-9", "name": "Printer on fire"},
          "profile": {"id": "profile-1"},
          "started_at": "2026-08-04T08:48:00Z",
          "ended_at": null,
          "duration_minutes": null,
          "billable": true,
          "invoice": null,
          "is_running": true,
          "live_duration_minutes": 12
        }
      ]
    },
    {
      "date": "2026-08-05",
      "total_minutes": 0,
      "billable_minutes": 0,
      "entries": []
    }
  ]
}
''';

void main() {
  late ProviderContainer container;
  late _FakeClient client;

  setUp(() {
    client = _FakeClient(body: _week);
    ApiService().setClientForTesting(client);
    container = ProviderContainer();
  });

  tearDown(() => container.dispose());

  group('reading the week', () {
    test('a ticket sent as an object gives an id and a name', () async {
      final week = await container.read(timesheetProvider.future);
      final entry = week.days.first.entries.first;

      expect(entry.caseId, 'case-9');
      expect(entry.caseName, 'Printer on fire');
      // The failure this replaces: `{id: case-9, name: Printer on fire}`.
      expect(entry.caseId, isNot(contains('{')));
    });

    test(
      'a bare ticket id still parses, because the ticket list sends one',
      () {
        final entry = TimeEntry.fromJson({
          'id': 'e1',
          'case': 'case-9',
          'started_at': '2026-08-03T09:00:00Z',
        });

        expect(entry.caseId, 'case-9');
        expect(entry.caseName, isNull);
      },
    );

    test(
      'an invoiced entry carries its number, an unbilled one carries none',
      () async {
        final week = await container.read(timesheetProvider.future);
        final billed = week.days.first.entries[0];
        final internal = week.days.first.entries[1];

        expect(billed.isBilled, isTrue);
        expect(billed.invoiceNumber, 'INV-0002');
        expect(internal.isBilled, isFalse);
        expect(internal.invoiceId, isNull);
      },
    );

    test(
      'an empty day is kept, because that is the point of the screen',
      () async {
        final week = await container.read(timesheetProvider.future);

        expect(week.days, hasLength(3));
        expect(week.days.last.isEmpty, isTrue);
        expect(week.days.last.date.day, 5);
      },
    );

    test('the running entry is found without trusting the clock', () async {
      final week = await container.read(timesheetProvider.future);

      expect(week.runningCount, 1);
      expect(week.runningEntries.map((e) => e.id), ['entry-running']);
    });

    test('billable work with no invoice is counted as a prompt', () async {
      final week = await container.read(timesheetProvider.future);

      // The running entry only. The other billable one is already on INV-0002,
      // and offering to bill it again is offering a refund.
      expect(week.unbilledCount, 1);
    });

    test('a failure is a failure, not an empty week', () async {
      final noRetry = ProviderContainer(retry: (_, _) => null);
      addTearDown(noRetry.dispose);
      client.status = 500;
      client.body = '{}';

      await expectLater(
        noRetry.read(timesheetProvider.future),
        throwsA(isA<Exception>()),
      );
    });
  });

  group('what ticks and what does not', () {
    test(
      'a running entry grows by the minutes the screen has been open',
      () async {
        final week = await container.read(timesheetProvider.future);
        final running = week.runningEntries.first;

        expect(running.minutesAt(0), 12);
        expect(running.minutesAt(7), 19);
      },
    );

    test('a stopped entry ignores elapsed time entirely', () async {
      final week = await container.read(timesheetProvider.future);
      final stopped = week.days.first.entries.first;

      expect(stopped.minutesAt(0), 120);
      expect(stopped.minutesAt(45), 120);
    });

    test('week totals tick only by the running entry', () async {
      final week = await container.read(timesheetProvider.future);

      // 120 + 30 + 12 at rest; ten minutes later only the running one moved.
      expect(week.totalMinutesAt(0), 162);
      expect(week.totalMinutesAt(10), 172);
      // Billable excludes the 30-minute internal entry throughout.
      expect(week.billableMinutesAt(0), 132);
      expect(week.billableMinutesAt(10), 142);
    });
  });

  group('what the week is worth', () {
    test('it values billable time at the rate saved on each entry', () {
      final week = _weekOf([
        _rated(minutes: 120, rate: 75, currency: 'USD'),
        _rated(minutes: 60, rate: 100, currency: 'USD'),
      ]);

      // 2h at 75 plus 1h at 100. Not 3h at either rate, which is the whole
      // reason the rate is stored per entry.
      expect(week.billableValueAt(0)?.amount, 250);
      expect(week.billableValueAt(0)?.currency, 'USD');
    });

    test('two currencies in one week produce no total at all', () {
      final week = _weekOf([
        _rated(minutes: 60, rate: 100, currency: 'USD'),
        _rated(minutes: 60, rate: 100, currency: 'EUR'),
      ]);

      // 200 of nothing. A single figure with one symbol on it would be a
      // wrong number about money, which is worse than no number.
      expect(week.billableValueAt(0), isNull);
    });

    test('unrated and non-billable time is worth nothing', () {
      final week = _weekOf([
        _rated(minutes: 120, rate: null, currency: 'USD'),
        _rated(minutes: 120, rate: 90, currency: 'USD', billable: false),
      ]);

      expect(week.billableValueAt(0), isNull);
    });

    test('a running rated entry accrues value as it runs', () {
      final week = _weekOf([
        TimeEntry(
          id: 'running',
          caseId: 'c1',
          profileId: 'p1',
          startedAt: DateTime(2026, 8, 4, 9),
          billable: true,
          hourlyRate: 60,
          currency: 'USD',
          liveDurationMinutes: 30,
        ),
      ]);

      expect(week.billableValueAt(0)?.amount, 30);
      expect(week.billableValueAt(30)?.amount, 60);
    });

    test('a decimal rate sent as a string still parses', () {
      final entry = TimeEntry.fromJson({
        'id': 'e1',
        'case': 'c1',
        'started_at': '2026-08-03T09:00:00Z',
        'hourly_rate': '75.50',
        'currency': 'GBP',
      });

      expect(entry.hourlyRate, 75.5);
      expect(entry.currency, 'GBP');
    });
  });

  group('picking a week', () {
    test('a Sunday resolves to the Monday before it, not the one after', () {
      // 2026-08-09 is a Sunday. `weekday` is 1..7 with Monday at 1, so Sunday
      // is 7 and the Monday is six days back. A Sunday-first index would put
      // this week's Monday a day in the future.
      final range = TimesheetRange.weekOf(DateTime(2026, 8, 9));

      expect(range.startParam, '2026-08-03');
      expect(range.endParam, '2026-08-09');
    });

    test('a Monday is its own week start', () {
      final range = TimesheetRange.weekOf(DateTime(2026, 8, 3));

      expect(range.startParam, '2026-08-03');
      expect(range.endParam, '2026-08-09');
    });

    test('dates are zero-padded, because the API parses %Y-%m-%d', () {
      final range = TimesheetRange.weekOf(DateTime(2026, 1, 5));

      expect(range.startParam, '2026-01-05');
    });

    test('the fetch carries the week the user moved to', () async {
      await container.read(timesheetProvider.future);
      final firstUrl = client.sent.first.url.toString();

      container.read(timesheetRangeProvider.notifier).shift(-7);
      await container.read(timesheetProvider.future);

      final movedTo = container.read(timesheetRangeProvider);
      final lastUrl = client.sent.last.url.toString();
      expect(lastUrl, isNot(firstUrl));
      expect(lastUrl, contains('start=${movedTo.startParam}'));
      expect(lastUrl, contains('end=${movedTo.endParam}'));
    });

    test('the request never asks for anyone else\'s week', () async {
      await container.read(timesheetProvider.future);

      // `?profile=` is admin-only and answers 403 for everyone else. This
      // screen is "your week" and must not send it at all.
      expect(client.sent.first.url.toString(), isNot(contains('profile=')));
    });

    test('this week is recognised so the reset control can be withheld', () {
      // The org's week: signed out here, so UTC's.
      final now = TimesheetRange.weekOf(orgToday());

      expect(now.isCurrent, isTrue);
      expect(now.shift(-7).isCurrent, isFalse);
    });
  });

  group('stopping a timer', () {
    test('it posts to the entry-scoped stop route', () async {
      await container.read(timesheetProvider.future);

      await container
          .read(timesheetProvider.notifier)
          .stopTimer('entry-running');

      final post = client.sent.firstWhere((r) => r.method == 'POST');
      expect(post.url.path, endsWith('/time-entries/entry-running/stop/'));
    });

    test(
      'a successful stop refetches, because the server settles the duration',
      () async {
        await container.read(timesheetProvider.future);
        final getsBefore = client.sent.where((r) => r.method == 'GET').length;

        await container
            .read(timesheetProvider.notifier)
            .stopTimer('entry-running');

        expect(
          client.sent.where((r) => r.method == 'GET').length,
          greaterThan(getsBefore),
        );
      },
    );

    test(
      'a refused stop returns the server\'s words and does not refetch',
      () async {
        await container.read(timesheetProvider.future);
        final getsBefore = client.sent.where((r) => r.method == 'GET').length;
        client.status = 400;
        client.body = '{"detail": "Timer is already stopped."}';

        final response = await container
            .read(timesheetProvider.notifier)
            .stopTimer('entry-running');

        expect(response.success, isFalse);
        expect(response.message, contains('already stopped'));
        expect(client.sent.where((r) => r.method == 'GET').length, getsBefore);
      },
    );

    test('an empty id sends nothing at all', () async {
      await container.read(timesheetProvider.future);
      final before = client.sent.length;

      final response = await container
          .read(timesheetProvider.notifier)
          .stopTimer('');

      // It would POST to `/time-entries//stop/`, a different path whose
      // failure reads as a routing bug rather than a missing argument.
      expect(response.success, isFalse);
      expect(client.sent, hasLength(before));
    });
  });
}

/// A one-day week holding [entries], for the value rules, which are pure
/// arithmetic over a parsed week and need no HTTP.
TimesheetWeek _weekOf(List<TimeEntry> entries) => TimesheetWeek(
  start: DateTime(2026, 8, 3),
  end: DateTime(2026, 8, 9),
  days: [TimesheetDay(date: DateTime(2026, 8, 3), entries: entries)],
);

TimeEntry _rated({
  required int minutes,
  required double? rate,
  required String currency,
  bool billable = true,
}) => TimeEntry(
  id: 'e$minutes-$rate-$currency',
  caseId: 'c1',
  profileId: 'p1',
  startedAt: DateTime(2026, 8, 3, 9),
  endedAt: DateTime(2026, 8, 3, 9).add(Duration(minutes: minutes)),
  durationMinutes: minutes,
  billable: billable,
  hourlyRate: rate,
  currency: currency,
);
