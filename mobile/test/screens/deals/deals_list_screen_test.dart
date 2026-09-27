import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
import 'package:bottle_crm/data/models/deal_board.dart';
import 'package:bottle_crm/data/models/models.dart';
import 'package:bottle_crm/providers/auth_provider.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/providers/deals_provider.dart';
import 'package:bottle_crm/screens/deals/deals_list_screen.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('DealsListScreen', () {
    testWidgets('shows closed lost deals in list view', (tester) async {
      final dealsNotifier = _FakeDealsNotifier(
        DealsListData(deals: [_deal(stage: _lost)]),
      );

      await tester.pumpWidget(_testApp(dealsNotifier));
      await _switchToListView(tester);

      expect(find.text('Closed Lost'), findsOneWidget);
      expect(find.text('Closed Lost Deal'), findsOneWidget);
    });

    testWidgets('the board columns are the pipeline\'s stages, lost aside', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(_testApp(_FakeDealsNotifier(DealsListData())));
      await tester.pumpAndSettle();

      expect(find.byType(DragTarget<Deal>), findsNWidgets(3));
      expect(find.text('No deals in Demo booked'), findsOneWidget);
      expect(find.text('No deals in Signed'), findsOneWidget);
      expect(find.text('No deals in Closed Lost'), findsNothing);
    });

    testWidgets('with one pipeline there is no pipeline switcher', (
      tester,
    ) async {
      await tester.pumpWidget(_testApp(_FakeDealsNotifier(DealsListData())));
      await tester.pumpAndSettle();

      expect(find.text('Sales'), findsNothing);
    });

    testWidgets('with two pipelines the switcher picks the other one', (
      tester,
    ) async {
      final dealsNotifier = _FakeDealsNotifier(DealsListData());
      await tester.pumpWidget(
        _testApp(dealsNotifier, pipelines: [_pipeline, _partners]),
      );
      await tester.pumpAndSettle();

      expect(find.text('Sales'), findsOneWidget);
      final row = find.ancestor(
        of: find.text('Sales'),
        matching: find.byType(InkWell),
      );
      expect(tester.getSize(row.first).height, greaterThanOrEqualTo(44));

      await tester.tap(find.text('Sales'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Partners'));
      await tester.pumpAndSettle();

      expect(dealsNotifier.pipelineChoices, ['p2']);
      expect(find.text('No deals in Intro call'), findsOneWidget);
    });

    testWidgets('a drop on a custom stage column moves the deal by its code', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final dealsNotifier = _FakeDealsNotifier(DealsListData());

      await tester.pumpWidget(
        _testApp(dealsNotifier, board: _boardOf([_deal(stage: _prospecting)])),
      );
      await tester.pumpAndSettle();

      await _dragDealToStage(tester, 'Prospecting Deal', 1);

      expect(dealsNotifier.stageUpdates, [('deal-prospecting', 'DEMO_BOOKED')]);
      // The card changes column at once.
      expect(find.text('No deals in Prospecting'), findsOneWidget);
    });

    testWidgets('clears the selection after a successful move', (tester) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final dealsNotifier = _FakeDealsNotifier(DealsListData());

      await tester.pumpWidget(
        _testApp(dealsNotifier, board: _boardOf([_deal(stage: _prospecting)])),
      );
      await tester.pumpAndSettle();

      // Picking a card up selects it (LongPressDraggable.onDragStarted), so a
      // completed move must not leave the selection app bar stranded.
      await _dragDealToStage(tester, 'Prospecting Deal', 1);

      expect(dealsNotifier.stageUpdates, [('deal-prospecting', 'DEMO_BOOKED')]);
      expect(find.text('1 selected'), findsNothing);
    });

    testWidgets('keeps the selection when a move fails', (tester) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final dealsNotifier = _FakeDealsNotifier(
        DealsListData(),
        updateError: 'Network unreachable',
      );

      await tester.pumpWidget(
        _testApp(dealsNotifier, board: _boardOf([_deal(stage: _prospecting)])),
      );
      await tester.pumpAndSettle();

      await _dragDealToStage(tester, 'Prospecting Deal', 1);

      expect(find.text('1 selected'), findsOneWidget);
      // A refused move leaves the card where it was.
      expect(find.text('No deals in Prospecting'), findsNothing);
    });

    testWidgets('a card the server says may not move is not draggable', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final dealsNotifier = _FakeDealsNotifier(DealsListData());
      await tester.pumpWidget(
        _testApp(
          dealsNotifier,
          board: _boardOf(
            [
              _deal(stage: _prospecting),
              _deal(id: 'locked', title: 'Locked Deal', stage: _prospecting),
            ],
            locked: {'locked'},
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byType(LongPressDraggable<Deal>), findsOneWidget);
      expect(
        find.ancestor(
          of: find.text('Locked Deal'),
          matching: find.byType(LongPressDraggable<Deal>),
        ),
        findsNothing,
      );

      await _dragDealToStage(tester, 'Locked Deal', 1);

      expect(dealsNotifier.stageUpdates, isEmpty);
      expect(find.text('1 selected'), findsNothing);
      expect(find.text('Locked Deal'), findsOneWidget);
    });

    testWidgets(
      'a selection holding an unmovable card offers no stage change',
      (tester) async {
        tester.view.physicalSize = const Size(1600, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);

        await tester.pumpWidget(
          _testApp(
            _FakeDealsNotifier(DealsListData()),
            board: _boardOf(
              [
                _deal(stage: _prospecting),
                _deal(id: 'locked', title: 'Locked Deal', stage: _prospecting),
              ],
              locked: {'locked'},
            ),
          ),
        );
        await tester.pumpAndSettle();

        // A long press on a movable card starts a drag, which selects it.
        final gesture = await tester.startGesture(
          tester.getCenter(find.text('Prospecting Deal')),
        );
        await tester.pump(
          kLongPressTimeout + const Duration(milliseconds: 100),
        );
        await gesture.up();
        await tester.pumpAndSettle();
        expect(find.text('1 selected'), findsOneWidget);
        expect(find.byTooltip('Change stage'), findsOneWidget);

        await tester.tap(find.text('Locked Deal'));
        await tester.pumpAndSettle();
        expect(find.text('2 selected'), findsOneWidget);
        expect(find.byTooltip('Change stage'), findsNothing);
        expect(find.byTooltip('Delete'), findsOneWidget);
      },
    );

    testWidgets('a column past the server\'s cap shows its true count', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        _testApp(
          _FakeDealsNotifier(DealsListData()),
          board: _boardOf(
            [_deal(stage: _prospecting)],
            counts: {'PROSPECTING': 140},
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.text('Showing the first 1. Filter to see the rest.'),
        findsOneWidget,
      );
      // The column header and the Active figure both count the column's true
      // size, not the one card loaded.
      expect(find.text('Active'), findsOneWidget);
      expect(find.text('140'), findsNWidgets(2));
    });

    for (final scale in [1.0, 1.3]) {
      testWidgets('the board fits a 390px phone at text scale $scale', (
        tester,
      ) async {
        tester.view.devicePixelRatio = 3.0;
        tester.view.physicalSize = const Size(390 * 3, 844 * 3);
        tester.platformDispatcher.textScaleFactorTestValue = scale;
        addTearDown(tester.view.reset);
        addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);

        await tester.pumpWidget(
          _testApp(
            _FakeDealsNotifier(DealsListData()),
            board: _boardOf(
              [
                _deal(
                  title: 'A deal with a name long enough to need two lines',
                  stage: _prospecting,
                ),
                _deal(id: 'locked', title: 'Locked Deal', stage: _prospecting),
              ],
              locked: {'locked'},
              counts: {'PROSPECTING': 140},
            ),
          ),
        );
        await tester.pumpAndSettle();

        expect(tester.takeException(), isNull);
        expect(
          find.text('Showing the first 2. Filter to see the rest.'),
          findsOneWidget,
        );
        expect(find.byType(LongPressDraggable<Deal>), findsOneWidget);
      });
    }

    testWidgets('selecting in list view never reads the kanban endpoint', (
      tester,
    ) async {
      final board = _FakeBoard(_boardOf([_deal(stage: _prospecting)]));
      await tester.pumpWidget(
        _testApp(
          _FakeDealsNotifier(
            DealsListData(deals: [_deal(stage: _prospecting)]),
          ),
          boardNotifier: board,
        ),
      );
      await tester.pumpAndSettle();
      await _switchToListView(tester);

      // A list reload drops the board (DealsNotifier.refresh), which leaves it
      // to be read again by the next thing that watches it.
      ProviderScope.containerOf(
        tester.element(find.byType(DealsListScreen)),
      ).invalidate(dealBoardProvider);
      final before = board.fetches;

      await tester.longPress(find.text('Prospecting Deal'));
      await tester.pumpAndSettle();

      expect(find.text('1 selected'), findsOneWidget);
      // The list moves any deal the server lets it; the server still decides.
      expect(find.byTooltip('Change stage'), findsOneWidget);
      expect(board.fetches, before);
    });

    testWidgets('loads the next page when list view is scrolled near the end', (
      tester,
    ) async {
      final dealsNotifier = _FakeDealsNotifier(
        DealsListData(
          deals: List.generate(
            20,
            (index) => _deal(
              id: 'deal-$index',
              title: 'Prospecting Deal $index',
              stage: _prospecting,
            ),
          ),
          totalCount: 40,
          hasMore: true,
          currentOffset: 20,
        ),
      );

      await tester.pumpWidget(_testApp(dealsNotifier));
      await _switchToListView(tester);

      await tester.drag(find.byType(ListView), const Offset(0, -3000));
      await tester.pumpAndSettle();

      expect(dealsNotifier.loadMoreCalls, 1);
    });
  });
}

