import 'package:bottle_crm/data/models/sales_goal.dart';
import 'package:bottle_crm/providers/goals_provider.dart';
import 'package:bottle_crm/screens/goals/goals_screen.dart';
import 'package:flutter_test/flutter_test.dart';

SalesGoal goal({
  String id = 'g1',
  String name = 'Goal',
  String status = 'on_track',
  bool isActive = true,
  double target = 1000,
  double progress = 100,
  int percent = 10,
  String periodEnd = '2026-12-31',
  String? assignedToId,
  String? assignedToName,
  String? teamId,
  String? teamName,
  String goalType = 'REVENUE',
}) => SalesGoal(
  id: id,
  name: name,
  goalType: goalType,
  targetValue: target,
  periodType: 'MONTHLY',
  periodStart: '2026-01-01',
  periodEnd: periodEnd,
  assignedToId: assignedToId,
  assignedToName: assignedToName,
  teamId: teamId,
  teamName: teamName,
  isActive: isActive,
  progressValue: progress,
  progressPercent: percent,
  status: status,
);

void main() {
  group('SalesGoal.fromJson', () {
    test('flattens the nested assignee and team the API sends', () {
      final parsed = SalesGoal.fromJson({
        'id': 'g1',
        'name': 'Q1 revenue',
        'goal_type': 'REVENUE',
        'target_value': '50000.00',
        'period_type': 'QUARTERLY',
        'period_start': '2026-01-01',
        'period_end': '2026-03-31',
        'assigned_to': 'p1',
        'assigned_to_detail': {
          'id': 'p1',
          'user_details': {'name': 'Ada Lovelace', 'email': 'ada@example.com'},
        },
        'team': null,
        'team_detail': null,
        'is_active': true,
        'progress_value': 20000,
        'progress_percent': 40,
        'status': 'behind',
      });

      expect(parsed.assignedToId, 'p1');
      expect(parsed.assignedToName, 'Ada Lovelace');
      // `target_value` is a DecimalField, so it arrives as a string.
      expect(parsed.targetValue, 50000);
      expect(parsed.progressPercent, 40);
    });

    test('falls back to the email when a person has no name', () {
      final parsed = SalesGoal.fromJson({
        'id': 'g1',
        'assigned_to_detail': {
          'id': 'p1',
          'user_details': {'name': '', 'email': 'ada@example.com'},
        },
      });
      expect(parsed.assignedToName, 'ada@example.com');
    });

    test('survives a payload with nothing in it', () {
      final parsed = SalesGoal.fromJson(const {});
      expect(parsed.id, '');
      expect(parsed.targetValue, 0);
      expect(parsed.assignedToName, isNull);
    });
  });

  group('targetLabel', () {
    test('names the person, then the team, then the org', () {
      expect(
        goal(assignedToId: 'p1', assignedToName: 'Ada').targetLabel,
        'Ada',
      );
      expect(
        goal(teamId: 't1', teamName: 'Support').targetLabel,
        'Support (team)',
      );
      expect(goal().targetLabel, 'Whole organisation');
    });

    test('prefers the person when the API sends both', () {
      // The API permits both FKs. Neither client can write that state, but a
      // row created by curl can arrive in it, and the label must pick one.
      final both = goal(
        assignedToId: 'p1',
        assignedToName: 'Ada',
        teamId: 't1',
        teamName: 'Support',
      );
      expect(both.targetLabel, 'Ada');
    });
  });

  group('goalTotals', () {
    test('sums the active goals and counts every one', () {
      final totals = goalTotals([
        goal(id: 'a', target: 100, progress: 50),
        goal(id: 'b', target: 200, progress: 20),
        goal(id: 'c', target: 999, progress: 999, isActive: false),
      ], today: '2026-06-01');

      expect(totals.count, 3);
      expect(totals.active, 2);
      // The retired goal's 999 is in neither sum.
      expect(totals.target, 300);
      expect(totals.achieved, 70);
    });

    test('counts a goal ending today as still behind pace', () {
      // The boundary the web got wrong: it compared `period_end` as a UTC
      // instant against the clock, so a goal dropped out of this count part-way
      // through its own final day. A goal ending today is one somebody can
      // still act on.
      final totals = goalTotals([
        goal(status: 'behind', periodEnd: '2026-06-01'),
      ], today: '2026-06-01');
      expect(totals.behind, 1);
    });

    test('leaves out a goal whose period has already ended', () {
      final totals = goalTotals([
        goal(status: 'behind', periodEnd: '2026-05-31'),
      ], today: '2026-06-01');
      expect(totals.behind, 0);
    });

    test('leaves out a retired goal even when it is behind and open', () {
      final totals = goalTotals([
        goal(status: 'behind', periodEnd: '2026-12-31', isActive: false),
      ], today: '2026-06-01');
      expect(totals.behind, 0);
    });

    test('is all zeroes for no goals rather than throwing', () {
      final totals = goalTotals(const [], today: '2026-06-01');
      expect(totals.count, 0);
      expect(totals.target, 0);
    });
  });

  group('goalToday', () {
    test('is the local date, zero-padded', () {
      expect(goalToday(DateTime(2026, 1, 5)), '2026-01-05');
      expect(goalToday(DateTime(2026, 12, 31)), '2026-12-31');
    });

    test('does not shift the day for a late-evening local time', () {
      // `toIso8601String()` on a UTC conversion would hand back the next day
      // here for anywhere west of Greenwich, and the previous one east of it.
      // These are date-only fields, so a day is the whole quantity.
      expect(goalToday(DateTime(2026, 6, 1, 23, 59)), '2026-06-01');
      expect(goalToday(DateTime(2026, 6, 1, 0, 1)), '2026-06-01');
    });
  });

  group('sortGoalsByUrgency', () {
    test('puts active before retired, whatever the status', () {
      final sorted = sortGoalsByUrgency([
        goal(id: 'retired', status: 'behind', isActive: false),
        goal(id: 'active', status: 'completed'),
      ]);
      expect(sorted.map((g) => g.id), ['active', 'retired']);
    });

    test('orders behind, at risk, on track, then met', () {
      final sorted = sortGoalsByUrgency([
        goal(id: 'done', status: 'completed'),
        goal(id: 'ok', status: 'on_track'),
        goal(id: 'bad', status: 'behind'),
        goal(id: 'risky', status: 'at_risk'),
      ]);
      expect(sorted.map((g) => g.id), ['bad', 'risky', 'ok', 'done']);
    });

    test('breaks a tie on progress, furthest along first', () {
      final sorted = sortGoalsByUrgency([
        goal(id: 'low', status: 'behind', percent: 10),
        goal(id: 'high', status: 'behind', percent: 40),
      ]);
      expect(sorted.map((g) => g.id), ['high', 'low']);
    });

    test('does not mutate what it was given', () {
      final input = [
        goal(id: 'done', status: 'completed'),
        goal(id: 'bad', status: 'behind'),
      ];
      sortGoalsByUrgency(input);
      expect(input.map((g) => g.id), ['done', 'bad']);
    });
  });

  group('validateGoalForm', () {
    String? check({
      String name = 'Goal',
      String target = '100',
      String start = '2026-01-01',
      String end = '2026-03-31',
    }) => validateGoalForm(
      name: name,
      targetValue: target,
      periodStart: start,
      periodEnd: end,
    );

    test('accepts a filled-in form', () {
      expect(check(), isNull);
    });

    test('refuses a blank name', () {
      expect(check(name: '   '), isNotNull);
    });

    test('refuses a target that is not a number', () {
      expect(check(target: 'lots'), contains('number'));
    });

    test('refuses zero and negative targets, matching the serializer', () {
      // `SalesGoalCreateSerializer.validate` rejects `target_value <= 0`.
      expect(check(target: '0'), contains('greater than 0'));
      expect(check(target: '-5'), contains('greater than 0'));
    });

    test('refuses an end date on or before the start', () {
      // The serializer rejects `period_end <= period_start`, so equal dates are
      // refused too and the message must not imply a one-day goal is possible.
      expect(check(start: '2026-03-31', end: '2026-01-01'), isNotNull);
      expect(check(start: '2026-01-01', end: '2026-01-01'), isNotNull);
    });

    test('refuses a missing date rather than sending a blank one', () {
      expect(check(start: ''), isNotNull);
      expect(check(end: ''), isNotNull);
    });
  });

  group('goalTargetFields', () {
    test('sets exactly one FK and explicitly nulls the other', () {
      // Both keys are always present. PUT is partial, so omitting one keeps the
      // old value, and switching a goal from a person to a team would leave it
      // assigned to both.
      expect(goalTargetFields('profile:p1'), {
        'assigned_to': 'p1',
        'team': null,
      });
      expect(goalTargetFields('team:t1'), {'assigned_to': null, 'team': 't1'});
    });

    test('clears both for a whole-org goal, and for anything unrecognised', () {
      expect(goalTargetFields('org'), {'assigned_to': null, 'team': null});
      expect(goalTargetFields(null), {'assigned_to': null, 'team': null});
      expect(goalTargetFields('nonsense'), {'assigned_to': null, 'team': null});
    });
  });

  group('goalTargetValue', () {
    test('round-trips through goalTargetFields', () {
      expect(goalTargetFields(goalTargetValue(goal(assignedToId: 'p1'))), {
        'assigned_to': 'p1',
        'team': null,
      });
      expect(goalTargetFields(goalTargetValue(goal(teamId: 't1'))), {
        'assigned_to': null,
        'team': 't1',
      });
      expect(goalTargetFields(goalTargetValue(goal())), {
        'assigned_to': null,
        'team': null,
      });
    });

    test('reads an empty string FK as no target, not as an id', () {
      expect(goalTargetValue(goal(assignedToId: '', teamId: '')), 'org');
    });
  });

  group('GoalLeaderRow.fromJson', () {
    test('reads the name the endpoint now sends', () {
      final row = GoalLeaderRow.fromJson({
        'rank': 1,
        'goal_id': 'g1',
        'goal_name': 'Q1',
        'user': {'id': 'p1', 'name': 'Ada Lovelace'},
        'target': 100.0,
        'achieved': 104.0,
        'percent': 104,
      });
      expect(row.user, 'Ada Lovelace');
      // Uncapped, unlike SalesGoal.progressPercent. 104% is the point of a board.
      expect(row.percent, 104);
    });

    test('says Unknown rather than blank when the user block is missing', () {
      expect(GoalLeaderRow.fromJson(const {'rank': 1}).user, 'Unknown');
    });
  });

  group('labels', () {
    test('spell each status for a person', () {
      expect(goalStatusLabel('behind'), 'Behind pace');
      expect(goalStatusLabel('at_risk'), 'At risk');
      expect(goalStatusLabel('on_track'), 'On track');
      expect(goalStatusLabel('completed'), 'Target met');
    });

    test('pass an unrecognised status through rather than blanking it', () {
      expect(goalStatusLabel('surprise'), 'surprise');
    });

    test('cover every type and period the backend accepts', () {
      for (final type in goalTypes) {
        expect(goalTypeLabel(type), isNot(type));
      }
      for (final period in goalPeriodTypes) {
        expect(goalPeriodLabel(period), isNotEmpty);
      }
    });
  });

  _activitiesAndWeights();
  _dateRangeTests();
  _filterTests();
}

