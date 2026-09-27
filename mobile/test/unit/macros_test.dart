import 'dart:convert';

import 'package:bottle_crm/data/models/macro.dart';
import 'package:bottle_crm/providers/settings_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

/// Saved replies, the one settings page every member can write to.
///
/// The three things worth pinning:
///
/// - Who may write. An org macro needs an admin, a personal one needs its
///   owner, and an admin gets no exception on somebody else's personal macro.
///   `MacroDetailView._get_writable` answers 404 rather than 403 there, so the
///   id space cannot be used to find out whose private replies exist.
/// - What removing one does. `DELETE` turns an org macro off and deletes a
///   personal one for good, from the same endpoint. A dialog that says the
///   wrong one is the defect.
/// - `owner` never goes on the wire. It is `read_only` on the serializer and
///   re-derived from `request.profile`; a client that could name one could
///   file a saved reply as somebody else.
class _FakeClient extends http.BaseClient {
  int status = 200;
  String body = '{}';
  final List<http.BaseRequest> sent = [];
  final List<String> bodies = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    sent.add(request);
    final bytes = await request.finalize().toBytes();
    bodies.add(utf8.decode(bytes));
    return http.StreamedResponse(
      Stream.value(utf8.encode(body)),
      status,
      request: request,
    );
  }
}

Macro _macro({
  String id = 'm1',
  String title = 'Password reset',
  String body = 'Hello %customer_name%',
  String scope = Macro.scopeOrg,
  String? ownerEmail,
  bool isActive = true,
}) {
  return Macro(
    id: id,
    title: title,
    body: body,
    scope: scope,
    ownerEmail: ownerEmail,
    isActive: isActive,
  );
}

