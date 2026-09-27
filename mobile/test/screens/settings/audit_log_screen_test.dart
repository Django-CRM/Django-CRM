import 'package:bottle_crm/data/models/audit_entry.dart';
import 'package:bottle_crm/providers/audit_log_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/screens/settings/audit_log_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The audit log on the phone: a 390px phone at 1.0x and 1.3x text (Flutter
/// reports an overflow as an exception `takeException` returns), a tablet,
/// the member gate, tapping a person to filter, the token refresh switch, and
/// the model and query helpers the screen leans on.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void useViewport(
    WidgetTester tester, {
    Size size = const Size(390, 844),
    double textScale = 1.0,
  }) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = Size(size.width * 3, size.height * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  final paused = AuditEntry.fromJson(const {
    'id': 'a1',
    'event_type': 'WEBHOOK_PAUSED',
    'event_label': 'Webhook Paused',
    'created_at': '2026-09-26T10:15:00Z',
    'success': true,
    'actor': {
      'id': 'u1',
      'name': 'Asha Raman with a rather long display name',
      'email': 'asha@example.com',
    },
    'ip_address': '2001:db8:85a3:0000:0000:8a2e:0370:7334',
    'details': {
      'endpoint_id': 'w1',
      'pause_reason':
          'Paused because the admin who created it is no longer an admin.',
    },
  });
  final refused = AuditEntry.fromJson(const {
    'id': 'a2',
    'event_type': 'PERMISSION_DENIED',
    'event_label': 'Permission Denied',
    'created_at': '2026-09-25T08:00:00Z',
    'success': false,
    'actor': null,
    'details': {'action': 'ORG_SWITCH', 'resource': 'org:123'},
  });
  final page = AuditLogPage(
    entries: [paused, refused],
    count: 60,
    eventTypes: const [
      AuditEventType(value: 'WEBHOOK_PAUSED', label: 'Webhook Paused'),
      AuditEventType(value: 'PERMISSION_DENIED', label: 'Permission Denied'),
    ],
  );

  // Credential events: an admin revoking a member's token, and a calendar
  // feed regenerated. Plus a type this build has never heard of, which still
  // shows the label the server sent.
  final revoked = AuditEntry.fromJson(const {
    'id': 'a6',
    'event_type': 'API_TOKEN_REVOKED',
    'event_label': 'API Token Revoked',
    'created_at': '2026-09-27T09:00:00Z',
    'actor': {'id': 'admin', 'name': 'Priya', 'email': 'p@example.com'},
    'details': {
      'token_id': 't1',
      'token_prefix': 'bcrm_pat_ab12',
      'token_name': 'Laptop script with a rather long descriptive name',
      'scopes': ['leads:read', 'contacts:read', 'accounts:read'],
      'owner_id': 'u1',
      'owner_name': 'Asha Raman with a rather long display name',
    },
  });
  final regenerated = AuditEntry.fromJson(const {
    'id': 'a7',
    'event_type': 'CALENDAR_FEED_REGENERATED',
    'event_label': 'Calendar Feed Regenerated',
    'created_at': '2026-09-27T08:00:00Z',
    'actor': {'id': 'u1', 'name': 'Asha', 'email': 'a@example.com'},
    'details': <String, dynamic>{},
  });
  final unknown = AuditEntry.fromJson(const {
    'id': 'a8',
    'event_type': 'SOMETHING_NEW',
    'event_label': 'Something New',
    'created_at': '2026-09-27T07:00:00Z',
    'actor': null,
    'details': {'whatever': 1},
  });
  final credentialPage = AuditLogPage(
    entries: [revoked, regenerated, unknown],
    count: 3,
    eventTypes: const [
      AuditEventType(value: 'API_TOKEN_CREATED', label: 'API Token Created'),
      AuditEventType(value: 'API_TOKEN_REVOKED', label: 'API Token Revoked'),
      AuditEventType(
        value: 'CALENDAR_FEED_REGENERATED',
        label: 'Calendar Feed Regenerated',
      ),
    ],
  );

  late List<AuditLogQuery> asked;

  Future<void> pump(
    WidgetTester tester, {
    bool admin = true,
    double textScale = 1.0,
    Size size = const Size(390, 844),
    AuditLogPage? data,
  }) async {
    asked = [];
    useViewport(tester, size: size, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          isOrgAdminProvider.overrideWithValue(admin),
          auditLogProvider.overrideWith((ref, q) async {
            asked.add(q);
            return data ?? page;
          }),
        ],
        child: const MaterialApp(home: AuditLogScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('fits a 390px phone at ${scale}x text', (tester) async {
      await pump(tester, textScale: scale);
      expect(tester.takeException(), isNull);
      expect(find.text('Webhook Paused'), findsWidgets);
      expect(find.text(paused.detail), findsOneWidget);
      expect(find.text('Refused'), findsOneWidget);
      expect(find.text('Open webhook'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('Older'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(tester.takeException(), isNull);
    });
  }

  for (final scale in [1.0, 1.3]) {
    testWidgets('credential entries fit a 390px phone at ${scale}x text', (
      tester,
    ) async {
      await pump(tester, textScale: scale, data: credentialPage);
      expect(tester.takeException(), isNull);
      expect(find.text('API Token Revoked'), findsOneWidget);
      expect(find.text(revoked.detail), findsOneWidget);
      expect(find.text('Calendar Feed Regenerated'), findsWidgets);
      expect(find.text('Something New'), findsOneWidget);
    });
  }

  testWidgets('the event filter lists the credential events', (tester) async {
    await pump(tester, data: credentialPage);
    await tester.tap(find.byType(DropdownButtonFormField<String?>));
    await tester.pumpAndSettle();
    expect(find.text('API Token Created'), findsWidgets);
    expect(find.text('API Token Revoked'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('holds up at tablet width', (tester) async {
    await pump(tester, size: const Size(834, 1112), textScale: 1.3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a member sees the notice and never asks', (tester) async {
    await pump(tester, admin: false);
    expect(find.text('Administrators only'), findsOneWidget);
    expect(asked, isEmpty);
  });

  testWidgets('tapping a person narrows the log to them', (tester) async {
    await pump(tester);
    await tester.tap(find.text(paused.actorLabel));
    await tester.pumpAndSettle();
    expect(asked.last.actor, 'u1');
    expect(asked.last.offset, 0);
    expect(find.text('Show everyone'), findsOneWidget);
  });

  testWidgets('token refreshes stay hidden until the switch is on', (
    tester,
  ) async {
    await pump(tester);
    expect(asked.last.includeTokenRefresh, isFalse);
    expect(find.text('Show token refreshes'), findsOneWidget);
    await tester.tap(find.text('Show token refreshes'));
    await tester.pumpAndSettle();
    expect(asked.last.includeTokenRefresh, isTrue);
    expect(asked.last.offset, 0);
    // A view option, not a filter: it alone offers no "Clear".
    expect(find.text('Clear'), findsNothing);
    await tester.tap(find.text(paused.actorLabel));
    await tester.pumpAndSettle();
    expect(asked.last.actor, 'u1');
    expect(asked.last.includeTokenRefresh, isTrue);
  });

  testWidgets('a system entry has no person to tap', (tester) async {
    await pump(tester);
    final button = tester.widget<TextButton>(
      find.ancestor(
        of: find.text('No user'),
        matching: find.byType(TextButton),
      ),
    );
    expect(button.onPressed, isNull);
  });

  group('models and query', () {
    test('details read as one line, as on the web', () {
      expect(paused.detail, startsWith('Paused because'));
      expect(refused.detail, 'ORG_SWITCH on org:123');
      expect(paused.webhookId, 'w1');
      expect(refused.webhookId, isNull);
      expect(refused.actorLabel, 'No user');
      final changed = AuditEntry.fromJson(const {
        'id': 'a3',
        'event_type': 'WEBHOOK_CHANGED',
        'details': {
          'changed': ['url', 'secret'],
        },
      });
      expect(
        changed.detail,
        'Changed url, secret, and now answers for the webhook.',
      );
      final merged = AuditEntry.fromJson(const {
        'id': 'a4',
        'event_type': 'RECORD_MERGED',
        'details': {
          'entity': 'contact',
          'kept_id': 'k1',
          'kept_name': 'Liz Lopez',
          'merged_id': 'm1',
          'merged_name': 'Elizabeth Lopez',
        },
      });
      expect(
        merged.detail,
        'Merged contact "Elizabeth Lopez" into "Liz Lopez".',
      );
      final twins = AuditEntry.fromJson(const {
        'id': 'a5',
        'event_type': 'RECORD_MERGED',
        'details': {
          'entity': 'contact',
          'kept_id': '5e6f7a8b-0000-4000-8000-000000000000',
          'kept_name': 'Rosalind Beck',
          'merged_id': '1a2b3c4d-0000-4000-8000-000000000000',
          'merged_name': 'Rosalind Beck',
        },
      });
      expect(
        twins.detail,
        'Merged contact "Rosalind Beck" (1a2b3c4d) into "Rosalind Beck" (5e6f7a8b).',
      );
    });

    test('credential entries read as one line, as on the web', () {
      expect(
        revoked.detail,
        'Token "Laptop script with a rather long descriptive name" '
        '(bcrm_pat_ab12), scopes leads:read, contacts:read, accounts:read, '
        'owned by Asha Raman with a rather long display name.',
      );
      final created = AuditEntry.fromJson(const {
        'id': 'a9',
        'event_type': 'API_TOKEN_CREATED',
        'actor': {'id': 'u1'},
        'details': {
          'token_prefix': 'bcrm_pat_ab12',
          'token_name': 'CI script',
          'scopes': <String>[],
          'owner_id': 'u1',
          'owner_name': 'Asha',
        },
      });
      expect(created.detail, 'Token "CI script" (bcrm_pat_ab12), full access.');
      expect(regenerated.detail, '');
      expect(unknown.detail, '');
      expect(unknown.eventLabel, 'Something New');
      final unlabelled = AuditEntry.fromJson(const {
        'id': 'a10',
        'event_type': 'SOMETHING_NEWER',
      });
      expect(unlabelled.eventLabel, 'SOMETHING_NEWER');
    });

    test('only set filters are sent, dates as YYYY-MM-DD', () {
      final params = auditLogParams((
        eventType: 'ORG_SWITCH',
        actor: null,
        from: DateTime(2026, 9, 1),
        to: null,
        includeTokenRefresh: false,
        offset: 25,
      ));
      expect(params, {
        'limit': '$auditLogPageSize',
        'offset': '25',
        'event_type': 'ORG_SWITCH',
        'from': '2026-09-01',
      });
    });

    test('the token refresh switch is sent only when on', () {
      final params = auditLogParams((
        eventType: null,
        actor: null,
        from: null,
        to: null,
        includeTokenRefresh: true,
        offset: 0,
      ));
      expect(params, {
        'limit': '$auditLogPageSize',
        'offset': '0',
        'include_token_refresh': 'true',
      });
    });
  });
}