void _activitiesAndWeights() {
  group('activity goals', () {
    test('offers ACTIVITIES as a goal type the backend accepts', () {
      expect(goalTypes, contains('ACTIVITIES'));
    });

    test('labels an activities goal', () {
      expect(goalTypeLabel('ACTIVITIES'), 'Activities');
    });

    test('counts activities rather than formatting them as money', () {
      // `isMoney` used to read `goalType != 'DEALS_CLOSED'`, so a third goal
      // type would have printed "$40" for a quota of forty logged activities.
      expect(goal(goalType: 'ACTIVITIES').isMoney, isFalse);
      expect(goal(goalType: 'DEALS_CLOSED').isMoney, isFalse);
      expect(goal(goalType: 'REVENUE').isMoney, isTrue);
    });
  });

  group('deal type weights', () {
    test('names the five deal types a weight can be set against', () {
      expect(dealTypes, [
        'NEW_BUSINESS',
        'EXISTING_BUSINESS',
        'RENEWAL',
        'UPSELL',
        'CROSS_SELL',
      ]);
    });

    test('reads the stored weight map off the payload', () {
      final parsed = SalesGoal.fromJson({
        'id': 'g1',
        'name': 'Weighted',
        'goal_type': 'REVENUE',
        'target_value': '100',
        'type_weights': {'RENEWAL': 0.5},
      });

      expect(parsed.typeWeights, {'RENEWAL': 0.5});
    });

    test('reports an unweighted goal as an empty map, never null', () {
      final parsed = SalesGoal.fromJson({'id': 'g1', 'name': 'Plain'});

      expect(parsed.typeWeights, isEmpty);
    });

    test('counts only the types actually re-weighed', () {
      expect(goal().weightedTypeCount, 0);
      expect(
        SalesGoal.fromJson({
          'id': 'g',
          'name': 'n',
          // A weight of exactly 1 is the default, so it is not an adjustment.
          'type_weights': {'RENEWAL': 0.5, 'UPSELL': 1},
        }).weightedTypeCount,
        1,
      );
    });
  });

  group('formatGoalValue', () {
    test('counts deals as deals', () {
      expect(
        formatGoalValue(3, goalType: 'DEALS_CLOSED', symbol: r'$'),
        '3 deals',
      );
      expect(
        formatGoalValue(1, goalType: 'DEALS_CLOSED', symbol: r'$'),
        '1 deal',
      );
    });

    test('counts activities as activities, not as deals', () {
      // The unit used to hang off `isMoney`, so anything that was not revenue
      // was printed as deals: a quota of forty logged activities read "40
      // deals".
      expect(
        formatGoalValue(40, goalType: 'ACTIVITIES', symbol: r'$'),
        '40 activities',
      );
      expect(
        formatGoalValue(1, goalType: 'ACTIVITIES', symbol: r'$'),
        '1 activity',
      );
    });

    test('prices revenue in the org currency', () {
      expect(
        formatGoalValue(120000, goalType: 'REVENUE', symbol: r'$'),
        contains(r'$'),
      );
    });
  });

  group('GoalHistoryPeriod', () {
    test('reads a finished period and keeps its goal type', () {
      final period = GoalHistoryPeriod.fromJson({
        'period_start': '2026-01-01',
        'period_end': '2026-01-31',
        'period_type': 'MONTHLY',
        'goal_type': 'REVENUE',
        'goals_count': 2,
        'attained_count': 1,
        'target': 300,
        'achieved': 240,
        'percent': 80,
        'goals': [
          {'id': 'g1', 'name': 'Jan', 'goal_type': 'REVENUE'},
        ],
      });

      expect(period.goalType, 'REVENUE');
      expect(period.percent, 80);
      expect(period.attainedCount, 1);
      expect(period.goals.single.name, 'Jan');
      expect(period.isMoney, isTrue);
    });

    test('does not format a deals period as money', () {
      final period = GoalHistoryPeriod.fromJson({
        'goal_type': 'DEALS_CLOSED',
        'target': 56,
        'achieved': 4,
        'percent': 7,
      });

      expect(period.isMoney, isFalse);
    });
  });
}

