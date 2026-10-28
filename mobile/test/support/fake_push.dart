import 'dart:async';
import 'dart:convert';

import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/services/push_messaging.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A [PushMessaging] a test drives by hand. Nothing reaches Firebase.
class FakePush implements PushMessaging {
  FakePush({
    this.currentPermission = PushPermission.notDetermined,
    this.answer = PushPermission.granted,
    this.currentToken = 'tok-1',
    this.cold,
  });

  PushPermission currentPermission;
  PushPermission answer;
  String? currentToken;
  PushMessage? cold;
  var asked = 0;
  var tokenDeleted = false;

  final refreshes = StreamController<String>.broadcast();
  final foreground = StreamController<PushMessage>.broadcast();
  final opened = StreamController<PushMessage>.broadcast();

  @override
  bool get supported => true;
  @override
  Future<PushPermission> permission() async => currentPermission;
  @override
  Future<PushPermission> requestPermission() async {
    asked++;
    return currentPermission = answer;
  }

  @override
  Future<String?> token() async => currentToken;
  @override
  Future<void> deleteToken() async => tokenDeleted = true;
  @override
  Stream<String> get onTokenRefresh => refreshes.stream;
  @override
  Stream<PushMessage> get onForeground => foreground.stream;
  @override
  Stream<PushMessage> get onOpened => opened.stream;
  @override
  Future<PushMessage?> initialMessage() async => cold;
}

/// Answers the two device calls through the real [ApiService], so the
/// contract check sees every request. Records what was sent.
class FakeDeviceApi {
  FakeDeviceApi({this.status = 204, this.hang = false}) {
    ApiService().setClientForTesting(
      MockClient((request) async {
        final body = request.body.isEmpty
            ? <String, dynamic>{}
            : jsonDecode(request.body) as Map<String, dynamic>;
        (request.method == 'POST' ? posts : deletes).add(body);
        if (hang) await Completer<void>().future;
        return http.Response('', status);
      }),
    );
  }

  final int status;

  /// Never answers, to stand in for a dead network.
  final bool hang;
  final posts = <Map<String, dynamic>>[];
  final deletes = <Map<String, dynamic>>[];

  ApiService get client => ApiService();
}
