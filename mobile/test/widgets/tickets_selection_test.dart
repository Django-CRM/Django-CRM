import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/ticket.dart';
import 'package:bottle_crm/providers/lookup_provider.dart';
import 'package:bottle_crm/providers/tickets_provider.dart';
import 'package:bottle_crm/screens/tickets/tickets_list_screen.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Selection mode and the bulk action sheet on the tickets list, the mobile
/// counterpart to the web bulk bar (`frontend/src/lib/v2/components/BulkActionBar.svelte`).
///
/// The harness mirrors `test/screens/tasks/tasks_list_screen_test.dart` and
/// `test/screens/deals/deals_list_screen_test.dart`: a fake `TicketsNotifier`
/// subclass overrides `build` with canned data and records the bulk calls, so
/// the widget pumps with no live backend.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('TicketsListScreen selection mode', () {
    testWidgets('the app-bar toggle enters selection mode with a checkbox '
        'per row', (tester) async {
      final notifier = _FakeTicketsNotifier(
        TicketsListData(
          tickets: [_ticket(id: 't1', name: 'Login broken')],
          totalCount: 1,
          hasMore: false,
        ),
      );

      await tester.pumpWidget(_testApp(notifier));
      await tester.pumpAndSettle();

      expect(find.byType(Checkbox), findsNothing);

      await tester.tap(find.byTooltip('Select tickets'));
      await tester.pumpAndSettle();

      expect(find.byType(Checkbox), findsOneWidget);
    });

    testWidgets('selecting a card and opening Actions lists all six bulk '
        'actions', (tester) async {
      final notifier = _FakeTicketsNotifier(
        TicketsListData(
          tickets: [_ticket(id: 't1', name: 'Login broken')],
          totalCount: 1,
          hasMore: false,
        ),
      );

      await tester.pumpWidget(_testApp(notifier));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Select tickets'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();

      expect(find.text('1 selected'), findsOneWidget);
      await tester.tap(find.text('Actions'));
      await tester.pumpAndSettle();

      for (final label in [
        'Reassign',
        'Set priority',
        'Set status',
        'Set type',
        'Add tags',
        'Delete',
      ]) {
        expect(find.text(label), findsOneWidget, reason: label);
      }
    });

    testWidgets(
      'Set priority, then Urgent, calls bulkUpdate with the selected id and '
      'a scalar field, not a list',
      (tester) async {
        final notifier = _FakeTicketsNotifier(
          TicketsListData(
            tickets: [_ticket(id: 't1', name: 'Login broken')],
            totalCount: 1,
            hasMore: false,
          ),
        );

        await tester.pumpWidget(_testApp(notifier));
        await tester.pumpAndSettle();

        await tester.tap(find.byTooltip('Select tickets'));
        await tester.pumpAndSettle();
        await tester.tap(find.byType(Checkbox).first);
        await tester.pumpAndSettle();
        await tester.tap(find.text('Actions'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Set priority'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Urgent'));
        await tester.pumpAndSettle();

        expect(notifier.bulkUpdateCalls, hasLength(1));
        expect(notifier.bulkUpdateCalls.single.ids, ['t1']);
        expect(notifier.bulkUpdateCalls.single.fields, {'priority': 'Urgent'});

        // The bar clears and the SnackBar reports the same wording the web
        // bulk bar's summaryText() produces for one clean update.
        expect(find.text('1 updated'), findsOneWidget);
        expect(find.text('1 selected'), findsNothing);
      },
    );
  });

  group('TicketsListScreen bulk bar and sheet at phone and tablet widths', () {
    // An iPhone-ish logical viewport, the narrow end of what ships today.
    // Mirrors test/screens/phone_viewport_test.dart's usePhone helper: a
    // screen isn't done until it holds up at 390px, checked by rendering
    // rather than by reading the layout code.
    void useSize(WidgetTester tester, Size logicalSize) {
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = logicalSize;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
    }

    // Selects two of three loaded tickets and opens the actions sheet, so
    // both the bottom bar and the sheet are on screen together, the state
    // Step 5 of the brief would otherwise check by hand on a device.
    Future<void> pumpWithSelectionAndSheet(
      WidgetTester tester,
      Size size,
    ) async {
      useSize(tester, size);
      final notifier = _FakeTicketsNotifier(
        TicketsListData(
          tickets: [
            _ticket(id: 't1', name: 'Login broken for the whole billing team'),
            _ticket(id: 't2', name: 'Export stuck at ninety percent'),
            _ticket(id: 't3', name: 'Password reset email never arrives'),
          ],
          totalCount: 3,
          hasMore: false,
        ),
      );

      await tester.pumpWidget(_testApp(notifier));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Select tickets'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).at(0));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).at(1));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Actions'));
      await tester.pumpAndSettle();
    }

    testWidgets('the bulk bar and the actions sheet fit at 390px', (
      tester,
    ) async {
      await pumpWithSelectionAndSheet(tester, const Size(390, 844));

      expect(tester.takeException(), isNull);
      expect(find.text('2 selected'), findsOneWidget);
      expect(find.text('Reassign'), findsOneWidget);
    });

    testWidgets('the bulk bar and the actions sheet fit at a tablet width', (
      tester,
    ) async {
      await pumpWithSelectionAndSheet(tester, const Size(800, 1100));

      expect(tester.takeException(), isNull);
      expect(find.text('2 selected'), findsOneWidget);
      expect(find.text('Reassign'), findsOneWidget);
    });
  });
}

