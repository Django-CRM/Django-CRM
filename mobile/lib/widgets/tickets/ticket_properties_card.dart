import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/ticket.dart';

/// Sidebar-style card showing ticket SLA status, escalation count, and
/// watcher count. The same surface the web app shows in its right rail.
///
/// Deadlines and breach flags come from the backend so this view stays in
/// sync with whatever the SLA calculator (business hours, paused windows)
/// decided. Pause icon appears when `slaPausedAt != null`.
class TicketPropertiesCard extends StatelessWidget {
  final Ticket ticket;
  final int? watcherCount;
  final bool isWatching;

  const TicketPropertiesCard({
    super.key,
    required this.ticket,
    this.watcherCount,
    this.isWatching = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'PROPERTIES',
            style: AppTypography.overline.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          _slaRow(
            label: 'First response',
            deadline: ticket.firstResponseSlaDeadline,
            met: ticket.firstResponseAt != null,
            metAt: ticket.firstResponseAt,
            breached:
                ticket.isFirstResponseSlaBreached ||
                ticket.isFirstResponseSlaBreachedFromApi,
            atRisk: ticket.isFirstResponseSlaAtRisk,
            paused: ticket.slaPausedAt != null,
          ),
          const Divider(height: 20),
          _slaRow(
            label: 'Resolution',
            deadline: ticket.resolutionSlaDeadline,
            met: ticket.resolvedAt != null,
            metAt: ticket.resolvedAt,
            breached: ticket.isResolutionSlaBreachedFromApi,
            atRisk: ticket.isResolutionSlaAtRisk,
            paused: ticket.slaPausedAt != null,
          ),
          if (ticket.escalationCount > 0) ...[
            const Divider(height: 20),
            _iconRow(
              icon: LucideIcons.alertTriangle,
              iconColor: AppColors.danger600,
              label: 'Escalated',
              trailing: '${ticket.escalationCount}x',
              trailingColor: AppColors.danger600,
            ),
          ],
          if (watcherCount != null) ...[
            const Divider(height: 20),
            _iconRow(
              icon: isWatching ? LucideIcons.eye : LucideIcons.eyeOff,
              iconColor: isWatching
                  ? AppColors.primary600
                  : AppColors.textSecondary,
              label: isWatching ? 'You are watching' : 'Watchers',
              trailing: '$watcherCount',
              trailingColor: AppColors.textPrimary,
            ),
          ],
        ],
      ),
    );
  }

  Widget _slaRow({
    required String label,
    required DateTime? deadline,
    required bool met,
    required DateTime? metAt,
    required bool breached,
    required bool atRisk,
    required bool paused,
  }) {
    final IconData icon;
    final Color iconColor;
    final String stateText;
    final Color stateColor;

    if (met) {
      icon = LucideIcons.checkCircle;
      iconColor = AppColors.success600;
      stateText = 'Met';
      stateColor = AppColors.success600;
    } else if (breached) {
      icon = LucideIcons.alertCircle;
      iconColor = AppColors.danger600;
      stateText = deadline != null ? 'Breached' : 'Overdue';
      stateColor = AppColors.danger600;
    } else if (paused) {
      icon = LucideIcons.pauseCircle;
      iconColor = AppColors.warning600;
      stateText = 'Paused';
      stateColor = AppColors.warning600;
    } else if (deadline != null) {
      final remaining = deadline.difference(DateTime.now());
      if (remaining.isNegative) {
        icon = LucideIcons.clock;
        iconColor = AppColors.danger600;
        stateText = 'Overdue';
        stateColor = AppColors.danger600;
      } else if (atRisk) {
        // The amber band. Still counts down rather than switching to a word,
        // because how long is left is the thing worth acting on.
        icon = LucideIcons.alertTriangle;
        iconColor = AppColors.warning600;
        stateText = 'in ${_formatDuration(remaining)}';
        stateColor = AppColors.warning600;
      } else {
        icon = LucideIcons.clock;
        iconColor = AppColors.textSecondary;
        stateText = 'in ${_formatDuration(remaining)}';
        stateColor = AppColors.textPrimary;
      }
    } else {
      icon = LucideIcons.minusCircle;
      iconColor = AppColors.gray400;
      stateText = 'Not set';
      stateColor = AppColors.textSecondary;
    }

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 18, color: iconColor),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: AppTypography.label.copyWith(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                _subtitle(deadline: deadline, met: met, metAt: metAt),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
        Text(
          stateText,
          style: AppTypography.caption.copyWith(
            color: stateColor,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }

  String _subtitle({
    required DateTime? deadline,
    required bool met,
    required DateTime? metAt,
  }) {
    if (met && metAt != null) return 'Replied ${_formatDate(metAt)}';
    if (deadline != null) return 'Due ${_formatDate(deadline)}';
    return 'No SLA configured';
  }

  Widget _iconRow({
    required IconData icon,
    required Color iconColor,
    required String label,
    required String trailing,
    required Color trailingColor,
  }) {
    return Row(
      children: [
        Icon(icon, size: 18, color: iconColor),
        const SizedBox(width: 10),
        Expanded(child: Text(label, style: AppTypography.label)),
        Text(
          trailing,
          style: AppTypography.label.copyWith(
            color: trailingColor,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }

  String _formatDuration(Duration d) {
    if (d.inDays > 0) return '${d.inDays}d ${d.inHours.remainder(24)}h';
    if (d.inHours > 0) return '${d.inHours}h ${d.inMinutes.remainder(60)}m';
    return '${d.inMinutes}m';
  }

  String _formatDate(DateTime t) {
    const months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    final hh = t.hour.toString().padLeft(2, '0');
    final mm = t.minute.toString().padLeft(2, '0');
    return '${months[t.month - 1]} ${t.day}, $hh:$mm';
  }
}
