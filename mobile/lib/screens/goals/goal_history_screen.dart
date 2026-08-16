import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal.dart' show Currency;
import '../../data/models/sales_goal.dart';
import '../../providers/goals_provider.dart';
import '../../providers/settings_provider.dart';
import 'goals_screen.dart' show formatGoalValue;

/// How did we do, one finished period at a time.
///
/// The goals screen answers "how are we doing"; nothing answered this. A closed
/// period cannot move any more, so the interesting number is not pace but
/// whether the number was made, and by how many people.
///
/// A card is a period AND a goal type, which is how the API groups them:
/// pooling a revenue target in currency with a deals-closed target in deals
/// produced a single meaningless figure, so each card carries one unit.
///
/// Reading is open to any member and narrowed server-side to their own goals
/// and their teams', the same rule the list and the board use, so nothing here
/// gates on role.
class GoalHistoryScreen extends ConsumerWidget {
  const GoalHistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(goalHistoryProvider);
    final symbol =
        ref.watch(orgSettingsProvider).value?.currencySymbol ??
        Currency.usd.symbol;

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Goal history'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) =>
            _HistoryError(onRetry: () => ref.invalidate(goalHistoryProvider)),
        data: (periods) {
          if (periods.isEmpty) return const _HistoryEmpty();
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(goalHistoryProvider),
            child: ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 32),
              children: [
                for (final period in periods)
                  _PeriodCard(period: period, symbol: symbol),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  child: Text(
                    'The twelve most recent periods you can see. Attainment is '
                    'recomputed from the closed-won deals of each period, so it '
                    'matches what the goals screen showed while the period was '
                    'running.',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _PeriodCard extends StatelessWidget {
  const _PeriodCard({required this.period, required this.symbol});

  final GoalHistoryPeriod period;
  final String symbol;

  Color get _colour {
    if (period.percent >= 100) return AppColors.success600;
    if (period.percent >= 80) return AppColors.warning600;
    return AppColors.danger600;
  }

  @override
  Widget build(BuildContext context) {
    final goalWord = period.goalsCount == 1 ? 'goal' : 'goals';

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: Colors.grey.shade200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      goalDateRange(period.periodStart, period.periodEnd),
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 14,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '${goalPeriodLabel(period.periodType)} · '
                      '${goalTypeLabel(period.goalType)} · '
                      '${period.attainedCount} of ${period.goalsCount} '
                      '$goalWord met',
                      style: TextStyle(
                        fontSize: 12,
                        color: Colors.grey.shade600,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: _colour.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  '${period.percent}% of target',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: _colour,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              // `percent` is uncapped so an over-attaining period reports what
              // it did; a bar cannot be more than full, and the figure above
              // carries the overshoot.
              value: (period.percent / 100).clamp(0.0, 1.0),
              minHeight: 6,
              backgroundColor: Colors.grey.shade200,
              valueColor: AlwaysStoppedAnimation(_colour),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            '${formatGoalValue(period.achieved, goalType: period.goalType, symbol: symbol)}'
            ' of '
            '${formatGoalValue(period.target, goalType: period.goalType, symbol: symbol)}',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          ),
          if (period.goals.isNotEmpty) ...[
            const SizedBox(height: 12),
            Divider(height: 1, color: Colors.grey.shade200),
            for (final goal in period.goals)
              _GoalOutcome(goal: goal, symbol: symbol),
          ],
        ],
      ),
    );
  }
}

class _GoalOutcome extends StatelessWidget {
  const _GoalOutcome({required this.goal, required this.symbol});

  final SalesGoal goal;
  final String symbol;

  @override
  Widget build(BuildContext context) {
    final met = goal.targetValue > 0 && goal.progressValue >= goal.targetValue;

    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  goal.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  '${goal.targetLabel} · '
                  '${formatGoalValue(goal.progressValue, goalType: goal.goalType, symbol: symbol)}'
                  ' / '
                  '${formatGoalValue(goal.targetValue, goalType: goal.goalType, symbol: symbol)}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: met
                  ? AppColors.success600.withValues(alpha: 0.12)
                  : Colors.grey.shade100,
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              met ? 'Met' : 'Missed',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: met ? AppColors.success600 : Colors.grey.shade700,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoryEmpty extends StatelessWidget {
  const _HistoryEmpty();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(LucideIcons.history, size: 40, color: Colors.grey.shade400),
            const SizedBox(height: 12),
            const Text(
              'No finished periods yet',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            Text(
              // Says which set is empty. The endpoint narrows a member to their
              // own goals and their teams', so "nothing has finished yet" would
              // be a claim about the org that a member cannot actually see.
              'Once a goal\'s period ends it moves here with what it attained. '
              'Goals still running are on the goals screen.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
            ),
          ],
        ),
      ),
    );
  }
}

class _HistoryError extends StatelessWidget {
  const _HistoryError({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.triangleAlert,
              size: 36,
              color: Colors.grey.shade400,
            ),
            const SizedBox(height: 12),
            const Text('Could not load goal history'),
            const SizedBox(height: 12),
            FilledButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
