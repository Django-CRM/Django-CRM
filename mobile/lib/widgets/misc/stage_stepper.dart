import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';

/// Stage Stepper Widget
///
/// A deal's progress through its pipeline: the open stages then the won ones,
/// in board order. Lost stages are not steps; a lost deal shows a banner
/// instead, and tapping any step reopens it there. With more stages than fit
/// the width, the row scrolls sideways rather than squeezing each step below a
/// thumb's width.
class StageStepper extends StatelessWidget {
  /// Every stage of the deal's pipeline, in board order.
  final List<DealPipelineStage> stages;

  /// The deal's stage code.
  final String currentCode;
  final ValueChanged<DealPipelineStage>? onStageChange;

  const StageStepper({
    super.key,
    required this.stages,
    required this.currentCode,
    this.onStageChange,
  });

  static const double _minStepWidth = 64;

  @override
  Widget build(BuildContext context) {
    final steps = stages.where((s) => !s.isLost).toList();
    DealPipelineStage? lost;
    for (final s in stages) {
      if (s.isLost && s.code == currentCode) lost = s;
    }
    final currentIndex = steps.indexWhere((s) => s.code == currentCode);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        children: [
          if (lost != null)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              margin: const EdgeInsets.only(bottom: 12),
              decoration: BoxDecoration(
                color: AppColors.danger100,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Icon(
                    LucideIcons.circleX,
                    size: 16,
                    color: AppColors.danger600,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '${lost.label}. Tap a stage to reopen.',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.danger600,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          LayoutBuilder(
            builder: (context, constraints) {
              final fits =
                  steps.isEmpty ||
                  constraints.maxWidth / steps.length >= _minStepWidth;
              final row = Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final (i, stage) in steps.indexed)
                    _wrap(
                      fits,
                      _Step(
                        stage: stage,
                        number: i + 1,
                        completed: lost == null && i < currentIndex,
                        current: lost == null && i == currentIndex,
                        onTap:
                            onStageChange == null ||
                                (lost == null && i == currentIndex)
                            ? null
                            : () => onStageChange!(stage),
                      ),
                    ),
                ],
              );
              return fits
                  ? row
                  : SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: row,
                    );
            },
          ),
        ],
      ),
    );
  }

  Widget _wrap(bool fits, Widget step) => fits
      ? Expanded(child: step)
      : SizedBox(width: _minStepWidth, child: step);
}

class _Step extends StatelessWidget {
  const _Step({
    required this.stage,
    required this.number,
    required this.completed,
    required this.current,
    required this.onTap,
  });

  final DealPipelineStage stage;
  final int number;
  final bool completed;
  final bool current;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final filled = completed || current;
    final Widget mark = completed
        ? const Icon(LucideIcons.check, size: 16, color: Colors.white)
        : stage.isWon
        ? Icon(
            LucideIcons.trophy,
            size: 16,
            color: filled ? Colors.white : AppColors.gray400,
          )
        : Text(
            '$number',
            textScaler: TextScaler.noScaling,
            style: AppTypography.caption.copyWith(
              fontWeight: FontWeight.w600,
              color: filled ? Colors.white : AppColors.gray500,
            ),
          );
    return Semantics(
      button: onTap != null,
      label: stage.label,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 44),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 2),
            child: Column(
              children: [
                AnimatedContainer(
                  duration: AppDurations.normal,
                  width: 32,
                  height: 32,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: filled ? AppColors.primary600 : Colors.transparent,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: filled ? AppColors.primary600 : AppColors.gray300,
                      width: 2,
                    ),
                  ),
                  child: mark,
                ),
                const SizedBox(height: 6),
                Text(
                  stage.label,
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: AppTypography.caption.copyWith(
                    fontSize: 10,
                    color: current
                        ? AppColors.primary600
                        : completed
                        ? AppColors.textSecondary
                        : AppColors.textTertiary,
                    fontWeight: current ? FontWeight.w600 : FontWeight.normal,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
