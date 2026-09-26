import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/lead_board.dart';
import '../../providers/lead_board_provider.dart';

/// Leads grouped by the stages of one lead pipeline, including a pipeline a
/// vertical pack created.
///
/// One lane per page, like the tasks board: lanes side by side are too narrow
/// to read on a phone, and a drag between two of them is a gesture nobody
/// lands. Moving a lead is a menu on its card, which ends in the same
/// `PATCH /leads/<id>/move/` the web board uses.
///
/// The first lane, "No stage", holds the leads in no pipeline yet. It is where
/// a new lead starts, and moving one from there into a stage is how it joins
/// the pipeline. Moving one back into it takes the lead out of the pipeline.
class LeadBoardScreen extends ConsumerStatefulWidget {
  const LeadBoardScreen({super.key});

  @override
  ConsumerState<LeadBoardScreen> createState() => _LeadBoardScreenState();
}

class _LeadBoardScreenState extends ConsumerState<LeadBoardScreen> {
  final PageController _pages = PageController();
  int _laneIndex = 0;
  bool _busy = false;

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final boardAsync = ref.watch(leadBoardProvider);
    final data = boardAsync.value;
    // A lane can disappear between builds (the picker moved to a pipeline with
    // fewer stages), so never index past the end.
    final lanes = data?.lanes ?? const <LeadBoardLane>[];
    final laneIndex = lanes.isEmpty ? 0 : _laneIndex.clamp(0, lanes.length - 1);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        title: _title(data),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(LucideIcons.refreshCw, size: 20),
            onPressed: _busy
                ? null
                : () => ref.read(leadBoardProvider.notifier).refresh(),
          ),
        ],
      ),
      body: boardAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _Message(
          icon: LucideIcons.circleAlert,
          title: 'Could not load the board',
          body: '$error',
          action: FilledButton(
            onPressed: () => ref.read(leadBoardProvider.notifier).refresh(),
            child: const Text('Try again'),
          ),
        ),
        data: (data) => data.hasNoPipelines
            ? const _Message(
                icon: LucideIcons.squareKanban,
                title: 'No pipelines yet',
                body:
                    'A pipeline sorts leads into stages you move them '
                    'through. Applying an industry pack in organization '
                    'settings creates one.',
              )
            : _board(data, lanes, laneIndex),
      ),
    );
  }

  Widget _title(LeadBoardData? data) {
    final name = data?.active?.name ?? 'Lead board';
    final canPick = (data?.pipelines.length ?? 0) > 1;
    final text = Text(
      name,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: AppTypography.h3,
    );
    if (!canPick) return text;
    return InkWell(
      onTap: _busy ? null : _showPipelinePicker,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 44),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(child: text),
            const SizedBox(width: 4),
            Icon(
              LucideIcons.chevronDown,
              size: 18,
              color: AppColors.textSecondary,
            ),
          ],
        ),
      ),
    );
  }

  Widget _board(LeadBoardData data, List<LeadBoardLane> lanes, int laneIndex) {
    return Column(
      children: [
        _laneStrip(lanes, laneIndex),
        if (data.stages.isEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Text(
              'This pipeline has no stages yet, so there is nowhere to move '
              'a lead.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
        Expanded(
          child: PageView.builder(
            controller: _pages,
            itemCount: lanes.length,
            onPageChanged: (index) => setState(() => _laneIndex = index),
            itemBuilder: (context, index) => _lanePage(lanes[index]),
          ),
        ),
      ],
    );
  }

  /// The lane tabs: the "where am I" a PageView needs, and a way to reach a
  /// lane without swiping through the ones between.
  Widget _laneStrip(List<LeadBoardLane> lanes, int laneIndex) {
    return Container(
      height: 52,
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.gray100)),
      ),
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        children: [
          for (var i = 0; i < lanes.length; i++)
            _laneTab(lanes[i], selected: i == laneIndex, onTap: () => _goTo(i)),
        ],
      ),
    );
  }

  Widget _laneTab(
    LeadBoardLane lane, {
    required bool selected,
    required VoidCallback onTap,
  }) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? AppColors.primary50 : AppColors.gray100,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: lane.color,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 6),
              Text(
                lane.name,
                style: AppTypography.labelSmall.copyWith(
                  color: selected
                      ? AppColors.primary600
                      : AppColors.textSecondary,
                ),
              ),
              const SizedBox(width: 6),
              Text(
                lane.wipLimit == null
                    ? '${lane.count}'
                    : '${lane.count}/${lane.wipLimit}',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _lanePage(LeadBoardLane lane) {
    if (lane.cards.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            lane.isUnstaged
                ? 'Every lead you can see is already in a stage.'
                : 'Nothing in ${lane.name}.',
            textAlign: TextAlign.center,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 32),
      itemCount: lane.cards.length + (lane.isTruncated ? 1 : 0),
      itemBuilder: (context, index) {
        if (index == lane.cards.length) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text(
              'Showing the first ${lane.cards.length} of ${lane.count}.',
              textAlign: TextAlign.center,
              style: AppTypography.caption.copyWith(
                color: AppColors.textTertiary,
              ),
            ),
          );
        }
        return _cardTile(lane, lane.cards[index]);
      },
    );
  }

  Widget _cardTile(LeadBoardLane lane, LeadBoardCard card) {
    final ratingColor = switch (card.rating) {
      'HOT' => AppColors.danger600,
      'WARM' => AppColors.warning600,
      _ => AppColors.textSecondary,
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        // Uniform on purpose: Flutter refuses to paint a border radius over
        // sides of different colours. The lane tab carries the stage colour.
        border: Border.all(color: AppColors.border),
      ),
      child: InkWell(
        onTap: _busy ? null : () => _showCardActions(lane, card),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                card.name,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
              ),
              if (card.company.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  card.company,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
              if (card.rating.isNotEmpty ||
                  card.followUpOverdue ||
                  card.owner.isNotEmpty) ...[
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    if (card.rating.isNotEmpty) _pill(card.rating, ratingColor),
                    if (card.followUpOverdue)
                      _pill('Follow-up due', AppColors.danger600),
                    if (card.owner.isNotEmpty)
                      Text(
                        card.owner,
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textTertiary,
                        ),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _pill(String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    );
  }

  void _goTo(int index) {
    setState(() => _laneIndex = index);
    if (_pages.hasClients) {
      _pages.animateToPage(
        index,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    }
  }

  void _showCardActions(LeadBoardLane lane, LeadBoardCard card) {
    final data = ref.read(leadBoardProvider).value;
    final targets = data?.destinationsFrom(lane) ?? const <LeadBoardLane>[];
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
                child: Text(card.name, style: AppTypography.h3),
              ),
              ListTile(
                leading: Icon(
                  LucideIcons.externalLink,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                title: const Text('Open lead'),
                onTap: () {
                  Navigator.pop(sheetContext);
                  context.push('/leads/${card.id}');
                },
              ),
              if (targets.isNotEmpty) ...[
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                  child: Text('Move to', style: AppTypography.labelSmall),
                ),
                for (final stage in targets)
                  ListTile(
                    leading: Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: stage.color,
                        shape: BoxShape.circle,
                      ),
                    ),
                    title: Text(stage.name),
                    subtitle: stage.isUnstaged
                        ? const Text('Takes the lead out of this pipeline')
                        : null,
                    onTap: () {
                      Navigator.pop(sheetContext);
                      _move(card, stage);
                    },
                  ),
              ],
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  /// The server's own words on a refusal: it knows the caller may not edit
  /// this lead, that the stage is full, or that the lead is in another
  /// pipeline. On success the board follows the lead to its new lane.
  Future<void> _move(LeadBoardCard card, LeadBoardLane target) async {
    setState(() => _busy = true);
    final response = await ref
        .read(leadBoardProvider.notifier)
        .moveLead(leadId: card.id, stageId: target.moveStageId);
    if (!mounted) return;
    setState(() => _busy = false);
    if (!response.success) {
      _snack(response.message ?? 'Could not move that lead.');
      return;
    }
    _snack('Moved to ${target.name}.');
    final lanes = ref.read(leadBoardProvider).value?.lanes ?? const [];
    final index = lanes.indexWhere((lane) => lane.id == target.id);
    if (index >= 0) _goTo(index);
  }

  void _showPipelinePicker() {
    final data = ref.read(leadBoardProvider).value;
    final pipelines = data?.pipelines ?? const <LeadPipelineSummary>[];
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text('Pipelines', style: AppTypography.h3),
              ),
              for (final pipeline in pipelines)
                ListTile(
                  leading: Icon(
                    pipeline.id == data?.active?.id
                        ? LucideIcons.check
                        : LucideIcons.squareKanban,
                    size: 20,
                    color: pipeline.id == data?.active?.id
                        ? AppColors.primary600
                        : AppColors.textSecondary,
                  ),
                  title: Text(pipeline.name),
                  onTap: () {
                    Navigator.pop(sheetContext);
                    setState(() => _laneIndex = 0);
                    if (_pages.hasClients) _pages.jumpToPage(0);
                    ref.read(leadBoardProvider.notifier).select(pipeline.id);
                  },
                ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  void _snack(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.body,
    this.action,
  });

  final IconData icon;
  final String title;
  final String body;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 36, color: AppColors.textTertiary),
            const SizedBox(height: 12),
            Text(title, style: AppTypography.h3, textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text(
              body,
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            if (action != null) ...[const SizedBox(height: 16), action!],
          ],
        ),
      ),
    );
  }
}
