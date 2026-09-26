import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/lead_pipeline.dart';
import '../../providers/auth_provider.dart';
import '../../providers/lead_pipelines_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/badge.dart';
import 'lead_pipeline_form_sheet.dart';

/// The org's lead pipelines: the boards leads are moved through.
///
/// Reading is open to every member; creating, renaming and deleting are
/// admin-only, and the server answers 403 to anyone else. A pipeline that
/// still holds leads cannot be deleted, and the server says how many.
class LeadPipelinesScreen extends ConsumerWidget {
  const LeadPipelinesScreen({super.key});

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    LeadPipeline? existing,
  ) async {
    final payload = await showLeadPipelineFormSheet(
      context,
      existing: existing,
    );
    if (payload == null || !context.mounted) return;
    final notifier = ref.read(leadPipelinesProvider.notifier);
    final error = existing == null
        ? await notifier.createPipeline(payload)
        : await notifier.updatePipeline(existing.id, payload);
    if (!context.mounted) return;
    _snack(
      context,
      error ?? (existing == null ? 'Pipeline created' : 'Pipeline saved'),
    );
  }

  Future<void> _delete(
    BuildContext context,
    WidgetRef ref,
    LeadPipeline pipeline,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete ${pipeline.name}?'),
        content: const Text(
          'It disappears from the board along with its stages. A pipeline '
          'that still holds leads cannot be deleted: move them out of its '
          'stages first.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final error = await ref
        .read(leadPipelinesProvider.notifier)
        .deletePipeline(pipeline.id);
    if (!context.mounted) return;
    _snack(context, error ?? 'Pipeline deleted');
  }

  void _snack(BuildContext context, String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(leadPipelinesProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Lead pipelines'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          if (isAdmin)
            IconButton(
              icon: const Icon(LucideIcons.plus),
              tooltip: 'New pipeline',
              onPressed: () => _edit(context, ref, null),
            ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => _ErrorState(
          onRetry: () => ref.read(leadPipelinesProvider.notifier).refresh(),
        ),
        data: (pipelines) {
          if (pipelines.isEmpty) return _EmptyState(isAdmin: isAdmin);
          return RefreshIndicator(
            onRefresh: () => ref.read(leadPipelinesProvider.notifier).refresh(),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 96),
              children: [
                for (final pipeline in pipelines)
                  _PipelineRow(
                    pipeline: pipeline,
                    canEdit: isAdmin,
                    onOpen: () => context.push(
                      AppRoutes.settingsLeadPipeline(pipeline.id),
                    ),
                    onRename: () => _edit(context, ref, pipeline),
                    onDelete: () => _delete(context, ref, pipeline),
                  ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                  child: Text(
                    'Open a pipeline to see its stages. Each stage is a lane '
                    'on the lead board.',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
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

String _count(int n, String one, String many) => '$n ${n == 1 ? one : many}';

class _PipelineRow extends StatelessWidget {
  const _PipelineRow({
    required this.pipeline,
    required this.canEdit,
    required this.onOpen,
    required this.onRename,
    required this.onDelete,
  });

  final LeadPipeline pipeline;
  final bool canEdit;
  final VoidCallback onOpen;
  final VoidCallback onRename;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: onOpen,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Wrap(
                          spacing: 8,
                          runSpacing: 4,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            Text(
                              pipeline.name,
                              style: AppTypography.body.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            if (pipeline.isDefault)
                              StatusBadge(
                                label: 'Default',
                                color: AppColors.primary600,
                              ),
                          ],
                        ),
                        if (pipeline.description.isNotEmpty) ...[
                          const SizedBox(height: 2),
                          Text(
                            pipeline.description,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.caption.copyWith(
                              color: AppColors.textSecondary,
                            ),
                          ),
                        ],
                        const SizedBox(height: 4),
                        Text(
                          '${_count(pipeline.stageCount, 'stage', 'stages')}, '
                          '${_count(pipeline.leadCount, 'lead', 'leads')}',
                          style: AppTypography.caption.copyWith(
                            color: AppColors.textTertiary,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Icon(
                    LucideIcons.chevronRight,
                    size: 18,
                    color: AppColors.textTertiary,
                  ),
                ],
              ),
            ),
          ),
          if (canEdit)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  SizedBox(
                    height: 44,
                    child: OutlinedButton(
                      onPressed: onRename,
                      child: const Text('Rename'),
                    ),
                  ),
                  SizedBox(
                    height: 44,
                    child: OutlinedButton(
                      onPressed: onDelete,
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger600,
                      ),
                      child: const Text('Delete'),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.isAdmin});

  final bool isAdmin;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.squareKanban,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'No lead pipelines',
              style: AppTypography.h3,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              isAdmin
                  ? 'A pipeline sorts leads into stages you move them through. '
                        'Tap + to create one.'
                  : 'A pipeline sorts leads into stages you move them through. '
                        'An administrator sets these up.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.triangleAlert,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 16),
            Text(
              'Could not load the pipelines',
              style: AppTypography.body,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
