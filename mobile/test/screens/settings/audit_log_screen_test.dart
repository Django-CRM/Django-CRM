import 'package:bottle_crm/data/models/audit_entry.dart';
import 'package:bottle_crm/providers/audit_log_provider.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/screens/settings/audit_log_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The audit log on the phone: a 390px phone at 1.0x and 1.3x text (Flutter
/// reports an overflow as an exception `takeException` returns), a tablet,
/// the member gate, tapping a person to filter, and the model and query
/// helpers the screen leans on.
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

  late List<AuditLogQuery> asked;

  Future<void> pump(
    WidgetTester tester, {
    bool admin = true,
    double textScale = 1.0,
    Size size = const Size(390, 844),
  }) async {
    asked = [];
    useViewport(tester, size: size, textScale: textScale);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          isOrgAdminProvider.overrideWithValue(admin),
          auditLogProvider.overrideWith((ref, q) async {
            asked.add(q);
            return page;
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
    });

    test('only set filters are sent, dates as YYYY-MM-DD', () {
      final params = auditLogParams((
        eventType: 'ORG_SWITCH',
        actor: null,
        from: DateTime(2026, 9, 1),
        to: null,
        offset: 25,
      ));
      expect(params, {
        'limit': '$auditLogPageSize',
        'offset': '25',
        'event_type': 'ORG_SWITCH',
        'from': '2026-09-01',
      });
    });
  });
}
