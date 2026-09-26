import 'package:bottle_crm/core/theme/theme.dart';
import 'package:bottle_crm/data/models/auth_response.dart';
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

      final dealsNotifier = _FakeDealsNotifier(
        DealsListData(deals: [_deal(stage: _prospecting)]),
      );

      await tester.pumpWidget(_testApp(dealsNotifier));
      await tester.pumpAndSettle();

      await _dragDealToStage(tester, 'Prospecting Deal', 1);

      expect(dealsNotifier.stageUpdates, [('deal-prospecting', 'DEMO_BOOKED')]);
    });

    testWidgets('clears the selection after a successful move', (tester) async {
      tester.view.physicalSize = const Size(1600, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final dealsNotifier = _FakeDealsNotifier(
        DealsListData(deals: [_deal(stage: _prospecting)]),
      );

      await tester.pumpWidget(_testApp(dealsNotifier));
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
        DealsListData(deals: [_deal(stage: _prospecting)]),
        updateError: 'Network unreachable',
      );

      await tester.pumpWidget(_testApp(dealsNotifier));
      await tester.pumpAndSettle();

      await _dragDealToStage(tester, 'Prospecting Deal', 1);

      expect(find.text('1 selected'), findsOneWidget);
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
}) {
  return ProviderScope(
    overrides: [
      authProvider.overrideWith(() => _FakeAuthNotifier()),
      dealsProvider.overrideWith(() => dealsNotifier),
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
