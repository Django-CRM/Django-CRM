import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../cards/deal_card.dart';

/// Kanban Column Widget
/// Displays one stage of a deal pipeline with draggable deal cards.
class KanbanColumn extends StatelessWidget {
  final DealPipelineStage stage;

  /// The stage's colour in its pipeline. See [DealPipeline.colorOf].
  final Color color;
  final List<Deal> deals;
  final Function(Deal) onDealTap;
  final Function(Deal)? onDealLongPress;
  final Function(Deal, DealPipelineStage)? onDealMoved;
  final double width;
  final Set<String> selectedIds;

  const KanbanColumn({
    super.key,
    required this.stage,
    required this.color,
    required this.deals,
    required this.onDealTap,
    this.onDealLongPress,
    this.onDealMoved,
    this.width = 300,
    this.selectedIds = const {},
  });

  /// Picks the currency that holds the largest total within this column so the
  /// header total isn't apples-to-oranges when an org keeps deals in multiple
  /// currencies.
  ({Currency currency, double total, bool mixed}) _dominantBucket() {
    if (deals.isEmpty) {
      return (currency: Currency.usd, total: 0, mixed: false);
    }
    final Map<Currency, double> totals = {};
    for (final deal in deals) {
      totals[deal.currency] = (totals[deal.currency] ?? 0) + deal.value;
    }
    final entries = totals.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return (
      currency: entries.first.key,
      total: entries.first.value,
      mixed: entries.length > 1,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      margin: const EdgeInsets.symmetric(horizontal: 6),
      decoration: BoxDecoration(
        color: AppColors.gray50,
        borderRadius: AppLayout.borderRadiusLg,
      ),
      child: Column(
        children: [
          _buildHeader(),
          Expanded(
            child: DragTarget<Deal>(
              onWillAcceptWithDetails: (details) {
                return details.data.stage != stage.code;
              },
              onAcceptWithDetails: (details) {
                onDealMoved?.call(details.data, stage);
              },
              builder: (context, candidateData, rejectedData) {
                final isHighlighted = candidateData.isNotEmpty;

                return AnimatedContainer(
                  duration: AppDurations.fast,
                  decoration: BoxDecoration(
                    color: isHighlighted
                        ? color.withValues(alpha: 0.1)
                        : Colors.transparent,
                    borderRadius: const BorderRadius.vertical(
                      bottom: Radius.circular(16),
                    ),
                    border: isHighlighted
                        ? Border.all(
                            color: color.withValues(alpha: 0.5),
                            width: 2,
                          )
                        : null,
                  ),
                  child: deals.isEmpty
                      ? _buildEmptyState(isHighlighted)
                      : ListView.builder(
                          padding: const EdgeInsets.all(12),
                          itemCount: deals.length,
                          itemBuilder: (context, index) {
                            final deal = deals[index];
                            final selected = selectedIds.contains(deal.id);
                            return LongPressDraggable<Deal>(
                              data: deal,
                              feedback: Material(
                                elevation: 8,
                                borderRadius: AppLayout.borderRadiusLg,
                                child: SizedBox(
                                  width: width - 24,
                                  child: DealCard(deal: deal, isDragging: true),
                                ),
                              ),
                              childWhenDragging: Opacity(
                                opacity: 0.3,
                                child: DealCard(deal: deal),
                              ),
                              onDragStarted: selected
                                  ? null
                                  : () => onDealLongPress?.call(deal),
                              // No onLongPress here: DealCard's own long-press
                              // recognizer sits deeper in the tree than this
                              // LongPressDraggable and shares the same 500ms
                              // deadline, so it would win the gesture arena and
                              // the drag would never start. Selection is still
                              // reachable, onDragStarted above enters selection
                              // mode, and tapping toggles it once in that mode.
                              child: DealCard(
                                deal: deal,
                                onTap: () => onDealTap(deal),
                                isSelected: selected,
                              ),
                            );
                          },
                        ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader() {
    final bucket = _dominantBucket();
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.border, width: 1)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  stage.label,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: AppTypography.label.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: AppColors.gray200,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  '${deals.length}',
                  style: AppTypography.caption.copyWith(
                    fontWeight: FontWeight.w600,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              Text(
                _formatCurrency(bucket.total, bucket.currency),
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              if (bucket.mixed) ...[
                const SizedBox(width: 4),
                Text(
                  '+ mixed',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState(bool highlighted) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              highlighted ? LucideIcons.arrowDownCircle : LucideIcons.inbox,
              size: 32,
              color: highlighted ? color : AppColors.gray300,
            ),
            const SizedBox(height: 8),
            Text(
              highlighted
                  ? 'Drop to move to ${stage.label}'
                  : 'No deals in ${stage.label}',
              textAlign: TextAlign.center,
              style: AppTypography.caption.copyWith(
                color: highlighted ? color : AppColors.textTertiary,
                fontWeight: highlighted ? FontWeight.w600 : FontWeight.normal,
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _formatCurrency(double value, Currency currency) {
    final symbol = currency.symbol;
    if (value >= 1000000) {
      return '$symbol${(value / 1000000).toStringAsFixed(1)}M';
    } else if (value >= 1000) {
      return '$symbol${(value / 1000).toStringAsFixed(0)}K';
    } else {
      return '$symbol${value.toStringAsFixed(0)}';
    }
  }
}
