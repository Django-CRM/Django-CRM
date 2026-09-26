import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal_pipeline.dart';
import '../../providers/auth_provider.dart';
import '../../providers/deal_pipelines_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/badge.dart';
import 'deal_pipeline_sheets.dart';

/// The org's deal pipelines: the boards deals move through, each with its own
/// stages.
///
/// Reading is open to every member; creating, renaming and deleting are
/// admin-only, and the server answers 403 to anyone else. The default pipeline
/// cannot be deleted, nor can one that still holds deals; the server says
/// which.
class DealPipelinesScreen extends ConsumerWidget {
  const DealPipelinesScreen({super.key});

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    DealPipeline? existing,
  ) async {
    final name = await showDealPipelineNameSheet(context, existing: existing);
    if (name == null || !context.mounted) return;
    final notifier = ref.read(dealPipelinesProvider.notifier);
    final error = existing == null
        ? await notifier.createPipeline(name)
        : await notifier.renamePipeline(existing.id, name);
    if (!context.mounted) return;
    _snack(
      context,
      error ?? (existing == null ? 'Pipeline created' : 'Pipeline renamed'),
    );
  }

  Future<void> _delete(
    BuildContext context,
    WidgetRef ref,
    DealPipeline pipeline,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete ${pipeline.name}?'),
        content: const Text(
          'Its stages go with it. A pipeline that still holds deals cannot be '
          'deleted: move them to another pipeline first.',
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
        .read(dealPipelinesProvider.notifier)
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
    final async = ref.watch(dealPipelinesProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);
    // A server without configurable pipelines answers with the one built-in
    // pipeline, which has no id to write to.
    final canEdit =
        isAdmin && !(async.value?.any((p) => p.id.isEmpty) ?? false);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Deal pipelines'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          if (canEdit)
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
          onRetry: () => ref.read(dealPipelinesProvider.notifier).refresh(),
        ),
        data: (pipelines) => RefreshIndicator(
          onRefresh: () => ref.read(dealPipelinesProvider.notifier).refresh(),
          child: ListView(
            padding: const EdgeInsets.only(bottom: 96),
            children: [
              for (final pipeline in pipelines)
                _PipelineRow(
                  pipeline: pipeline,
                  canEdit: canEdit,
                  onOpen: pipeline.id.isEmpty
                      ? null
                      : () => context.push(
                          AppRoutes.settingsDealPipeline(pipeline.id),
                        ),
                  onRename: () => _edit(context, ref, pipeline),
                  onDelete: pipeline.isDefault
                      ? null
                      : () => _delete(context, ref, pipeline),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Text(
                  'Open a pipeline to see its stages. Each stage is a column '
                  'on the deals board.',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
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

class _PipelineRow extends StatelessWidget {
  const _PipelineRow({
    required this.pipeline,
    required this.canEdit,
    required this.onOpen,
    required this.onRename,
    required this.onDelete,
  });

  final DealPipeline pipeline;
  final bool canEdit;

  /// Null for the built-in pipeline of an older server, which has no page.
  final VoidCallback? onOpen;
  final VoidCallback onRename;

  /// Null for the default pipeline, which the server never deletes.
  final VoidCallback? onDelete;

  @override
  Widget build(BuildContext context) {
    final n = pipeline.stages.length;
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: onOpen,
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 44),
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
                          const SizedBox(height: 4),
                          Text(
                            '$n ${n == 1 ? 'stage' : 'stages'}',
                            style: AppTypography.caption.copyWith(
                              color: AppColors.textTertiary,
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (onOpen != null) ...[
                      const SizedBox(width: 8),
                      Icon(
                        LucideIcons.chevronRight,
                        size: 18,
                        color: AppColors.textTertiary,
                      ),
                    ],
                  ],
                ),
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
                  if (onDelete != null)
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
