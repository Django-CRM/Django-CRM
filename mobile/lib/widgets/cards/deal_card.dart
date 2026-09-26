import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/lookup_provider.dart';
import '../common/common.dart';
import 'deal_aging_badge.dart';

/// Deal Card Widget
/// Displays deal summary in Kanban columns or list view
class DealCard extends ConsumerWidget {
  final Deal deal;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final bool isDragging;
  final bool isSelected;

  const DealCard({
    super.key,
    required this.deal,
    this.onTap,
    this.onLongPress,
    this.isDragging = false,
    this.isSelected = false,
  });

  int get daysUntilClose {
    if (deal.closeDate == null) return 999;
    return deal.closeDate!.difference(DateTime.now()).inDays;
  }

  Color get closeDateColor {
    if (daysUntilClose < 0) return AppColors.danger600;
    if (daysUntilClose <= 7) return AppColors.warning600;
    return AppColors.textSecondary;
  }

  String get closeDateText {
    if (daysUntilClose < 0) {
      return '${-daysUntilClose}d overdue';
    } else if (daysUntilClose == 0) {
      return 'Today';
    } else {
      return '${daysUntilClose}d left';
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Real assignee lookup, match the first profile id from the deal against
    // the loaded users list. Falls back to the email-derived display name on
    // the Deal so we still render something when the lookup hasn't resolved.
    final users = ref.watch(usersProvider);
    UserLookup? assignee;
    if (deal.assignedToIds.isNotEmpty) {
      final firstId = deal.assignedToIds.first;
      for (final u in users) {
        if (u.id == firstId) {
          assignee = u;
          break;
        }
      }
    }
    final assigneeLabel =
        assignee?.displayName ??
        (deal.assignedTo == 'Unassigned' ? null : deal.assignedTo);

    return GestureDetector(
      onTap: onTap,
      onLongPress: onLongPress,
      child: AnimatedContainer(
        duration: AppDurations.fast,
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppLayout.borderRadiusLg,
          boxShadow: isDragging ? AppLayout.shadowMd : AppLayout.shadowSm,
          border: isSelected
              ? Border.all(color: AppColors.primary500, width: 2)
              : null,
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: AppLayout.borderRadiusLg,
          child: InkWell(
            onTap: onTap,
            onLongPress: onLongPress,
            borderRadius: AppLayout.borderRadiusLg,
            child: Opacity(
              opacity: isDragging ? 0.8 : 1.0,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Row 1: Title + Priority + Selection check
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (isSelected) ...[
                          Icon(
                            LucideIcons.checkCircle2,
                            size: 18,
                            color: AppColors.primary600,
                          ),
                          const SizedBox(width: 6),
                        ],
                        Expanded(
                          child: Text(
                            deal.title,
                            style: AppTypography.label.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        // Priority badge shows for every priority that isn't
                        // the default Medium, Low/High/Urgent all visible.
                        if (deal.priority != Priority.medium) ...[
                          const SizedBox(width: 8),
                          PriorityBadge.fromPriority(deal.priority),
                        ],
                      ],
                    ),

                    // Row 2: Company
                    const SizedBox(height: 4),
                    Text(
                      deal.companyName,
                      style: AppTypography.body.copyWith(
                        color: AppColors.textSecondary,
                        fontSize: 13,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),

                    // Row 2b: past expected / stalled pill
                    if (dealAgingLabel(deal) != null) ...[
                      const SizedBox(height: 6),
                      DealAgingBadge(deal: deal),
                    ],

                    // Row 3: Labels
                    if (deal.labels.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: deal.labels
                            .take(3)
                            .map((label) => LabelPill(label: label))
                            .toList(),
                      ),
                    ],

                    // Row 4: Value + Close Date
                    const SizedBox(height: 12),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          _formatCurrency(deal.value),
                          style: AppTypography.h3.copyWith(
                            color: AppColors.textPrimary,
                          ),
                        ),
                        Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (daysUntilClose <= 7 && daysUntilClose >= 0)
                              Padding(
                                padding: const EdgeInsets.only(right: 4),
                                child: Icon(
                                  LucideIcons.alertCircle,
                                  size: 14,
                                  color: closeDateColor,
                                ),
                              ),
                            Text(
                              closeDateText,
                              style: AppTypography.caption.copyWith(
                                color: closeDateColor,
                                fontWeight: daysUntilClose <= 7
                                    ? FontWeight.w600
                                    : FontWeight.normal,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),

                    // Row 5: Probability bar + Assignee
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              ClipRRect(
                                borderRadius: BorderRadius.circular(2),
                                child: LinearProgressIndicator(
                                  value: deal.probability / 100,
                                  minHeight: 4,
                                  backgroundColor: AppColors.gray200,
                                  valueColor: AlwaysStoppedAnimation(
                                    _getProbabilityColor(deal.probability),
                                  ),
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${deal.probability}% probability',
                                style: AppTypography.caption.copyWith(
                                  color: AppColors.textTertiary,
                                  fontSize: 10,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 12),
                        if (assigneeLabel != null)
                          UserAvatar(
                            name: assigneeLabel,
                            imageUrl: assignee?.profilePic,
                            size: AvatarSize.xs,
                          ),
                      ],
                    ),

                    // Row 6: Products count
                    if (deal.products.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: AppColors.gray100,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              LucideIcons.package,
                              size: 12,
                              color: AppColors.textSecondary,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              '${deal.products.length} product${deal.products.length > 1 ? 's' : ''}',
                              style: AppTypography.caption.copyWith(
                                color: AppColors.textSecondary,
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  String _formatCurrency(double value) {
    final symbol = deal.currency.symbol;
    if (value >= 1000000) {
      return '$symbol${(value / 1000000).toStringAsFixed(1)}M';
    } else if (value >= 1000) {
      return '$symbol${(value / 1000).toStringAsFixed(0)}K';
    } else {
      return '$symbol${value.toStringAsFixed(0)}';
    }
  }

  Color _getProbabilityColor(int probability) {
    if (probability >= 75) return AppColors.success500;
    if (probability >= 50) return AppColors.primary500;
    if (probability >= 25) return AppColors.warning500;
    return AppColors.gray400;
  }
}