/// A default pipeline with a custom open stage and a renamed won stage, so
/// nothing here passes by matching the six seeded codes.
final _pipeline = DealPipeline.fromJson({
  'id': 'p1',
  'name': 'Sales',
  'is_default': true,
  'stages': [
    {'id': 's1', 'code': 'PROSPECTING', 'label': 'Prospecting', 'order': 0},
    {
      'id': 's2',
      'code': 'DEMO_BOOKED',
      'label': 'Demo booked',
      'order': 1,
      'kind': 'open',
      'expected_days': 7,
    },
    {
      'id': 's3',
      'code': 'SIGNED',
      'label': 'Signed',
      'order': 2,
      'kind': 'won',
    },
    {
      'id': 's4',
      'code': 'CLOSED_LOST',
      'label': 'Closed Lost',
      'order': 3,
      'kind': 'lost',
    },
  ],
});

final _partners = DealPipeline.fromJson({
  'id': 'p2',
  'name': 'Partners',
  'stages': [
    {'id': 't1', 'code': 'INTRO_CALL', 'label': 'Intro call', 'kind': 'open'},
    {'id': 't2', 'code': 'WON', 'label': 'Won', 'kind': 'won', 'order': 1},
    {'id': 't3', 'code': 'LOST', 'label': 'Lost', 'kind': 'lost', 'order': 2},
  ],
});