void _dateRangeTests() {
  group('goalDateRange', () {
    test('prints a period as two short dates', () {
      expect(goalDateRange('2026-05-01', '2026-05-31'), '1 May - 31 May');
    });

    test('hands back a malformed value rather than inventing a date', () {
      expect(
        goalDateRange('not-a-date', '2026-05-31'),
        'not-a-date to 2026-05-31',
      );
    });
  });
}

void _filterTests() {
  group('goalListQuery', () {
    test('asks for everything when nothing is filtered', () {
      final query = goalListQuery(const GoalFilters());
      expect(query, contains('limit=1000'));
      expect(query, isNot(contains('search=')));
      expect(query, isNot(contains('period_type=')));
    });

    test('sends the search text and the period', () {
      final query = goalListQuery(
        const GoalFilters(query: 'emea', periodType: 'QUARTERLY'),
      );
      expect(query, contains('search=emea'));
      expect(query, contains('period_type=QUARTERLY'));
    });

    test('encodes search text rather than pasting it into the URL', () {
      final query = goalListQuery(const GoalFilters(query: 'a&b c'));
      expect(query, isNot(contains('a&b c')));
      expect(query, contains('search=a%26b+c'));
    });

    test('maps the window onto the flag the API actually takes', () {
      expect(
        goalListQuery(const GoalFilters(window: 'current')),
        contains('current=true'),
      );
      expect(
        goalListQuery(const GoalFilters(window: 'active')),
        contains('active=true'),
      );
    });

    test('drops a period the backend does not accept', () {
      // The value reaches this from a picker today, but a filter that silently
      // forwarded anything would turn a future typo into an empty list with no
      // explanation.
      expect(
        goalListQuery(const GoalFilters(periodType: 'FORTNIGHTLY')),
        isNot(contains('period_type')),
      );
    });

    test('knows when a filter is on, so the empty state can say so', () {
      expect(const GoalFilters().isFiltered, isFalse);
      expect(const GoalFilters(query: 'x').isFiltered, isTrue);
      expect(const GoalFilters(window: 'current').isFiltered, isTrue);
      expect(const GoalFilters(periodType: 'MONTHLY').isFiltered, isTrue);
    });
  });
}
