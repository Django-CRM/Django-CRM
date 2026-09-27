/// Calendar dates for a form's defaults, in the org's day.
///
/// "Today" is the org's day, not UTC's and not the phone's: an invoice raised
/// at 09:00 in Kolkata is dated that day, although UTC still reads yesterday,
/// and a phone set to another zone does not move it. The web computes the same
/// thing in `frontend/src/lib/v2/dates.js` (`todayIn`, `addDays`).
///
/// The zone is the access token's `org_settings.timezone` claim. Login, org
/// switch and token refresh each replace the token, so reading it on every
/// call is what keeps the zone current. It is display only: the API dates
/// what it owns itself.
library;

import 'package:jwt_decoder/jwt_decoder.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import 'auth_service.dart';

bool _zonesLoaded = false;

/// Load the bundled IANA database. `main()` calls this once at start; the
/// helpers below call it too, so a test or an early caller never meets an
/// empty database. Safe to call again.
///
/// The `all` database, not `latest` or `latest_10y`: those two leave out every
/// backward link (`US/Eastern`, `Asia/Calcutta`, even `UTC` and
/// `Europe/Amsterdam`), 258 of the 599 names the API accepts for an org, and
/// an org on any of them would silently get UTC's day.
void loadTimeZones() {
  if (_zonesLoaded) return;
  try {
    tzdata.initializeTimeZones();
    _zonesLoaded = true;
  } catch (_) {
    // Never stop the app starting over dates: with no database every zone
    // lookup below falls back to UTC, the same answer as an unknown zone.
  }
}

/// The IANA zone an access token names for its org, or `UTC` when it names
/// none (a token minted before an org was picked, or by an older API).
String orgTimeZoneOf(String? accessToken) {
  if (accessToken == null) return 'UTC';
  final settings = JwtDecoder.tryDecode(accessToken)?['org_settings'];
  final zone = settings is Map ? settings['timezone'] : null;
  return zone is String && zone.isNotEmpty ? zone : 'UTC';
}

/// Today in [zone], as a date-only local `DateTime` (midnight, no zone of its
/// own), which is the shape every date field and picker here holds. A zone
/// the database does not know falls back to UTC, the API's own default,
/// rather than throwing on a form's first build.
DateTime todayIn(String? zone, {DateTime? now}) {
  loadTimeZones();
  tz.Location location;
  try {
    location = tz.getLocation(zone ?? 'UTC');
  } catch (_) {
    location = tz.UTC;
  }
  final t = tz.TZDateTime.from(now ?? DateTime.now(), location);
  return DateTime(t.year, t.month, t.day);
}

/// Today in the signed-in org's zone. Every business-date default reads this.
DateTime orgToday({DateTime? now}) =>
    todayIn(orgTimeZoneOf(AuthService().accessToken), now: now);

/// [days] after [date], counted on the calendar. Not `add(Duration)`, which
/// adds 24-hour blocks and lands on the wrong day across a DST change.
DateTime addDays(DateTime date, int days) =>
    DateTime(date.year, date.month, date.day + days);

/// Calendar days from the org's today to [date]: negative once it has passed.
int orgDaysUntil(DateTime date, {DateTime? now}) {
  final today = orgToday(now: now);
  return DateTime.utc(
    date.year,
    date.month,
    date.day,
  ).difference(DateTime.utc(today.year, today.month, today.day)).inDays;
}
