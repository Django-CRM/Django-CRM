import 'package:bottle_crm/providers/duplicates_provider.dart';
import 'package:bottle_crm/screens/duplicates/merge_compare_screen.dart';
import 'package:bottle_crm/widgets/duplicates/duplicate_notice.dart';
import 'package:bottle_crm/widgets/duplicates/duplicates_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The G19 screens at a real phone width, at a scaled-up text size, and the
/// merge they lead to.
///
/// An overflow is a thrown FlutterError, so `takeException()` being null is
/// the layout assertion. The API is faked: what it may refuse is pinned by the
/// backend's own tests, and what the client sends by the provider's.
class _FakeApi extends DuplicatesApi {
  _FakeApi({
    this.hits = const [],
    this.record = const RecordDuplicates(),
    this.currentCanDelete = true,
    this.otherCanDelete = true,
    this.mergeFailure,
    this.sameName = false,
  });

  /// Both records called "Ann Lee", the usual shape of a duplicate.
  final bool sameName;

  final List<DuplicateHit> hits;
  final RecordDuplicates record;
  final bool currentCanDelete;
  final bool otherCanDelete;
  final String? mergeFailure;

  final List<Map<String, String>> checks = [];
  final List<(String, String)> merges = [];

  @override
  Future<List<DuplicateHit>> check(
    DuplicateModule module,
    Map<String, String> criteria,
  ) async {
    checks.add(criteria);
    return hits;
  }

  @override
  Future<RecordDuplicates> forRecord(DuplicateModule module, String id) async =>
      record;

  @override
  Future<MergeSide> side(DuplicateModule module, String id) async => MergeSide(
    id: id,
    name: id == 'c1' || sameName
        ? 'Ann Lee'
        : 'Annabel Lee-Whitcombe of Accounts Payable',
    canDelete: id == 'c1' ? currentCanDelete : otherCanDelete,
    fields: [
      ('Name', id == 'c1' ? 'Ann Lee' : 'Annabel Lee-Whitcombe'),
      ('Email', id == 'c1' ? '' : 'annabel.lee-whitcombe@example.com'),
      ('Phone', id == 'c1' ? '202 555 0147' : ''),
    ],
  );

  @override
  Future<String?> merge(
    DuplicateModule module,
    String keeperId,
    String loserId,
  ) async {
    merges.add((keeperId, loserId));
    return mergeFailure;
  }
}