final _prospecting = _pipeline.stages[0];
final _lost = _pipeline.stages[3];

Widget _testApp(
  _FakeDealsNotifier dealsNotifier, {
  List<DealPipeline>? pipelines,
  DealBoard board = const DealBoard(),
  _FakeBoard? boardNotifier,
}) {
  return ProviderScope(
    overrides: [
      authProvider.overrideWith(() => _FakeAuthNotifier()),
      dealsProvider.overrideWith(() => dealsNotifier),
      dealBoardProvider.overrideWith(() => boardNotifier ?? _FakeBoard(board)),
      dealPipelinesProvider.overrideWith(
        () => _FakePipelines(pipelines ?? [_pipeline]),
      ),
    ],
    child: MaterialApp(theme: AppTheme.light, home: const DealsListScreen()),
  );
}

Future<void> _switchToListView(WidgetTester tester) async {
  await tester.tap(find.byType(IconButton).at(1));
  await tester.pumpAndSettle();
}

Future<void> _dragDealToStage(
  WidgetTester tester,
  String dealTitle,
  int column,
) async {
  final dealCenter = tester.getCenter(find.text(dealTitle));
  final targetRect = tester.getRect(find.byType(DragTarget<Deal>).at(column));
  final targetPoint = Offset(targetRect.left + 48, targetRect.top + 120);

  final gesture = await tester.startGesture(dealCenter);
  await tester.pump(kLongPressTimeout + const Duration(milliseconds: 100));
  await gesture.moveTo(targetPoint);
  await tester.pump();
  await gesture.up();
  await tester.pumpAndSettle();
}