void main() {
  group('who may write a macro', () {
    // Both answers, for both scopes. A check that can only return one of them
    // is a check that is not running.
    test('an admin may write an org macro', () {
      expect(
        canWriteMacro(
          isAdmin: true,
          scope: Macro.scopeOrg,
          ownerEmail: null,
          myEmail: 'me@example.com',
        ),
        isTrue,
      );
    });

    test('a member may not write an org macro', () {
      expect(
        canWriteMacro(
          isAdmin: false,
          scope: Macro.scopeOrg,
          ownerEmail: null,
          myEmail: 'me@example.com',
        ),
        isFalse,
      );
    });

    test('anyone may write their own personal macro', () {
      expect(
        canWriteMacro(
          isAdmin: false,
          scope: Macro.scopePersonal,
          ownerEmail: 'me@example.com',
          myEmail: 'me@example.com',
        ),
        isTrue,
      );
    });

    test('an admin may NOT write somebody else personal macro', () {
      // The one place admin is not an override. The server answers 404 there,
      // so offering Edit would produce a refusal that reads like a bug and
      // would confirm the row exists.
      expect(
        canWriteMacro(
          isAdmin: true,
          scope: Macro.scopePersonal,
          ownerEmail: 'someone@example.com',
          myEmail: 'me@example.com',
        ),
        isFalse,
      );
    });

    test('the email comparison ignores case and surrounding space', () {
      expect(
        canWriteMacro(
          isAdmin: false,
          scope: Macro.scopePersonal,
          ownerEmail: '  Me@Example.com ',
          myEmail: 'me@example.com',
        ),
        isTrue,
      );
    });

    test('an unknown email hides the action rather than offering it', () {
      for (final pair in [
        (owner: null, me: 'me@example.com'),
        (owner: 'me@example.com', me: null),
        (owner: '', me: 'me@example.com'),
      ]) {
        expect(
          canWriteMacro(
            isAdmin: false,
            scope: Macro.scopePersonal,
            ownerEmail: pair.owner,
            myEmail: pair.me,
          ),
          isFalse,
          reason: '${pair.owner} / ${pair.me}',
        );
      }
    });
  });

  group('what removing one does', () {
    test('an org macro is turned off, and the dialog says so', () {
      final removal = macroRemoval(_macro(scope: Macro.scopeOrg));
      expect(removal.actionLabel, 'Turn off');
      expect(removal.isPermanent, isFalse);
      expect(removal.detail, contains('turn it back on'));
      expect(removal.detail, isNot(contains('for good')));
    });

    test('a personal macro is deleted, and the dialog says so', () {
      // Same endpoint, opposite outcome. Calling this "Turn off" would promise
      // a row that is about to stop existing.
      final removal = macroRemoval(
        _macro(scope: Macro.scopePersonal, ownerEmail: 'me@example.com'),
      );
      expect(removal.actionLabel, 'Delete');
      expect(removal.isPermanent, isTrue);
      expect(removal.detail, contains('for good'));
    });

    test('the dialog names the macro and the verb it is about to apply', () {
      expect(
        macroRemoval(_macro(title: 'Refund policy')).title,
        'Turn off Refund policy?',
      );
      expect(
        macroRemoval(
          _macro(
            title: 'Refund policy',
            scope: Macro.scopePersonal,
            ownerEmail: 'me@example.com',
          ),
        ).title,
        'Delete Refund policy?',
      );
    });
  });

  group('draft validation', () {
    test('a complete draft passes', () {
      expect(
        validateMacroDraft(title: 'Hi', body: 'There', scope: Macro.scopeOrg),
        isNull,
      );
    });

    test('both a title and a body are required', () {
      // `MacroSerializer` requires both, so a blank one is a 400 rather than a
      // saved-but-empty row.
      expect(
        validateMacroDraft(title: '  ', body: 'x', scope: Macro.scopeOrg),
        contains('title'),
      );
      expect(
        validateMacroDraft(title: 'x', body: '  ', scope: Macro.scopeOrg),
        contains('something to say'),
      );
    });

    test('a scope outside the two is refused', () {
      expect(
        validateMacroDraft(title: 'x', body: 'y', scope: 'everyone'),
        contains('who this reply is for'),
      );
    });
  });

  group('the payload', () {
    test('never carries owner, org or usage_count', () {
      // `owner` is read_only and re-derived from request.profile. A client
      // that could name one could file a saved reply as somebody else.
      final body = macroPayload(
        title: ' Hi ',
        body: ' There ',
        scope: Macro.scopePersonal,
      );
      expect(body.keys.toSet(), {'title', 'body', 'scope'});
    });

    test('trims the title and body', () {
      final body = macroPayload(
        title: '  Hi  ',
        body: '  There  ',
        scope: Macro.scopeOrg,
      );
      expect(body['title'], 'Hi');
      expect(body['body'], 'There');
    });

    test('the activate payload is is_active and nothing else', () {
      // A reactivate control has no title or body to send, and the serializer
      // requires both on a full write.
      expect(macroActivatePayload(), {'is_active': true});
    });
  });

  group('parsing', () {
    test('reads the fields the row shows', () {
      final macro = Macro.fromJson({
        'id': 'm1',
        'title': 'Password reset',
        'body': 'Hi %customer_name%, try %reset_link%',
        'scope': 'personal',
        'owner': 'p1',
        'owner_name': 'me@example.com',
        'is_active': false,
        'usage_count': 12,
        'unknown_placeholders': ['%reset_link%'],
      });
      expect(macro.isPersonal, isTrue);
      expect(macro.scopeLabel, 'Just you');
      expect(macro.ownerEmail, 'me@example.com');
      expect(macro.isActive, isFalse);
      expect(macro.usageCount, 12);
      expect(macro.unknownPlaceholders, ['%reset_link%']);
    });

    test('an org macro has no owner and says Everyone', () {
      final macro = Macro.fromJson({
        'id': 'm1',
        'scope': 'org',
        'owner': null,
        'owner_name': null,
      });
      expect(macro.ownerEmail, isNull);
      expect(macro.scopeLabel, 'Everyone');
    });

    test('an empty owner_name reads as unknown, not as an owner', () {
      // An empty string compared against an empty email would make everybody
      // the owner of it. `canWriteMacro` refuses an empty either way, and this
      // keeps the two from having to agree separately.
      expect(
        Macro.fromJson({'id': 'm1', 'owner_name': '  '}).ownerEmail,
        isNull,
      );
    });

    test('broken placeholders come from the server, not from a local regex', () {
      // Nothing here recomputes them. A second implementation of the token set
      // would drift from `macros/render.py` the first time either changed, and
      // the client's guess is not what goes out to the customer.
      final macro = Macro.fromJson({
        'id': 'm1',
        'body': 'Hi %customer_name% and %nonsense%',
        'unknown_placeholders': [],
      });
      expect(macro.unknownPlaceholders, isEmpty);
    });
  });

  group('the provider', () {
    late ProviderContainer container;
    late _FakeClient client;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
      container = ProviderContainer();
    });

    tearDown(() => container.dispose());

    Future<MacrosState> readMacros() {
      container.listen(macrosProvider, (_, _) {});
      return container.read(macrosProvider.future);
    }

    Future<List<Macro>> readActive() {
      container.listen(activeMacrosProvider, (_, _) {});
      return container.read(activeMacrosProvider.future);
    }

    test('reads the rows, the totals and the placeholder reference', () async {
      client.body = '''
      {"results": [
        {"id": "m1", "title": "Reset", "body": "Hi", "scope": "org",
         "is_active": true, "usage_count": 3, "unknown_placeholders": []}
      ],
       "totals": {"count": 5, "org": 3, "personal": 2, "inactive": 1,
                  "with_unknown_placeholders": 1},
       "placeholders": [
         {"token": "%customer_name%", "resolves": "The case's first contact"}
       ]}
      ''';
      final state = await readMacros();
      expect(state.macros.single.title, 'Reset');
      expect(state.orgCount, 3);
      expect(state.personalCount, 2);
      expect(state.inactiveCount, 1);
      expect(state.brokenCount, 1);
      expect(state.placeholders.single.token, '%customer_name%');
    });

    test('the settings list asks for the turned-off rows too', () async {
      // It is the screen that turns one back on, so it cannot filter them out.
      client.body = '{"results": []}';
      await readMacros();
      expect(client.sent.single.url.queryParameters, isEmpty);
    });

    test('the reply-box list asks only for the active ones', () async {
      // A turned-off reply must not be offered on a ticket at all: the render
      // endpoint answers 400 for one, so it would be a dead row in a picker.
      client.body = '{"results": []}';
      await readActive();
      expect(client.sent.single.url.queryParameters['active'], 'true');
    });

    test('an edit is a PATCH, not a PUT', () async {
      // `MacroDetailView.put` runs the serializer non-partial and
      // `MacroSerializer` requires both title and body, so PUT is a 400
      // waiting to happen the moment a caller sends a partial body.
      client.body = '{"results": []}';
      await readMacros();
      await container.read(macrosProvider.notifier).updateMacro('m1', {
        'title': 'x',
      });
      expect(client.sent.any((r) => r.method == 'PUT'), isFalse);
      final patch = client.sent.firstWhere((r) => r.method == 'PATCH');
      expect(patch.url.path, endsWith('/macros/m1/'));
    });

    test('turning one on sends only is_active', () async {
      client.body = '{"results": []}';
      await readMacros();
      await container.read(macrosProvider.notifier).activateMacro('m1');
      final patch = client.sent.firstWhere((r) => r.method == 'PATCH');
      expect(jsonDecode(client.bodies[client.sent.indexOf(patch)]), {
        'is_active': true,
      });
    });

    test('removing one is a DELETE on the row', () async {
      client.body = '{"results": []}';
      await readMacros();
      await container.read(macrosProvider.notifier).removeMacro('m1');
      final sent = client.sent.firstWhere((r) => r.method == 'DELETE');
      expect(sent.url.path, endsWith('/macros/m1/'));
    });

    test('the scope refusal is reported in the server own words', () async {
      // These endpoints answer `{"error": ...}`, not the `{"errors": ...}` the
      // rest of the app uses, and the wording is the difference between a bug
      // and a rule the user can act on.
      client.body = '{"results": []}';
      await readMacros();
      client.status = 403;
      client.body = '{"error": "Only admins can manage org-scope macros."}';
      final message = await container.read(macrosProvider.notifier).createMacro(
        {'title': 'x', 'body': 'y', 'scope': 'org'},
      );
      expect(message, 'Only admins can manage org-scope macros.');
    });

    test('a 404 on somebody else macro is reported as given', () async {
      client.body = '{"results": []}';
      await readMacros();
      client.status = 404;
      client.body = '{"detail": "Not found."}';
      final message = await container
          .read(macrosProvider.notifier)
          .removeMacro('m2');
      expect(message, 'Not found.');
    });
  });

  group('expanding one against a ticket', () {
    late _FakeClient client;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
    });

    test('posts the ticket id and returns the rendered text', () async {
      client.body = '{"rendered_body": "Hi Dana, about ticket 42"}';
      final result = await renderMacro(macroId: 'm1', ticketId: 't42');

      expect(result.text, 'Hi Dana, about ticket 42');
      expect(result.error, isNull);
      final sent = client.sent.single;
      expect(sent.method, 'POST');
      expect(sent.url.path, endsWith('/macros/m1/render/'));
      expect(jsonDecode(client.bodies.single), {'case_id': 't42'});
    });

    test('reports the refusal rather than inserting nothing', () async {
      client.status = 400;
      client.body = '{"error": "Macro is inactive."}';
      final result = await renderMacro(macroId: 'm1', ticketId: 't42');

      expect(result.text, isNull);
      expect(result.error, 'Macro is inactive.');
    });

    test('a response with no body is an error, not an empty insert', () async {
      // Silently inserting nothing would look like the tap did not register,
      // and the agent would send an empty reply.
      client.body = '{}';
      final result = await renderMacro(macroId: 'm1', ticketId: 't42');

      expect(result.text, isNull);
      expect(result.error, isNotNull);
    });
  });

  group('actions', () {
    final json = {
      'id': 'm1',
      'title': 'Escalate',
      'body': '',
      'set_status': 'Closed',
      'set_priority': 'Urgent',
      'set_assignees_details': [
        {'id': 'p1', 'name': 'Ann', 'email': 'ann@x.test', 'is_active': true},
        {'id': 'p2', 'name': '', 'email': 'bo@x.test', 'is_active': false},
      ],
      'add_tags_details': [
        {'id': 't1', 'name': 'vip', 'is_active': false},
      ],
    };

    test('parses the four actions and names inactive rows', () {
      final macro = Macro.fromJson(json);
      expect(macro.hasBody, isFalse);
      expect(macro.setStatus, 'Closed');
      expect(macro.assignees.map((a) => a.label), [
        'Ann',
        'bo@x.test (deactivated)',
      ]);
      expect(macro.tags.single.label, 'vip (archived)');
    });

    test('one chip per action, keyed by the name apply takes', () {
      expect(macroActionChips(Macro.fromJson(json)).map((c) => c.key), [
        'status',
        'priority',
        'assignees',
        'tags',
      ]);
      expect(macroActionChips(_macro()), isEmpty);
    });

    test('Duplicate is not a status a macro may set', () {
      expect(macroStatuses, isNot(contains('Duplicate')));
    });

    test('a draft with no text passes only when it carries an action', () {
      expect(
        validateMacroDraft(title: 'x', body: '', scope: Macro.scopeOrg),
        contains('something to do'),
      );
      expect(
        validateMacroDraft(
          title: 'x',
          body: '',
          scope: Macro.scopeOrg,
          hasAction: true,
        ),
        isNull,
      );
    });

    test('the payload carries the actions only when given', () {
      final body = macroPayload(
        title: 'x',
        body: '',
        scope: Macro.scopeOrg,
        setStatus: '',
        setPriority: 'High',
        assigneeIds: const ['p1'],
        tagIds: const [],
      );
      expect(body, {
        'title': 'x',
        'body': '',
        'scope': 'org',
        'set_status': '',
        'set_priority': 'High',
        'set_assignees': ['p1'],
        'add_tags': [],
      });
      expect(body.containsKey('owner'), isFalse);
    });

    test('the summary says what applied and why the rest did not', () {
      expect(
        macroApplySummary({
          'applied': ['priority'],
          'skipped': [
            {'action': 'assignees', 'reason': 'Everyone is deactivated.'},
          ],
        }),
        'Macro applied: priority. Everyone is deactivated.',
      );
      expect(
        macroApplySummary({'applied': [], 'skipped': []}),
        'The macro changed nothing on this ticket.',
      );
    });
  });

  group('applying one to a ticket', () {
    late _FakeClient client;

    setUp(() {
      client = _FakeClient();
      ApiService().setClientForTesting(client);
    });

    test('posts the ticket and the kept actions', () async {
      client.body = '{"applied": ["status"], "skipped": []}';
      final result = await applyMacro(
        macroId: 'm1',
        ticketId: 't42',
        only: const ['status'],
      );
      expect(result.summary, 'Macro applied: status.');
      expect(client.sent.single.url.path, endsWith('/macros/m1/apply/'));
      expect(jsonDecode(client.bodies.single), {
        'case_id': 't42',
        'only': ['status'],
      });
    });

    test('omits only to apply everything', () async {
      client.body = '{"applied": [], "skipped": []}';
      await applyMacro(macroId: 'm1', ticketId: 't42');
      expect(jsonDecode(client.bodies.single), {'case_id': 't42'});
    });

    test('a gate refusal comes back in the server words', () async {
      client.status = 400;
      client.body =
          '{"error": true, "errors": {"status": ["An approval is required '
          'before this case can be closed (rule: Close gate)."]}}';
      final result = await applyMacro(macroId: 'm1', ticketId: 't42');
      expect(result.summary, isNull);
      expect(result.error, contains('An approval is required'));
    });
  });
}
