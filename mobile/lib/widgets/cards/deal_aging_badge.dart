import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal.dart';

/// The words on a deal's aging badge, or null when there is no badge.
///
/// Read from the server's `aging_status`, never worked out here: the
/// thresholds are per stage and per pipeline. `yellow` is past the stage's
/// expected (or warning) days, `red` is rotting. `green`, a closed stage and a
/// missing status show nothing. Worded as the web board words it.
String? dealAgingLabel(Deal deal) {
  final days = deal.daysInStageServer ?? deal.daysInCurrentStage;
  final suffix = days == null ? '' : ' · ${days}d';
  return switch (deal.agingStatus) {
    'yellow' => 'Past expected$suffix',
    'red' => 'Stalled$suffix',
    _ => null,
  };
}

/// A small pill for a deal that has sat too long in its stage: warning colours
/// when past expected, danger colours when stalled. Renders nothing otherwise.
class DealAgingBadge extends StatelessWidget {
  const DealAgingBadge({super.key, required this.deal});

  final Deal deal;

  @override
  Widget build(BuildContext context) {
    final label = dealAgingLabel(deal);
    if (label == null) return const SizedBox.shrink();
    final stalled = deal.agingStatus == 'red';
    final color = stalled ? AppColors.danger600 : AppColors.warning700;
    final bg = stalled ? AppColors.danger50 : AppColors.warning50;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            stalled ? LucideIcons.octagonAlert : LucideIcons.clock,
            size: 11,
            color: color,
          ),
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTypography.caption.copyWith(
                color: color,
                fontSize: 10,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