const _hit = DuplicateHit(
  id: 'c2',
  name: 'Annabel Lee-Whitcombe of Accounts Payable',
  matchedOn: ['email', 'phone', 'name'],
  canDelete: true,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester, {double textScale = 1.0}) {
    tester.view.devicePixelRatio = 3.0;
    tester.view.physicalSize = const Size(390 * 3, 844 * 3);
    tester.platformDispatcher.textScaleFactorTestValue = textScale;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
  }

  Future<void> pumpRouted(
    WidgetTester tester,
    _FakeApi api,
    Widget home,
  ) async {
    final router = GoRouter(
      routes: [
        GoRoute(
          path: '/',
          builder: (_, _) => Scaffold(body: home),
        ),
        GoRoute(
          path: '/contacts/:id',
          builder: (_, state) =>
              Scaffold(body: Text('Opened ${state.pathParameters['id']}')),
        ),
        GoRoute(
          path: '/merge/:module/:id',
          builder: (_, state) =>
              Text('Compare with ${state.uri.queryParameters['with']}'),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [duplicatesApiProvider.overrideWithValue(api)],
        child: MaterialApp.router(routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('the create-form notice', () {
    for (final scale in [1.0, 1.3]) {
      testWidgets('waits for a pause, then lists hits at text scale $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        final api = _FakeApi(hits: const [_hit]);
        final email = TextEditingController();
        final phone = TextEditingController();
        addTearDown(email.dispose);
        addTearDown(phone.dispose);
        await pumpRouted(
          tester,
          api,
          SingleChildScrollView(
            child: DuplicateNotice(
              module: DuplicateModule.contacts,
              fields: {'email': email, 'phone': phone},
            ),
          ),
        );

        email.text = 'a';
        email.text = 'annabel@example.com';
        phone.text = '12'; // too few digits to send
        await tester.pump(const Duration(milliseconds: 100));
        expect(api.checks, isEmpty);

        await tester.pump(const Duration(milliseconds: 600));
        await tester.pump();
        expect(api.checks, [
          {'email': 'annabel@example.com'},
        ]);
        expect(find.text('Possible duplicate'), findsOneWidget);
        expect(tester.takeException(), isNull);

        await tester.tap(find.textContaining('Annabel Lee-Whitcombe'));
        await tester.pumpAndSettle();
        expect(find.text('Opened c2'), findsOneWidget);
      });
    }

    testWidgets('says nothing when there are no hits', (tester) async {
      usePhone(tester);
      final email = TextEditingController(text: '');
      addTearDown(email.dispose);
      await pumpRouted(
        tester,
        _FakeApi(),
        DuplicateNotice(
          module: DuplicateModule.leads,
          fields: {'email': email},
        ),
      );
      email.text = 'nobody@example.com';
      await tester.pump(const Duration(milliseconds: 600));
      await tester.pump();
      expect(find.textContaining('Possible duplicate'), findsNothing);
    });
  });

  group('the detail panel', () {
    for (final scale in [1.0, 1.3]) {
      testWidgets('offers Compare when a merge is possible, scale $scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        await pumpRouted(
          tester,
          _FakeApi(record: const RecordDuplicates(hits: [_hit])),
          const DuplicatesPanel(
            module: DuplicateModule.contacts,
            recordId: 'c1',
          ),
        );
        expect(tester.takeException(), isNull);
        expect(find.text('POSSIBLE DUPLICATE'), findsOneWidget);
        expect(find.text('Same email, phone and name'), findsOneWidget);

        await tester.tap(find.text('Compare'));
        await tester.pumpAndSettle();
        expect(find.text('Compare with c2'), findsOneWidget);
      });
    }

    testWidgets('explains instead of offering a merge nobody here may make', (
      tester,
    ) async {
      usePhone(tester);
      await pumpRouted(
        tester,
        _FakeApi(
          record: const RecordDuplicates(
            hits: [
              DuplicateHit(id: 'c2', name: 'Other', matchedOn: ['name']),
            ],
          ),
        ),
        const DuplicatesPanel(module: DuplicateModule.contacts, recordId: 'c1'),
      );
      expect(find.text('Compare'), findsNothing);
      expect(find.textContaining('Only an admin'), findsOneWidget);
    });

    testWidgets('is not there at all without hits', (tester) async {
      usePhone(tester);
      await pumpRouted(
        tester,
        _FakeApi(),
        const DuplicatesPanel(module: DuplicateModule.contacts, recordId: 'c1'),
      );
      expect(find.textContaining('POSSIBLE'), findsNothing);
    });
  });

  group('the compare screen', () {
    Future<void> pumpCompare(WidgetTester tester, _FakeApi api) => pumpRouted(
      tester,
      api,
      const MergeCompareScreen(
        module: DuplicateModule.contacts,
        recordId: 'c1',
        otherId: 'c2',
      ),
    );

    for (final scale in [1.0, 1.3]) {
      testWidgets('fits a 390px phone at text scale $scale', (tester) async {
        usePhone(tester, textScale: scale);
        await pumpCompare(tester, _FakeApi());
        expect(tester.takeException(), isNull);
        expect(find.text('Keep Ann Lee'), findsOneWidget);
        expect(find.text('AFTER THE MERGE'), findsOneWidget);
      });
    }

    testWidgets('merges the other record into the one kept, after a confirm', (
      tester,
    ) async {
      usePhone(tester);
      final api = _FakeApi();
      await pumpCompare(tester, api);

      // The kept record's blank email takes the other's.
      expect(
        find.text('annabel.lee-whitcombe@example.com'),
        findsNWidgets(2), // the other card, and the result
      );

      await tester.scrollUntilVisible(find.text('Merge into Ann Lee'), 200);
      await tester.ensureVisible(find.text('Merge into Ann Lee'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Merge into Ann Lee'));
      await tester.pumpAndSettle();
      expect(api.merges, isEmpty);
      expect(find.textContaining('cannot be undone'), findsOneWidget);

      await tester.tap(find.text('Merge and delete'));
      await tester.pumpAndSettle();
      expect(api.merges, [('c1', 'c2')]);
      expect(find.text('Opened c1'), findsOneWidget);
    });

    testWidgets('choosing the other record swaps keeper and loser', (
      tester,
    ) async {
      usePhone(tester);
      final api = _FakeApi();
      await pumpCompare(tester, api);

      await tester.tap(find.textContaining('Keep Annabel'));
      await tester.pumpAndSettle();
      final button = find.textContaining('Merge into Annabel');
      await tester.scrollUntilVisible(button, 200);
      await tester.ensureVisible(button);
      await tester.pumpAndSettle();
      await tester.tap(button);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Merge and delete'));
      await tester.pumpAndSettle();
      expect(api.merges, [('c2', 'c1')]);
    });

    testWidgets('a record whose partner cannot be deleted cannot be kept', (
      tester,
    ) async {
      usePhone(tester);
      await pumpCompare(tester, _FakeApi(otherCanDelete: false));
      expect(
        find.textContaining('Keeping this one would delete the other'),
        findsOneWidget,
      );
      // So the one the caller may delete is the one kept by default.
      await tester.scrollUntilVisible(
        find.textContaining('Merge into Annabel'),
        200,
      );
      expect(find.textContaining('Merge into Annabel'), findsOneWidget);
    });

    testWidgets('neither deletable: no merge is offered at all', (
      tester,
    ) async {
      usePhone(tester);
      await pumpCompare(
        tester,
        _FakeApi(currentCanDelete: false, otherCanDelete: false),
      );
      expect(find.textContaining('Merge into'), findsNothing);
      expect(find.textContaining('You cannot merge these two'), findsOneWidget);
    });

    for (final scale in [1.0, 1.3]) {
      testWidgets('two records with one name are told apart, x$scale', (
        tester,
      ) async {
        usePhone(tester, textScale: scale);
        final api = _FakeApi(sameName: true);
        await pumpCompare(tester, api);
        expect(tester.takeException(), isNull);

        // Not "Keep Ann Lee" twice.
        expect(find.text('Keep this record'), findsOneWidget);
        expect(find.text('Keep the other record'), findsOneWidget);
        expect(find.textContaining('Keep Ann Lee'), findsNothing);
        await tester.scrollUntilVisible(
          find.textContaining(
            'every link to the other record move to this record',
          ),
          200,
        );

        final merge = find.text('Merge into this record');
        await tester.scrollUntilVisible(merge, 200);
        await tester.ensureVisible(merge);
        await tester.tap(merge);
        await tester.pumpAndSettle();
        expect(find.text('Delete the other record?'), findsOneWidget);
        expect(tester.takeException(), isNull);

        await tester.tap(find.text('Merge and delete'));
        await tester.pumpAndSettle();
        expect(api.merges, [('c1', 'c2')]);
      });
    }

    testWidgets("a refused merge stays on the screen with the API's reason", (
      tester,
    ) async {
      usePhone(tester);
      await pumpCompare(tester, _FakeApi(mergeFailure: 'Not yours to delete.'));
      await tester.scrollUntilVisible(find.text('Merge into Ann Lee'), 200);
      await tester.ensureVisible(find.text('Merge into Ann Lee'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Merge into Ann Lee'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Merge and delete'));
      await tester.pumpAndSettle();
      expect(
        find.text('Nothing was merged. Not yours to delete.'),
        findsOneWidget,
      );
      expect(find.text('Opened c1'), findsNothing);
    });
  });
}
