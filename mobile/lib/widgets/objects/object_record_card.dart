import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_object.dart';
import '../common/badge.dart';

/// A record as a card: its name, tags, two filled fields and who it is
/// assigned to. The list and the board both show it.
class ObjectRecordCard extends StatelessWidget {
  const ObjectRecordCard({
    super.key,
    required this.record,
    required this.fields,
    required this.onTap,
    this.margin = const EdgeInsets.only(top: 8),
  });

  final ObjectRecord record;
  final List<ObjectField> fields;
  final VoidCallback onTap;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    final shown = [
      for (final f in fields)
        if (hasFieldValue(record.customFields[f.key]))
          '${f.label}: ${displayValue(f, record.customFields[f.key])}',
    ].take(2).toList();
    final assignees = record.assignedTo.length;

    return Container(
      margin: margin,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.gray200),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      record.name,
                      style: AppTypography.body.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (record.tags.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Wrap(
                          spacing: 4,
                          runSpacing: 4,
                          children: [
                            for (final t in record.tags)
                              LabelPill(label: t.name),
                          ],
                        ),
                      ),
                    for (final line in shown)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(
                          line,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: AppTypography.caption.copyWith(
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              if (assignees > 0) ...[
                const SizedBox(width: 12),
                Icon(
                  LucideIcons.users,
                  size: 16,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 4),
                Text(
                  '$assignees',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
