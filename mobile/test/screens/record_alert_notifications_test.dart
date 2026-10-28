import 'package:bottle_crm/data/models/app_notification.dart';
import 'package:bottle_crm/providers/notifications_provider.dart';
import 'package:bottle_crm/routes/app_router.dart';
import 'package:bottle_crm/screens/notifications/notifications_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

const _matchId = '11111111-1111-4111-8111-111111111111';
const _seatId = '22222222-2222-4222-8222-222222222222';
const _bareMatchId = '33333333-3333-4333-8333-333333333333';
const _bareSeatId = '44444444-4444-4444-8444-444444444444';

Map<String, dynamic> _row(String id, String verb, String? name, String link) =>
    {
      'id': id,
      'verb': verb,
      'actor': null,
      'entity_name': name,
      'data': <String, dynamic>{},
      'link': link,
      'read_at': null,
      'created_at': DateTime.now()
          .subtract(const Duration(minutes: 5))
          .toUtc()
          .toIso8601String(),
    };

class _FakeFeed extends NotificationsNotifier {
  @override
  Future<NotificationFeed> build() async {
    final items = [
      AppNotification.fromJson(
        _row(
          'n1',
          'real_estate.match',
          'Asha Raman',
          '/objects/requirement/$_matchId',
        ),
      ),
      AppNotification.fromJson(
        _row(
          'n2',
          'education.seat_open',
          'Year 7 Maths',
          '/objects/class/$_seatId',
        ),
      ),
      AppNotification.fromJson(
        _row(
          'n3',
          'real_estate.match',
          null,
          '/objects/requirement/$_bareMatchId',
        ),
      ),
      AppNotification.fromJson(
        _row('n4', 'education.seat_open', null, '/objects/class/$_bareSeatId'),
      ),
    ];
    return NotificationFeed(
      items: items,
      unreadCount: items.length,
      total: items.length,
    );
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> markRead(String id) async =>
      const ApiResponse(success: true, statusCode: 200);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<void> pump(WidgetTester tester) async {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [notificationsProvider.overrideWith(_FakeFeed.new)],
        child: MaterialApp.router(
          routerConfig: GoRouter(
            initialLocation: '/here',
            routes: [
              GoRoute(
                path: '/here',
                builder: (_, _) => const NotificationsScreen(),
              ),
              GoRoute(
                path: AppRoutes.objectRecordPattern,
                builder: (_, s) => Text(
                  'record ${s.pathParameters['key']} ${s.pathParameters['id']}',
                ),
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Finder rowOf(Finder text) =>
      find.ancestor(of: text, matching: find.byType(InkWell)).first;

  for (final (text, key, id, icon) in [
    (
      'Asha Raman fits an open requirement',
      'requirement',
      _matchId,
      LucideIcons.house,
    ),
    ('A seat opened in Year 7 Maths', 'class', _seatId, LucideIcons.armchair),
    (
      'A record fits an open requirement',
      'requirement',
      _bareMatchId,
      LucideIcons.house,
    ),
    ('A seat opened in a record', 'class', _bareSeatId, LucideIcons.armchair),
  ]) {
    testWidgets('"$text" has its icon and opens /objects/$key/<id>', (
      tester,
    ) async {
      await pump(tester);
      expect(find.text(text), findsOneWidget);
      expect(
        find.descendant(
          of: rowOf(find.text(text)),
          matching: find.byIcon(icon),
        ),
        findsOneWidget,
      );

      await tester.tap(find.text(text));
      await tester.pumpAndSettle();

      expect(find.text('record $key $id'), findsOneWidget);
    });
  }
}
