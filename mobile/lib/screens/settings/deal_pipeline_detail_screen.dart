import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/deal_pipeline.dart';
import '../../providers/auth_provider.dart';
import '../../providers/deal_pipelines_provider.dart';
import 'deal_pipeline_sheets.dart';

/// One deal pipeline's stages, in the order the board shows them as columns.
///
/// Every member may look. An admin may add, edit, delete and reorder stages,
/// and set each open stage's expected days; the server answers 403 to anyone
/// else. Reordering is the up and down buttons on each row, and each press
/// sends the whole order, because the server takes nothing less.
class DealPipelineDetailScreen extends ConsumerStatefulWidget {
  const DealPipelineDetailScreen({super.key, required this.pipelineId});

  final String pipelineId;

  @override
  ConsumerState<DealPipelineDetailScreen> createState() =>
      _DealPipelineDetailScreenState();
}

class _DealPipelineDetailScreenState
    extends ConsumerState<DealPipelineDetailScreen> {
  /// True while a write is in flight, so a second press cannot send an order
  /// computed from a list the first one is still changing.
  bool _busy = false;

  DealPipelinesNotifier get _notifier =>
      ref.read(dealPipelinesProvider.notifier);

  Future<void> _run(Future<String?> Function() write, String done) async {
    setState(() => _busy = true);
    final error = await write();
    if (!mounted) return;
    setState(() => _busy = false);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(error ?? done)));
  }

  Future<void> _edit(DealPipelineStage? existing) async {
    final payload = await showDealStageSheet(context, existing: existing);
    if (payload == null || !mounted) return;
    await _run(
      () => existing == null
          ? _notifier.createStage(widget.pipelineId, payload)
          : _notifier.updateStage(existing.id, payload),
      existing == null ? 'Stage added' : 'Stage saved',
    );
  }

  Future<void> _delete(DealPipelineStage stage) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete ${stage.label}?'),
        content: const Text(
          'Its column disappears from the board. A stage that still holds '
          'deals cannot be deleted, and neither can the last open, won or '
          'lost stage.',
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

  Future<void> _move(int from, int to) => _run(
    () => _notifier.moveStage(widget.pipelineId, from, to),
    'Order saved',
  );

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(dealPipelinesProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);
    final canEdit = isAdmin && !_busy;
    DealPipeline? pipeline;
    for (final p in async.value ?? const <DealPipeline>[]) {
      if (p.id == widget.pipelineId) pipeline = p;
    }

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(pipeline?.name ?? 'Pipeline'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          if (isAdmin && pipeline != null)
            IconButton(
              icon: const Icon(LucideIcons.plus),
              tooltip: 'Add stage',
              onPressed: canEdit ? () => _edit(null) : null,
            ),
        ],
      ),
      body: switch ((async, pipeline)) {
        (_, final DealPipeline p) => RefreshIndicator(
          onRefresh: _notifier.refresh,
          child: ListView(
            padding: const EdgeInsets.only(bottom: 96),
            children: [
              _Header(pipeline: p),
              for (final (index, stage) in p.stages.indexed)
                _StageRow(
                  stage: stage,
                  color: p.colorOf(stage),
                  isAdmin: isAdmin,
                  onEdit: canEdit ? () => _edit(stage) : null,
                  onDelete: canEdit ? () => _delete(stage) : null,
                  onMoveUp: canEdit && index > 0
                      ? () => _move(index, index - 1)
                      : null,
                  onMoveDown: canEdit && index < p.stages.length - 1
                      ? () => _move(index, index + 1)
                      : null,
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Text(
                  'Stages are columns on the deals board, in this order. Won '
                  'and lost stages close a deal; only open stages age.',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ),
            ],
          ),
        ),
        (AsyncValue(isLoading: true), _) => const Center(
          child: CircularProgressIndicator(),
        ),
        (AsyncValue(hasError: true), _) => _Message(
          message: 'Could not load the pipeline.',
          onRetry: _notifier.refresh,
        ),
        _ => _Message(
          message: 'This pipeline no longer exists.',
          onRetry: _notifier.refresh,
        ),
      },
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.pipeline});

  final DealPipeline pipeline;

  @override
  Widget build(BuildContext context) {
    final n = pipeline.stages.length;
    return Container(
      color: AppColors.surface,
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Text(
        '$n ${n == 1 ? 'stage' : 'stages'}'
        '${pipeline.isDefault ? ', the default pipeline' : ''}',
        style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
      ),
    );
  }
}

/// "Open. Past expected at 14 days, stalled at 21." and the like.
String dealStageSummary(DealPipelineStage stage) {
  final kind = dealStageKinds[stage.kind] ?? stage.kind;
  final thresholds = stage.agingThresholds;
  if (stage.isClosed) return kind;
  if (thresholds == null) return '$kind. Never ages.';
  final (yellow, red) = thresholds;
  return '$kind. Past expected at $yellow ${yellow == 1 ? 'day' : 'days'}, '
      'stalled at $red.';
}

class _StageRow extends StatelessWidget {
  const _StageRow({
    required this.stage,
    required this.color,
    required this.isAdmin,
    required this.onEdit,
    required this.onDelete,
    required this.onMoveUp,
    required this.onMoveDown,
  });

  final DealPipelineStage stage;
  final Color color;
  final bool isAdmin;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;
  final VoidCallback? onMoveUp;
  final VoidCallback? onMoveDown;

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
                          color: color,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              stage.label,
                              style: AppTypography.body.copyWith(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              dealStageSummary(stage),
                              style: AppTypography.caption.copyWith(
                                color: AppColors.textSecondary,
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
              tooltip: 'Move ${stage.label} up',
              onPressed: onMoveUp,
            ),
            _IconAction(
              icon: LucideIcons.chevronDown,
              tooltip: 'Move ${stage.label} down',
              onPressed: onMoveDown,
            ),
            _IconAction(
              icon: LucideIcons.trash2,
              tooltip: 'Delete ${stage.label}',
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

class _Message extends StatelessWidget {
  const _Message({required this.message, required this.onRetry});

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
