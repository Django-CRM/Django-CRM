import 'package:bottle_crm/data/models/dashboard_data.dart';
import 'package:bottle_crm/data/models/deal.dart';
import 'package:bottle_crm/data/models/deal_pipeline.dart';
import 'package:bottle_crm/providers/deal_pipelines_provider.dart';
import 'package:bottle_crm/services/api_service.dart';
import 'package:bottle_crm/widgets/cards/deal_aging_badge.dart';
import 'package:flutter_test/flutter_test.dart';

/// Deal stages come from the org's pipelines. What a deal's stage means is its
/// `stage_kind`; the seeded codes only stand in when a server is too old to
/// say.
void main() {
  Map<String, dynamic> dealJson(Map<String, dynamic> extra) => {
    'id': 'd1',
    'name': 'Deal',
    ...extra,
  };

  group('Deal.fromJson', () {
    test('reads pipeline, label and kind as sent', () {
      final deal = Deal.fromJson(
        dealJson({
          'pipeline': 'p1',
          'stage': 'SIGNED',
          'stage_label': 'Signed',
          'stage_kind': 'won',
        }),
      );

      expect(deal.pipelineId, 'p1');
      expect(deal.stage, 'SIGNED');
      expect(deal.stageLabel, 'Signed');
      expect(deal.isWon, isTrue);
      expect(deal.isClosed, isTrue);
    });

    test(
      'a custom code named like a closed one is only what its kind says',
      () {
        final deal = Deal.fromJson(
          dealJson({'stage': 'CLOSED_WON', 'stage_kind': 'open'}),
        );

        expect(deal.isClosed, isFalse);
      },
    );

    test('with no stage_kind, the seeded codes keep their meaning', () {
      final won = Deal.fromJson(dealJson({'stage': 'CLOSED_WON'}));
      final lost = Deal.fromJson(dealJson({'stage': 'CLOSED_LOST'}));
      final open = Deal.fromJson(dealJson({'stage': 'NEGOTIATION'}));

      expect(won.isWon, isTrue);
      expect(won.stageLabel, 'Closed Won');
      expect(lost.isLost, isTrue);
      expect(open.isClosed, isFalse);
      expect(open.pipelineId, isNull);
    });

    test('a null stage_kind falls back the same way', () {
      final deal = Deal.fromJson(
        dealJson({'stage': 'CLOSED_LOST', 'stage_kind': null}),
      );

      expect(deal.isLost, isTrue);
    });

    test('an unknown code with no label is made readable', () {
      final deal = Deal.fromJson(dealJson({'stage': 'DEMO_BOOKED'}));

      expect(deal.stageLabel, 'Demo Booked');
      expect(deal.isClosed, isFalse);
    });

    test('toJson sends pipeline and stage only when known', () {
      final known = Deal.fromJson(
        dealJson({'pipeline': 'p1', 'stage': 'DEMO', 'stage_kind': 'open'}),
      ).toJson();
      final fresh = Deal.fromJson(dealJson({})).toJson();

      expect(known['pipeline'], 'p1');
      expect(known['stage'], 'DEMO');
      expect(fresh.containsKey('pipeline'), isFalse);
      expect(fresh.containsKey('stage'), isFalse);
    });
  });

  group('DealPipeline.fromJson', () {
    final pipeline = DealPipeline.fromJson({
      'id': 'p1',
      'name': 'Sales',
      'is_default': true,
      'stages': [
        {
          'id': 's3',
          'code': 'LOST',
          'label': 'Lost',
          'order': 3,
          'kind': 'lost',
        },
        {'id': 's1', 'code': 'NEW', 'label': 'New', 'order': 0, 'kind': 'open'},
        {
          'id': 's2',
          'code': 'DEMO',
          'label': 'Demo',
          'order': 1,
          'kind': 'open',
          'expected_days': 10,
          'warning_days': 7,
        },
        {'id': 's4', 'code': 'WON', 'label': 'Won', 'order': 2, 'kind': 'won'},
      ],
    });

    test('puts the stages in board order', () {
      expect(pipeline.stages.map((s) => s.code), [
        'NEW',
        'DEMO',
        'WON',
        'LOST',
      ]);
      expect(pipeline.isDefault, isTrue);
    });

    test('finds the first open and first lost stage', () {
      expect(pipeline.firstOpenStage?.code, 'NEW');
      expect(pipeline.firstLostStage?.code, 'LOST');
      expect(pipeline.stageByCode('DEMO')?.expectedDays, 10);
      expect(pipeline.stageByCode('NOPE'), isNull);
    });

    test('ages like the server: warning first, stalled at 1.5x rounded up', () {
      expect(pipeline.stageByCode('DEMO')!.agingThresholds, (7, 15));
      expect(pipeline.stageByCode('NEW')!.agingThresholds, isNull);
      expect(pipeline.stageByCode('WON')!.agingThresholds, isNull);
      const late = DealPipelineStage(
        id: 'x',
        code: 'X',
        label: 'X',
        expectedDays: 5,
        warningDays: 9,
      );
      expect(late.agingThresholds, (5, 8));
    });

    test('colours closed stages by kind and open ones by position', () {
      expect(
        pipeline.colorOf(pipeline.stageByCode('WON')!),
        dealStageColor(dealStageWon, 0),
      );
      expect(
        pipeline.colorOf(pipeline.stageByCode('DEMO')!),
        dealStageColor(dealStageOpen, 1),
      );
    });
  });

  group('choosing a pipeline', () {
    final a = DealPipeline.fromJson({'id': 'a', 'name': 'A'});
    final b = DealPipeline.fromJson({
      'id': 'b',
      'name': 'B',
      'is_default': true,
    });

    test('the chosen one if it exists, else the default', () {
      expect(activeDealPipeline([a, b], 'a')?.id, 'a');
      expect(activeDealPipeline([a, b], 'gone')?.id, 'b');
      expect(activeDealPipeline([a, b], null)?.id, 'b');
      expect(activeDealPipeline([], null), isNull);
    });

    test('a deal is only ever in its own pipeline', () {
      expect(dealPipelineOf([a, b], 'a')?.id, 'a');
      expect(dealPipelineOf([a, b], 'gone'), isNull);
      expect(dealPipelineOf([a, b], null)?.id, 'b');
    });

    test('the built-in pipeline has no id to send', () {
      expect(DealPipeline.legacy.id, isEmpty);
      expect(DealPipeline.legacy.firstOpenStage?.code, 'PROSPECTING');
      expect(DealPipeline.legacy.stageByCode('CLOSED_WON')?.isWon, isTrue);
    });
  });

  test('stage probability mirrors the server', () {
    expect(dealStageProbability('ANYTHING', dealStageWon), 100);
    expect(dealStageProbability('PROPOSAL', dealStageLost), 0);
    expect(dealStageProbability('PROPOSAL', dealStageOpen), 50);
    expect(dealStageProbability('DEMO', dealStageOpen), 0);
  });

  group('dealCloseProblem', () {
    test('a won stage needs an amount, whatever it is called', () {
      final problem = dealCloseProblem(
        kind: dealStageWon,
        stageLabel: 'Signed',
        amount: null,
        closeDate: DateTime(2026, 9, 1),
      );
      expect(problem?.field, 'amount');
      expect(problem?.message, 'A deal in Signed needs an amount.');
    });

    test('a won or lost stage needs a close date', () {
      for (final kind in [dealStageWon, dealStageLost]) {
        final problem = dealCloseProblem(
          kind: kind,
          stageLabel: 'Done',
          amount: 10,
          closeDate: null,
        );
        expect(problem?.field, 'closed_on', reason: kind);
      }
    });

    test('an open stage needs neither, and a complete close passes', () {
      expect(
        dealCloseProblem(
          kind: dealStageOpen,
          stageLabel: 'Demo',
          amount: null,
          closeDate: null,
        ),
        isNull,
      );
      expect(
        dealCloseProblem(
          kind: dealStageWon,
          stageLabel: 'Won',
          amount: 5,
          closeDate: DateTime(2026, 9, 1),
        ),
        isNull,
      );
    });
  });

  group('stage drafts', () {
    test('a label is required and days must be 1 to 3650', () {
      String? check(String label, String expected) => dealStageDraftProblem(
        label: label,
        kind: dealStageOpen,
        expectedDays: expected,
        warningDays: '',
      );
      expect(check('  ', ''), 'Give the stage a name.');
      expect(check('Demo', '0'), isNotNull);
      expect(check('Demo', '3651'), isNotNull);
      expect(check('Demo', '3650'), isNull);
      expect(check('Demo', ''), isNull);
    });

    test('a closed stage ignores its day boxes and sends null for both', () {
      expect(
        dealStageDraftProblem(
          label: 'Won',
          kind: dealStageWon,
          expectedDays: '99999',
          warningDays: 'x',
        ),
        isNull,
      );
      expect(
        dealStagePayload(
          label: ' Won ',
          kind: dealStageWon,
          expectedDays: '12',
          warningDays: '3',
        ),
        {
          'label': 'Won',
          'kind': 'won',
          'expected_days': null,
          'warning_days': null,
        },
      );
    });

    test('an empty day box is sent as null, never ages', () {
      expect(
        dealStagePayload(
          label: 'Demo',
          kind: dealStageOpen,
          expectedDays: '14',
          warningDays: '',
        ),
        {
          'label': 'Demo',
          'kind': 'open',
          'expected_days': 14,
          'warning_days': null,
        },
      );
    });
  });

  group('the aging badge', () {
    Deal aged(String? status, int? days) => Deal.fromJson(
      dealJson({
        'stage': 'DEMO',
        'aging_status': status,
        'days_in_stage': days,
      }),
    );

    test('yellow reads past expected, red stalled, with the days', () {
      expect(dealAgingLabel(aged('yellow', 12)), 'Past expected · 12d');
      expect(dealAgingLabel(aged('red', 30)), 'Stalled · 30d');
    });

    test('green, missing and unknown statuses show nothing', () {
      expect(dealAgingLabel(aged('green', 3)), isNull);
      expect(dealAgingLabel(aged(null, 3)), isNull);
      expect(dealAgingLabel(aged('rotten', 3)), isNull);
    });
  });

  group('the dashboard pipeline', () {
    test('reads kind and order from the server', () {
      final data = DashboardData.fromJson({
        'pipeline_by_stage': {
          'SIGNED': {'label': 'Signed', 'kind': 'won', 'order': 2, 'count': 1},
          'DEMO': {'label': 'Demo', 'kind': 'open', 'order': 1, 'count': 2},
          'NEW': {'label': 'New', 'kind': 'open', 'order': 0, 'count': 4},
        },
      });

      expect(data.pipelineByStage.map((s) => s.code), [
        'NEW',
        'DEMO',
        'SIGNED',
      ]);
      expect(data.pipelineByStage.last.isOpen, isFalse);
    });

    test('an older server still sorts and closes the seeded codes', () {
      final stage = PipelineStage.fromJson('CLOSED_LOST', {'count': 1});
      final first = PipelineStage.fromJson('PROSPECTING', {'count': 1});

      expect(stage.isOpen, isFalse);
      expect(stage.label, 'Closed Lost');
      expect(first.order, lessThan(stage.order));
    });
  });

  group('dealPipelineMessage', () {
    ApiResponse<Map<String, dynamic>> refused(
      int status, {
      Map<String, dynamic>? data,
      String? message,
    }) => ApiResponse(
      success: false,
      statusCode: status,
      data: data,
      message: message,
    );

    test('a refusal sentence or the first field message', () {
      expect(
        dealPipelineMessage(
          refused(400, data: {'error': true, 'errors': 'Still holds deals.'}),
        ),
        'Still holds deals.',
      );
      expect(
        dealPipelineMessage(
          refused(
            400,
            data: {
              'errors': {
                'label': ['Taken.'],
              },
            },
          ),
        ),
        'Taken.',
      );
    });

    test('a 404 says so, and a bodiless failure keeps its message', () {
      expect(dealPipelineMessage(refused(404)), contains('no longer exists'));
      expect(
        dealPipelineMessage(
          refused(403, message: 'Only admins can change deal pipelines.'),
        ),
        'Only admins can change deal pipelines.',
      );
    });
  });
}
