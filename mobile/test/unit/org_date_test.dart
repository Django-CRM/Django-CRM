import 'package:bottle_crm/services/org_date.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/org_zone.dart';

/// `orgToday()` is the org's calendar day, the same answer the web's
/// `todayIn(data.org.timezone)` gives (`frontend/src/lib/v2/dates.js`). These
/// pin the instants where the org's day and UTC's (or the phone's) differ.
void main() {
  DateTime day(int y, int m, int d) => DateTime(y, m, d);

  group('todayIn', () {
    test('23:30 UTC on 31 Dec is already 1 Jan in Kolkata', () {
      final now = DateTime.utc(2026, 12, 31, 23, 30);
      expect(todayIn('Asia/Kolkata', now: now), day(2027, 1, 1));
      expect(todayIn('UTC', now: now), day(2026, 12, 31));
    });

    test('US/Eastern, a legacy name, is still the day before near UTC '
        'midnight', () {
      final now = DateTime.utc(2026, 6, 15, 2, 0);
      expect(todayIn('US/Eastern', now: now), day(2026, 6, 14));
      expect(todayIn('America/New_York', now: now), day(2026, 6, 14));
      expect(todayIn('UTC', now: now), day(2026, 6, 15));
    });

    test('other backward-link names the API accepts resolve too', () {
      // The `latest` and `latest_10y` databases drop every one of these, and
      // an org on one of them would get UTC's day without this database.
      final now = DateTime.utc(2026, 12, 31, 20, 0);
      expect(todayIn('Asia/Calcutta', now: now), day(2027, 1, 1));
      expect(todayIn('Europe/Amsterdam', now: now), day(2026, 12, 31));
      expect(todayIn('Pacific/Auckland', now: now), day(2027, 1, 1));
      expect(todayIn('NZ', now: now), day(2027, 1, 1));
      expect(
        todayIn('US/Pacific', now: DateTime.utc(2026, 1, 1, 7, 59)),
        day(2025, 12, 31),
      );
    });

    test('follows New York across both DST changes', () {
      // Spring forward, 8 Mar 2026: 04:30 UTC is 00:30 EDT on the 9th, but
      // would still be 23:30 on the 8th on a fixed EST offset.
      expect(
        todayIn('America/New_York', now: DateTime.utc(2026, 3, 9, 4, 30)),
        day(2026, 3, 9),
      );
      // The night before the change, the same UTC time is still the 7th.
      expect(
        todayIn('America/New_York', now: DateTime.utc(2026, 3, 8, 4, 30)),
        day(2026, 3, 7),
      );
      // Fall back, 1 Nov 2026: 04:30 UTC on the 2nd is 23:30 EST on the 1st,
      // where a fixed EDT offset would already read the 2nd.
      expect(
        todayIn('America/New_York', now: DateTime.utc(2026, 11, 2, 4, 30)),
        day(2026, 11, 1),
      );
    });

    test('an unknown, empty or missing zone falls back to UTC', () {
      final now = DateTime.utc(2026, 12, 31, 23, 30);
      expect(todayIn('Mars/Olympus_Mons', now: now), day(2026, 12, 31));
      expect(todayIn('', now: now), day(2026, 12, 31));
      expect(todayIn(null, now: now), day(2026, 12, 31));
    });

    test('is a date only: midnight, whatever the instant', () {
      final t = todayIn('Asia/Tokyo', now: DateTime.utc(2026, 5, 5, 13, 7));
      expect(t, day(2026, 5, 5));
      expect(t.isUtc, isFalse);
      expect([t.hour, t.minute, t.second], [0, 0, 0]);
    });
  });

  group('orgTimeZoneOf', () {
    test('reads the org_settings.timezone claim', () {
      expect(orgTimeZoneOf(orgToken('Asia/Kolkata')), 'Asia/Kolkata');
      expect(orgTimeZoneOf(orgToken('US/Eastern')), 'US/Eastern');
    });

    test('is UTC for a token with no claim, a bad token, or none', () {
      expect(orgTimeZoneOf(orgToken(null)), 'UTC');
      expect(orgTimeZoneOf(orgToken('')), 'UTC');
      expect(orgTimeZoneOf('not-a-jwt'), 'UTC');
      expect(orgTimeZoneOf(null), 'UTC');
    });
  });

  group('calendar arithmetic', () {
    test('addDays counts days, across month and year ends', () {
      expect(addDays(day(2026, 12, 2), 30), day(2027, 1, 1));
      expect(addDays(day(2026, 3, 1), -1), day(2026, 2, 28));
      expect(addDays(day(2026, 3, 1), 0), day(2026, 3, 1));
    });

    test('orgDaysUntil counts from the org day, signed', () {
      // Signed out: no token, so UTC's day, 31 Dec here.
      final now = DateTime.utc(2026, 12, 31, 23, 30);
      expect(orgDaysUntil(day(2026, 12, 31), now: now), 0);
      expect(orgDaysUntil(day(2027, 1, 1), now: now), 1);
      expect(orgDaysUntil(day(2026, 12, 20), now: now), -11);
    });
  });

  group('orgToday reads the signed-in org', () {
    late FakeAuthApi api;
    tearDown(() => api.uninstall());

    test('Kolkata org: 23:30 UTC on 31 Dec is 1 Jan', () async {
      api = await useOrgZone('Asia/Kolkata');
      final now = DateTime.utc(2026, 12, 31, 23, 30);
      expect(orgToday(now: now), day(2027, 1, 1));
      expect(orgDaysUntil(day(2027, 1, 1), now: now), 0);
    });

    test('an org whose zone this build does not know gets UTC', () async {
      api = await useOrgZone('Nowhere/Special');
      expect(
        orgToday(now: DateTime.utc(2026, 12, 31, 23, 30)),
        day(2026, 12, 31),
      );
    });
  });
}
