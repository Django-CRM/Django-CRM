import 'dart:convert';

import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/auth_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// An unsigned access token shaped like the API's: an hour to live, and the
/// org claims `OrgAwareRefreshToken` embeds, with `org_settings.timezone` set
/// to [zone] (left out entirely when [zone] is null, as a token minted before
/// an org is picked does).
String orgToken(String? zone, {String orgId = 'org-1'}) {
  String seg(Map<String, dynamic> m) =>
      base64Url.encode(utf8.encode(jsonEncode(m))).replaceAll('=', '');
  final exp =
      DateTime.now().add(const Duration(hours: 1)).millisecondsSinceEpoch ~/
      1000;
  return '${seg({'alg': 'HS256', 'typ': 'JWT'})}.'
      '${seg({
        'exp': exp,
        'org_id': orgId,
        if (zone != null) 'org_settings': {'default_currency': 'USD', 'timezone': zone},
      })}.sig';
}

/// What the API's auth endpoints answer, one token per call: `switch-org`
/// with [nextSwitch], `refresh-token` with [nextRefresh], and `logout` with
/// 200. Tests set the next token before the call that should receive it.
class FakeAuthApi {
  String nextSwitch = orgToken('UTC');
  String nextRefresh = orgToken('UTC');

  late final MockClient client = MockClient((request) async {
    final path = request.url.path;
    if (path.endsWith('/auth/switch-org/')) {
      return http.Response(
        jsonEncode({
          'access_token': nextSwitch,
          'refresh_token': 'refresh-after-switch',
          'profile': {'role': 'ADMIN', 'is_organization_admin': true},
        }),
        200,
      );
    }
    if (path.endsWith('/auth/refresh-token/')) {
      return http.Response(
        jsonEncode({'access': nextRefresh, 'refresh': 'refresh-rotated'}),
        200,
      );
    }
    return http.Response('{}', 200);
  });

  /// Point the shared [ApiService] at this fake, with in-memory stores behind
  /// [AuthService], which persists the tokens it is handed.
  void install() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
    ApiService().setClientForTesting(client);
  }

  /// Sign out and put the real client back, so the next test starts with no
  /// token and so with UTC's day.
  Future<void> uninstall() async {
    await AuthService().signOut();
    ApiService().setRefreshCallback(null);
    ApiService().setClientForTesting(http.Client());
  }
}

/// Put the app's session in an org whose token names [zone], through the
/// real org-switch path, so a screen's `orgToday()` reads that zone.
Future<FakeAuthApi> useOrgZone(String zone) async {
  final api = FakeAuthApi()..install();
  api.nextSwitch = orgToken(zone);
  final ok = await AuthService().selectOrganization(
    const Organization(id: 'org-1', name: 'Acme'),
  );
  if (!ok) throw StateError('the fake org switch failed');
  return api;
}
