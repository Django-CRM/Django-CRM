import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/ticket.dart';
import '../common/avatar.dart';

/// Ticket Card, list item for the tickets list.
/// Flat card aesthetic per mobile/DESIGN_SYSTEM.md (no shadow, grey-200 border).
class TicketCard extends StatelessWidget {
  final Ticket ticketItem;
  final VoidCallback? onTap;

  const TicketCard({super.key, required this.ticketItem, this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppLayout.borderRadiusMd,
          border: Border.all(color: AppColors.border),
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: AppLayout.borderRadiusMd,
          child: InkWell(
            onTap: onTap,
            borderRadius: AppLayout.borderRadiusMd,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHeader(),
                  if (ticketItem.tags.isNotEmpty || _hasRelationshipPill())
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: _buildTagAndRelationshipRow(),
                    ),
                  const SizedBox(height: 8),
                  _buildFooter(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  bool _hasRelationshipPill() =>
      ticketItem.parentSummary != null ||
      ticketItem.childCount > 0 ||
      ticketItem.isProblem;

  Widget _buildHeader() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Priority dot
        Container(
          margin: const EdgeInsets.only(top: 5, right: 8),
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: ticketItem.priority.color,
            shape: BoxShape.circle,
          ),
        ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                ticketItem.name,
                style: AppTypography.label.copyWith(
                  fontWeight: FontWeight.w600,
                ),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              if (ticketItem.accountName != null &&
                  ticketItem.accountName!.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    ticketItem.accountName!,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(width: 8),
        // Status pill
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(
            color: ticketItem.status.color.withValues(alpha: 0.1),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(
            ticketItem.status.label,
            style: AppTypography.caption.copyWith(
              color: ticketItem.status.color,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    );
  }

  /// Tag chips (first 2, "+N" overflow) and relationship pills on the same
  /// horizontal line so a card with both doesn't grow tall.
  Widget _buildTagAndRelationshipRow() {
    final pills = <Widget>[];

    if (ticketItem.isProblem) {
      pills.add(
        _pill(
          icon: LucideIcons.alertOctagon,
          label: 'Problem',
          bg: AppColors.warning100,
          fg: AppColors.warning700,
        ),
      );
    }
    if (ticketItem.parentSummary != null) {
      pills.add(
        _pill(
          icon: LucideIcons.cornerDownRight,
          label: 'Sub-ticket',
          bg: AppColors.primary100,
          fg: AppColors.primary700,
        ),
      );
    }
    if (ticketItem.childCount > 0) {
      pills.add(
        _pill(
          icon: LucideIcons.gitBranch,
          label:
              '${ticketItem.childCount} '
              'child${ticketItem.childCount == 1 ? '' : 'ren'}',
          bg: AppColors.gray100,
          fg: AppColors.gray700,
        ),
      );
    }

    const maxTags = 2;
    final shownTags = ticketItem.tags.take(maxTags).toList();
    final overflow = ticketItem.tags.length - shownTags.length;
    for (final tag in shownTags) {
      pills.add(
        _pill(label: tag, bg: AppColors.gray100, fg: AppColors.gray700),
      );
    }
    if (overflow > 0) {
      pills.add(
        _pill(
          label: '+$overflow',
          bg: AppColors.gray100,
          fg: AppColors.gray600,
        ),
      );
    }

    return Wrap(spacing: 4, runSpacing: 4, children: pills);
  }

  Widget _pill({
    IconData? icon,
    required String label,
    required Color bg,
    required Color fg,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(3),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 10, color: fg),
            const SizedBox(width: 3),
          ],
          Text(
            label,
            style: AppTypography.caption.copyWith(
              color: fg,
              fontWeight: FontWeight.w500,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFooter() {
    return Row(
      children: [
        Icon(
          ticketItem.ticketType.icon,
          size: 12,
          color: AppColors.textSecondary,
        ),
        const SizedBox(width: 3),
        Text(
          ticketItem.ticketType.label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(width: 8),
        Text(
          '·',
          style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
        ),
        const SizedBox(width: 8),
        Text(
          ticketItem.priority.label,
          style: AppTypography.caption.copyWith(
            color: ticketItem.priority.color,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(width: 10),
        Text(
          _formatTimeAgo(ticketItem.createdAt),
          style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
        ),
        const Spacer(),
        _buildAssignees(),
        if (ticketItem.isFirstResponseSlaBreached ||
            ticketItem.isResolutionSlaBreached) ...[
          const SizedBox(width: 6),
          _buildSlaChip(),
        ] else if (ticketItem.isSlaAtRisk) ...[
          const SizedBox(width: 6),
          _buildAtRiskChip(),
        ],
      ],
    );
  }

  /// First assignee avatar plus a "+N" overflow circle. Empty if unassigned.
  Widget _buildAssignees() {
    if (ticketItem.assignedTo.isEmpty) return const SizedBox.shrink();
    final first = ticketItem.assignedTo.first;
    final email =
        (first['user_details']?['email'] as String?) ??
        (first['email'] as String?) ??
        '';
    final name = email.isNotEmpty ? email.split('@').first : 'User';
    final extra = ticketItem.assignedTo.length - 1;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        UserAvatar(name: name, size: AvatarSize.xs),
        if (extra > 0) ...[
          const SizedBox(width: 4),
          Container(
            height: 20,
            padding: const EdgeInsets.symmetric(horizontal: 5),
            decoration: BoxDecoration(
              color: AppColors.gray100,
              borderRadius: BorderRadius.circular(10),
            ),
            alignment: Alignment.center,
            child: Text(
              '+$extra',
              style: AppTypography.caption.copyWith(
                color: AppColors.gray700,
                fontWeight: FontWeight.w600,
                fontSize: 10,
              ),
            ),
          ),
        ],
      ],
    );
  }

  /// The amber step below [_buildSlaChip]: due soon, not yet missed. Only ever
  /// rendered when no target has breached, so a card wears one chip or none.
  Widget _buildAtRiskChip() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.warning100,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(LucideIcons.clock, size: 11, color: AppColors.warning600),
          const SizedBox(width: 3),
          Text(
            'Due soon',
            style: AppTypography.caption.copyWith(
              color: AppColors.warning600,
              fontWeight: FontWeight.w600,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSlaChip() {
    // Distinguish first-response vs resolution breaches with a tooltip-y
    // label rather than a separate chip. Keeps the card compact.
    final isResolution =
        ticketItem.isResolutionSlaBreached &&
        !ticketItem.isFirstResponseSlaBreached;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: AppColors.danger100,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(LucideIcons.alertTriangle, size: 11, color: AppColors.danger600),
          const SizedBox(width: 3),
          Text(
            isResolution ? 'SLA · Resolve' : 'SLA',
            style: AppTypography.caption.copyWith(
              color: AppColors.danger600,
              fontWeight: FontWeight.w600,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }

  String _formatTimeAgo(DateTime dateTime) {
    final now = DateTime.now();
    final difference = now.difference(dateTime);
    if (difference.inDays > 30) {
      return '${(difference.inDays / 30).floor()}mo ago';
    } else if (difference.inDays > 0) {
      return '${difference.inDays}d ago';
    } else if (difference.inHours > 0) {
      return '${difference.inHours}h ago';
    } else if (difference.inMinutes > 0) {
      return '${difference.inMinutes}m ago';
    } else {
      return 'Just now';
    }
  }
}