Widget _testApp(_FakeTicketsNotifier notifier) {
  return ProviderScope(
    overrides: [
      ticketsProvider.overrideWith(() => notifier),
      usersProvider.overrideWithValue(const []),
      tagsProvider.overrideWithValue(const []),
      accountOptionsProvider.overrideWithValue(const []),
    ],
    child: MaterialApp(theme: AppTheme.light, home: const TicketsListScreen()),
  );
}

Ticket _ticket({required String id, required String name}) {
  return Ticket(
    id: id,
    name: name,
    status: TicketStatus.newStatus,
    priority: TicketPriority.normal,
    ticketType: TicketType.question,
    createdAt: DateTime(2026, 5, 1),
  );
}

/// Records the ids and fields shape passed to `bulkUpdate` as a plain class
/// rather than a Dart record: a record's `==` compares `List`/`Map` fields by
/// identity, not by content, which would make an `expect(..., [...])` on the
/// call list fail even for a matching call. Asserting `.ids` and `.fields`
/// directly (each a bare List/Map) gets `flutter_test`'s deep equality.
class _BulkUpdateCall {
  final List<String> ids;
  final Map<String, dynamic> fields;
  const _BulkUpdateCall(this.ids, this.fields);
}

class _FakeTicketsNotifier extends TicketsNotifier {
  _FakeTicketsNotifier(this.initialData);

  final TicketsListData initialData;
  final List<_BulkUpdateCall> bulkUpdateCalls = [];
  final List<List<String>> bulkDeleteCalls = [];

  @override
  Future<TicketsListData> build() async => initialData;

  @override
  Future<void> refresh({
    TicketListFilters filters = const TicketListFilters(),
  }) async {}

  @override
  Future<void> loadMore({
    TicketListFilters filters = const TicketListFilters(),
  }) async {}

  @override
  Future<ApiResponse<Map<String, dynamic>>> bulkUpdate(
    List<String> ids,
    Map<String, dynamic> fields,
  ) async {
    bulkUpdateCalls.add(_BulkUpdateCall(ids, fields));
    return ApiResponse(
      success: true,
      statusCode: 200,
      data: {
        'updated': ids.length,
        'results': [
          for (final id in ids) {'id': id, 'status': 'updated'},
        ],
      },
    );
  }

  @override
  Future<ApiResponse<Map<String, dynamic>>> bulkDelete(List<String> ids) async {
    bulkDeleteCalls.add(ids);
    return ApiResponse(
      success: true,
      statusCode: 200,
      data: {
        'deleted': ids.length,
        'results': [
          for (final id in ids) {'id': id, 'status': 'deleted'},
        ],
      },
    );
  }
}
