import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/services/auth_service.dart';
import 'package:bottle_crm/services/org_date.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/org_zone.dart';

/// The org's zone rides on the access token, so it has to follow every event
/// that replaces the token: an org switch through [authProvider], a silent
/// refresh, and a sign-out. A form opened after any of them must date itself
/// by the org the session is now in.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // 23:30 UTC on 31 Dec: 1 Jan in Kolkata, 31 Dec in UTC and New York.
  final instant = DateTime.utc(2026, 12, 31, 23, 30);
  const acme = Organization(id: 'org-1', name: 'Acme');
  const globex = Organization(id: 'org-2', name: 'Globex');

  late FakeAuthApi api;
  late ProviderContainer container;

  setUp(() {
    api = FakeAuthApi()..install();
    container = ProviderContainer();
  });

  tearDown(() async {
    container.dispose();
    await api.uninstall();
  });

  test('switching org moves the day to the new org\'s zone', () async {
    final auth = container.read(authProvider.notifier);

    api.nextSwitch = orgToken('Asia/Kolkata', orgId: acme.id);
    expect(await auth.switchOrganization(acme), isTrue);
    expect(orgToday(now: instant), DateTime(2027, 1, 1));

    api.nextSwitch = orgToken('US/Eastern', orgId: globex.id);
    expect(await auth.switchOrganization(globex), isTrue);
    expect(container.read(selectedOrgProvider)?.id, globex.id);
    expect(orgToday(now: instant), DateTime(2026, 12, 31));
  });

  test('a refreshed token carries a changed zone with it', () async {
    api.nextSwitch = orgToken('UTC');
    await container.read(authProvider.notifier).switchOrganization(acme);
    expect(orgToday(now: instant), DateTime(2026, 12, 31));

    // An admin moved the org to Kolkata; the next refresh re-derives the
    // claims from the database.
    api.nextRefresh = orgToken('Asia/Kolkata');
    expect(await AuthService().refreshAccessToken(), isTrue);
    expect(orgToday(now: instant), DateTime(2027, 1, 1));
  });

  test('signing out returns to UTC', () async {
    api.nextSwitch = orgToken('Asia/Kolkata');
    await container.read(authProvider.notifier).switchOrganization(acme);
    expect(orgToday(now: instant), DateTime(2027, 1, 1));

    await container.read(authProvider.notifier).signOut();
    expect(orgToday(now: instant), DateTime(2026, 12, 31));
  });
}
