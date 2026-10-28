import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/auth_service.dart';
import 'package:bottle_crm/services/device_registration.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../support/org_zone.dart';

class _Recording extends DeviceRegistration {
  _Recording(this.events) : super(ApiService());

  final List<String> events;

  @override
  Future<void> register({String? token}) async => events.add('register');

  @override
  void finishSignOut() => events.add('finish');

  @override
  Future<void> unregister({bool forget = false}) async =>
      events.add(forget ? 'unregister+forget' : 'unregister');
}

/// When the phone joins and leaves an org's alerts: after every org select,
/// before an org switch, and on sign-out before the server is told to end the
/// session (the delete needs the token still in hand).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const acme = Organization(id: 'org-1', name: 'Acme');
  const globex = Organization(id: 'org-2', name: 'Globex');

  late FakeAuthApi api;
  late List<String> events;
  late DeviceRegistration saved;

  setUp(() {
    api = FakeAuthApi()..install();
    events = [];
    ApiService().setClientForTesting(
      MockClient((request) async {
        if (request.url.path.endsWith('/auth/logout/')) events.add('logout');
        final copy = http.Request(request.method, request.url)
          ..headers.addAll(request.headers)
          ..body = request.body;
        return http.Response.fromStream(await api.client.send(copy));
      }),
    );
    saved = DeviceRegistration.instance;
    DeviceRegistration.instance = _Recording(events);
  });

  tearDown(() async {
    DeviceRegistration.instance = saved;
    await api.uninstall();
  });

  test('registers after the first select and moves on a switch', () async {
    await AuthService().selectOrganization(acme);
    expect(events, ['register']);

    events.clear();
    await AuthService().selectOrganization(globex);
    expect(events, ['unregister', 'register']);
  });

  test('sign-out deletes the device before the server logout', () async {
    await AuthService().selectOrganization(acme);
    events.clear();

    await AuthService().signOut();

    expect(events, ['unregister+forget', 'logout', 'finish']);
  });

  test('a sign-out with no refresh token still drops the device', () async {
    await AuthService().signOut();
    events.clear();

    await AuthService().signOut();

    expect(events, ['unregister+forget', 'finish']);
  });
}