Deal _deal({String? id, String? title, required DealPipelineStage stage}) {
  final suffix = stage.label.replaceAll(' ', '-').toLowerCase();
  return Deal(
    id: id ?? 'deal-$suffix',
    title: title ?? '${stage.label} Deal',
    value: 100000,
    pipelineId: 'p1',
    stage: stage.code,
    stageLabel: stage.label,
    stageKind: stage.kind,
    probability: dealStageProbability(stage.code, stage.kind),
    closeDate: DateTime(2026, 6, 1),
    companyName: 'Acme Inc',
    assignedTo: 'user-1',
    priority: Priority.medium,
    createdAt: DateTime(2026, 5, 1),
    updatedAt: DateTime(2026, 5, 1),
  );
}

/// The board the kanban endpoint would send for [deals]: one column per
/// stage they sit in. Every card may move unless [locked] names it; [counts]
/// overrides a column's `item_count`, as the server does past its cap.
DealBoard _boardOf(
  List<Deal> deals, {
  Set<String> locked = const {},
  Map<String, int> counts = const {},
}) {
  final codes = {for (final d in deals) d.stage};
  return DealBoard(
    columns: [
      for (final code in codes)
        DealBoardColumn(
          code: code,
          itemCount: counts[code] ?? deals.where((d) => d.stage == code).length,
          cards: [
            for (final d in deals.where((d) => d.stage == code))
              DealBoardCard(deal: d, canMove: !locked.contains(d.id)),
          ],
        ),
    ],
  );
}

class _FakeBoard extends DealBoardNotifier {
  _FakeBoard(this.board);

  final DealBoard board;
  int fetches = 0;

  @override
  Future<DealBoard> fetch() async {
    fetches += 1;
    return board;
  }
}

class _FakeDealsNotifier extends DealsNotifier {
  _FakeDealsNotifier(this.initialData, {this.updateError});

  final DealsListData initialData;
  final String? updateError;
  final List<(String id, String code)> stageUpdates = [];
  final List<String> pipelineChoices = [];
  int loadMoreCalls = 0;

  @override
  String? get pipelineId =>
      pipelineChoices.isEmpty ? null : pipelineChoices.last;

  @override
  Future<void> setPipeline(String id) async => pipelineChoices.add(id);

  @override
  Future<DealsListData> build() async => initialData;

  @override
  Future<void> refresh({String? search, String? stage}) async {}

  @override
  Future<void> loadMore({String? search, String? stage}) async {
    loadMoreCalls += 1;
  }

  @override
  Future<({String? error, bool success})> updateDealStage(
    String id,
    DealPipelineStage stage,
  ) async {
    stageUpdates.add((id, stage.code));
    if (updateError != null) {
      return (success: false, error: updateError);
    }
    return (success: true, error: null);
  }
}

class _FakePipelines extends DealPipelinesNotifier {
  _FakePipelines(this.pipelines);

  final List<DealPipeline> pipelines;

  @override
  Future<List<DealPipeline>> build() async => pipelines;
}

class _FakeAuthNotifier extends AuthNotifier {
  @override
  AuthState build() {
    const org = Organization(
      id: 'org-1',
      name: 'Test Org',
      currencySymbol: r'$',
    );

    return AuthState(
      user: const AuthUser(id: 'user-1', email: 'user@example.com'),
      organizations: [org],
      selectedOrganization: org,
      isAuthenticated: true,
    );
  }
}
