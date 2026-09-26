import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme/theme.dart';
import '../../providers/duplicates_provider.dart';

/// "Possible duplicates" on a lead, contact or account screen, shown only when
/// there are some. A warning that is always on is one people learn to scroll
/// past.
///
/// Compare opens the side-by-side screen, where the person picks which record
/// to keep. It is offered when the caller could merge in at least one
/// direction: keeping one record deletes the other, and only an admin or a
/// record's creator may delete it. The API enforces that whatever this shows.
class DuplicatesPanel extends ConsumerWidget {
  const DuplicatesPanel({
    super.key,
    required this.module,
    required this.recordId,
  });

  final DuplicateModule module;
  final String recordId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final found = ref.watch(recordDuplicatesProvider((module, recordId))).value;
    if (found == null || found.hits.isEmpty) return const SizedBox.shrink();

    // Rounded by the clip: Flutter refuses a radius on a border whose sides
    // differ, and the warning edge is what marks this card out.
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: Container(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: Border(
              left: BorderSide(color: AppColors.warning500, width: 3),
              top: BorderSide(color: AppColors.border),
              right: BorderSide(color: AppColors.border),
              bottom: BorderSide(color: AppColors.border),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                found.hits.length == 1
                    ? 'POSSIBLE DUPLICATE'
                    : 'POSSIBLE DUPLICATES',
                style: AppTypography.overline.copyWith(
                  color: AppColors.warning700,
                ),
              ),
              const SizedBox(height: 4),
              for (final hit in found.hits)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    children: [
                      Expanded(
                        child: InkWell(
                          onTap: () =>
                              context.push('/${module.path}/${hit.id}'),
                          child: ConstrainedBox(
                            constraints: const BoxConstraints(minHeight: 44),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Text(
                                  hit.name,
                                  style: AppTypography.bodySmall.copyWith(
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                                Text(
                                  'Same ${hit.matchedLabelText}',
                                  style: AppTypography.caption.copyWith(
                                    color: AppColors.textSecondary,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      if (found.canDelete || hit.canDelete)
                        OutlinedButton(
                          style: OutlinedButton.styleFrom(
                            minimumSize: const Size(44, 44),
                          ),
                          onPressed: () => context.push(
                            '/merge/${module.path}/$recordId?with=${hit.id}',
                          ),
                          child: const Text('Compare'),
                        ),
                    ],
                  ),
                ),
              if (!found.canDelete && found.hits.every((h) => !h.canDelete))
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    'Only an admin or whoever created one of them can merge these.',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
