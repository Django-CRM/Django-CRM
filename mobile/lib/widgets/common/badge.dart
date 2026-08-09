import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';

/// Status Badge - Displays lead/deal status
class StatusBadge extends StatelessWidget {
  final String label;
  final Color color;
  final bool filled;

  const StatusBadge({
    super.key,
    required this.label,
    required this.color,
    this.filled = true,
  });

  factory StatusBadge.fromLeadStatus(LeadStatus status) {
    return StatusBadge(label: status.label, color: status.color);
  }

  factory StatusBadge.fromDealStage(DealStage stage) {
    return StatusBadge(label: stage.shortLabel, color: stage.color);
  }

  factory StatusBadge.fromTicketStatus(TicketStatus status) {
    return StatusBadge(label: status.label, color: status.color);
  }

  factory StatusBadge.fromTicketType(TicketType type) {
    return StatusBadge(label: type.label, color: AppColors.gray500);
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: filled ? color.withValues(alpha: 0.1) : Colors.transparent,
        borderRadius: BorderRadius.circular(4),
        border: filled ? null : Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(
        label,
        style: AppTypography.caption.copyWith(
          color: color,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Priority Badge - Shows priority level
class PriorityBadge extends StatelessWidget {
  final String label;
  final Color color;
  final IconData icon;
  final bool showLabel;
  final bool compact;

  const PriorityBadge({
    super.key,
    required this.label,
    required this.color,
    required this.icon,
    this.showLabel = true,
    this.compact = false,
  });

  /// Backward-compat constructor; existing callers pass `priority:` directly.
  factory PriorityBadge.fromPriority(
    Priority priority, {
    bool showLabel = true,
    bool compact = false,
  }) {
    return PriorityBadge(
      label: priority.label,
      color: priority.color,
      icon: _priorityIcon(priority),
      showLabel: showLabel,
      compact: compact,
    );
  }

  factory PriorityBadge.fromTicketPriority(
    TicketPriority priority, {
    bool showLabel = true,
    bool compact = false,
  }) {
    return PriorityBadge(
      label: priority.label,
      color: priority.color,
      icon: _ticketPriorityIcon(priority),
      showLabel: showLabel,
      compact: compact,
    );
  }

  static IconData _priorityIcon(Priority p) {
    switch (p) {
      case Priority.urgent:
        return LucideIcons.chevronsUp;
      case Priority.high:
        return LucideIcons.arrowUp;
      case Priority.medium:
        return LucideIcons.minus;
      case Priority.low:
        return LucideIcons.arrowDown;
    }
  }

  static IconData _ticketPriorityIcon(TicketPriority p) {
    switch (p) {
      case TicketPriority.urgent:
        return LucideIcons.chevronsUp;
      case TicketPriority.high:
        return LucideIcons.arrowUp;
      case TicketPriority.normal:
        return LucideIcons.minus;
      case TicketPriority.low:
        return LucideIcons.arrowDown;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (compact) {
      return Container(
        width: 20,
        height: 20,
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Icon(icon, size: 12, color: color),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: color),
          if (showLabel) ...[
            const SizedBox(width: 3),
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: color,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Label Pill - Tag/label display
class LabelPill extends StatelessWidget {
  final String label;
  final Color? color;
  final VoidCallback? onRemove;
  final bool outlined;

  const LabelPill({
    super.key,
    required this.label,
    this.color,
    this.onRemove,
    this.outlined = false,
  });

  @override
  Widget build(BuildContext context) {
    final pillColor = color ?? AppColors.primary500;

    return Container(
      padding: EdgeInsets.only(
        left: 8,
        right: onRemove != null ? 4 : 8,
        top: 3,
        bottom: 3,
      ),
      decoration: BoxDecoration(
        color: outlined ? Colors.transparent : pillColor.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(4),
        border: outlined
            ? Border.all(color: pillColor.withValues(alpha: 0.3))
            : null,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: AppTypography.caption.copyWith(
              color: pillColor,
              fontWeight: FontWeight.w500,
            ),
          ),
          if (onRemove != null) ...[
            const SizedBox(width: 3),
            GestureDetector(
              onTap: onRemove,
              child: Container(
                padding: const EdgeInsets.all(2),
                decoration: BoxDecoration(
                  color: pillColor.withValues(alpha: 0.2),
                  shape: BoxShape.circle,
                ),
                child: Icon(LucideIcons.x, size: 10, color: pillColor),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Count Badge - Small notification count
class CountBadge extends StatelessWidget {
  final int count;
  final Color? color;
  final bool showZero;

  const CountBadge({
    super.key,
    required this.count,
    this.color,
    this.showZero = false,
  });

  @override
  Widget build(BuildContext context) {
    if (count == 0 && !showZero) {
      return const SizedBox.shrink();
    }

    final displayText = count > 99 ? '99+' : count.toString();
    final badgeColor = color ?? AppColors.danger500;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      constraints: const BoxConstraints(minWidth: 20),
      decoration: BoxDecoration(
        color: badgeColor,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        displayText,
        textAlign: TextAlign.center,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 11,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Source Badge - Lead source indicator
class SourceBadge extends StatelessWidget {
  final LeadSource source;

  const SourceBadge({super.key, required this.source});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.gray100,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(source.icon, size: 12, color: AppColors.gray500),
          const SizedBox(width: 4),
          Text(
            source.label,
            style: AppTypography.labelSmall.copyWith(color: AppColors.gray600),
          ),
        ],
      ),
    );
  }
}
