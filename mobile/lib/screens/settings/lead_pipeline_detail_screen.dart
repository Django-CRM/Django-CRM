import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/lead_pipeline.dart';
import '../../providers/auth_provider.dart';
import '../../providers/lead_pipelines_provider.dart';
import 'lead_stage_form_sheet.dart';

/// One lead pipeline's stages, in the order the board shows them as lanes.
///
/// Every member may look. An admin may add, edit, delete and reorder stages;
/// the server answers 403 to anyone else. Reordering is the up and down
/// buttons on each row, and each press sends the whole order, because the
/// server takes nothing less.
class LeadPipelineDetailScreen extends ConsumerStatefulWidget {
  const LeadPipelineDetailScreen({super.key, required this.pipelineId});

  final String pipelineId;

  @override
  ConsumerState<LeadPipelineDetailScreen> createState() =>
      _LeadPipelineDetailScreenState();
}

class _LeadPipelineDetailScreenState
    extends ConsumerState<LeadPipelineDetailScreen> {
  /// True while a write is in flight, so a second press cannot send an order
  /// computed from a list the first one is still changing.
  bool _busy = false;

  LeadPipelineDetailNotifier get _notifier =>
      ref.read(leadPipelineDetailProvider(widget.pipelineId).notifier);

  Future<void> _run(Future<String?> Function() write, String done) async {
    setState(() => _busy = true);
    final error = await write();
    if (!mounted) return;
    setState(() => _busy = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? done)));
  }

  Future<void> _edit(LeadStage? existing) async {
    final payload = await showLeadStageFormSheet(context, existing: existing);
    if (payload == null || !mounted) return;
    await _run(
      () => existing == null
          ? _notifier.createStage(payload)
          : _notifier.updateStage(existing.id, payload),
      existing == null ? 'Stage added' : 'Stage saved',
    );
  }

  Future<void> _delete(LeadStage stage) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete ${stage.name}?'),
        content: const Text(
          'Its lane disappears from the board. A stage that still holds leads '
          'cannot be deleted: move them to another stage first.',
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
    if (confirmed != true || !mounted) return;
    await _run(() => _notifier.deleteStage(stage.id), 'Stage deleted');
  }

  Future<void> _move(int from, int to) =>
      _run(() => _notifier.moveStage(from, to), 'Order saved');

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(leadPipelineDetailProvider(widget.pipelineId));
    final isAdmin = ref.watch(isOrgAdminProvider);
    final canEdit = isAdmin && !_busy;

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(async.value?.pipeline.name ?? 'Pipeline'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          if (isAdmin && async.hasValue)
            IconButton(
              icon: const Icon(LucideIcons.plus),
              tooltip: 'Add stage',
              onPressed: canEdit ? () => _edit(null) : null,
            ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _ErrorState(
          message: error.toString().replaceFirst('Exception: ', ''),
          onRetry: () => _notifier.refresh(),
        ),
        data: (detail) => RefreshIndicator(
          onRefresh: () => _notifier.refresh(),
          child: ListView(
            padding: const EdgeInsets.only(bottom: 96),
            children: [
              _Header(detail: detail),
              if (detail.stages.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(
                    isAdmin
                        ? 'No stages yet. Add one so leads can be moved into '
                              'this pipeline.'
                        : 'No stages yet.',
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ),
              for (final (index, stage) in detail.stages.indexed)
                _StageRow(
                  stage: stage,
                  isAdmin: isAdmin,
                  onEdit: canEdit ? () => _edit(stage) : null,
                  onDelete: canEdit ? () => _delete(stage) : null,
                  onMoveUp: canEdit && index > 0
                      ? () => _move(index, index - 1)
                      : null,
                  onMoveDown: canEdit && index < detail.stages.length - 1
                      ? () => _move(index, index + 1)
                      : null,
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Text(
                  'Stages are lanes on the lead board, in this order. Moving a '
                  'lead into a stage can set its status and win probability.',
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

String _count(int n, String one, String many) => '$n ${n == 1 ? one : many}';

class _Header extends StatelessWidget {
  const _Header({required this.detail});

  final LeadPipelineDetail detail;

  @override
  Widget build(BuildContext context) {
    final pipeline = detail.pipeline;
    return Container(
      color: AppColors.surface,
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (pipeline.description.isNotEmpty) ...[
            Text(
              pipeline.description,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 4),
          ],
          Text(
            '${_count(detail.stages.length, 'stage', 'stages')}, '
            '${_count(pipeline.leadCount, 'lead', 'leads')}'
            '${pipeline.isDefault ? ', the default pipeline' : ''}',
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

class _StageRow extends StatelessWidget {
  const _StageRow({
    required this.stage,
    required this.isAdmin,
    required this.onEdit,
    required this.onDelete,
    required this.onMoveUp,
    required this.onMoveDown,
  });

  final LeadStage stage;
  final bool isAdmin;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;
  final VoidCallback? onMoveUp;
  final VoidCallback? onMoveDown;

  String get _summary {
    final parts = [
      leadStageTypeOptions(stage.stageType)[stage.stageType]!,
      if (stage.mapsToStatus != null)
        'sets status to '
            '${leadStageStatusOptions(stage.mapsToStatus)[stage.mapsToStatus]}',
      if (stage.winProbability > 0) '${stage.winProbability}% win',
    ];
    return parts.join(', ');
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(0, 4, 4, 4),
      child: Row(
        children: [
          Expanded(
            child: InkWell(
              onTap: onEdit,
              child: ConstrainedBox(
                constraints: const BoxConstraints(minHeight: 44),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                  child: Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(
                          color: stage.swatch,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              stage.name,
                              style: AppTypography.body.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              _summary,
                              style: AppTypography.caption.copyWith(
                                color: AppColors.textSecondary,
                              ),
                            ),
                            Text(
                              _count(stage.leadCount, 'lead', 'leads'),
                              style: AppTypography.caption.copyWith(
                                color: AppColors.textTertiary,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
          if (isAdmin) ...[
            _IconAction(
              icon: LucideIcons.chevronUp,
              tooltip: 'Move ${stage.name} up',
              onPressed: onMoveUp,
            ),
            _IconAction(
              icon: LucideIcons.chevronDown,
              tooltip: 'Move ${stage.name} down',
              onPressed: onMoveDown,
            ),
            _IconAction(
              icon: LucideIcons.trash2,
              tooltip: 'Delete ${stage.name}',
              color: AppColors.danger600,
              onPressed: onDelete,
            ),
          ],
        ],
      ),
    );
  }
}

class _IconAction extends StatelessWidget {
  const _IconAction({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.color,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      icon: Icon(icon, size: 18),
      tooltip: tooltip,
      color: color ?? AppColors.textSecondary,
      constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
      padding: EdgeInsets.zero,
      onPressed: onPressed,
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});

  final String message;
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
              message,
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
