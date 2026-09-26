import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/ticket_board.dart';
import '../../providers/ticket_board_provider.dart';

/// Tickets grouped by status, or by the stages of one ticket pipeline.
///
/// One lane per page, like the lead and task boards: lanes side by side are
/// too narrow to read on a phone. Moving a ticket is a menu on its card, which
/// ends in the same `PATCH /cases/<id>/move/` the web board uses. A refusal
/// (a full stage, the close gate, no write access) shows the server's
/// sentence and the card stays where it was.
class TicketBoardScreen extends ConsumerStatefulWidget {
  const TicketBoardScreen({super.key});

  @override
  ConsumerState<TicketBoardScreen> createState() => _TicketBoardScreenState();
}

class _TicketBoardScreenState extends ConsumerState<TicketBoardScreen> {
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
    final boardAsync = ref.watch(ticketBoardProvider);
    final data = boardAsync.value;
    // A lane can disappear between builds (the picker moved to a pipeline
    // with fewer stages), so never index past the end.
    final lanes = data?.lanes ?? const <TicketBoardLane>[];
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
                : () => ref.read(ticketBoardProvider.notifier).refresh(),
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
            onPressed: () => ref.read(ticketBoardProvider.notifier).refresh(),
            child: const Text('Try again'),
          ),
        ),
        data: (data) => data.lanes.isEmpty
            ? const _Message(
                icon: LucideIcons.squareKanban,
                title: 'No stages yet',
                body:
                    'This pipeline has no stages, so there is nowhere to '
                    'show a ticket.',
              )
            : _board(lanes, laneIndex),
      ),
    );
  }

  Widget _title(TicketBoardData? data) {
    final name = data?.active?.name ?? 'Ticket board';
    final text = Text(
      name,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: AppTypography.h3,
    );
    if ((data?.pipelines ?? const []).isEmpty) return text;
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

  Widget _board(List<TicketBoardLane> lanes, int laneIndex) {
    return Column(
      children: [
        _laneStrip(lanes, laneIndex),
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
  Widget _laneStrip(List<TicketBoardLane> lanes, int laneIndex) {
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
    TicketBoardLane lane, {
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

  Widget _lanePage(TicketBoardLane lane) {
    if (lane.cards.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            'Nothing in ${lane.name}.',
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

  Widget _cardTile(TicketBoardLane lane, TicketBoardCard card) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        // Uniform on purpose: Flutter refuses to paint a border radius over
        // sides of different colours. The lane tab carries the lane colour.
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
              if (card.accountName.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  card.accountName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  _pill(card.priority.label, card.priority.color),
                  if (card.slaBreached)
                    _pill('SLA breached', AppColors.danger600)
                  else if (card.slaAtRisk)
                    _pill('SLA at risk', AppColors.warning600),
                  Text(
                    card.assignee.isEmpty ? 'Unassigned' : card.assignee,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
                  ),
                ],
              ),
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

  void _showCardActions(TicketBoardLane lane, TicketBoardCard card) {
    final data = ref.read(ticketBoardProvider).value;
    final targets = data?.destinationsFrom(lane) ?? const <TicketBoardLane>[];
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
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
                title: const Text('Open ticket'),
                onTap: () {
                  Navigator.pop(sheetContext);
                  context.push('/tickets/${card.id}');
                },
              ),
              if (targets.isNotEmpty) ...[
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                  child: Text('Move to', style: AppTypography.labelSmall),
                ),
                for (final target in targets)
                  ListTile(
                    leading: Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: target.color,
                        shape: BoxShape.circle,
                      ),
                    ),
                    title: Text(target.name),
                    onTap: () {
                      Navigator.pop(sheetContext);
                      _move(card, target);
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

  /// The server's own words on a refusal. On success the board follows the
  /// ticket to its new lane.
  Future<void> _move(TicketBoardCard card, TicketBoardLane target) async {
    setState(() => _busy = true);
    final response = await ref
        .read(ticketBoardProvider.notifier)
        .moveTicket(ticketId: card.id, target: target);
    if (!mounted) return;
    setState(() => _busy = false);
    if (!response.success) {
      _snack(response.message ?? 'Could not move that ticket.');
      return;
    }
    _snack('Moved to ${target.name}.');
    final lanes = ref.read(ticketBoardProvider).value?.lanes ?? const [];
    final index = lanes.indexWhere((lane) => lane.id == target.id);
    if (index >= 0) _goTo(index);
  }

  void _showPipelinePicker() {
    final data = ref.read(ticketBoardProvider).value;
    final pipelines = data?.pipelines ?? const <TicketPipelineSummary>[];
    final activeId = data?.active?.id;

    Widget option(
      BuildContext sheetContext,
      String? id,
      String name,
      IconData icon,
    ) {
      final selected = id == activeId;
      return ListTile(
        leading: Icon(
          selected ? LucideIcons.check : icon,
          size: 20,
          color: selected ? AppColors.primary600 : AppColors.textSecondary,
        ),
        title: Text(name),
        onTap: () {
          Navigator.pop(sheetContext);
          setState(() => _laneIndex = 0);
          if (_pages.hasClients) _pages.jumpToPage(0);
          ref.read(ticketBoardProvider.notifier).select(id);
        },
      );
    }

    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
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
                child: Text('Board', style: AppTypography.h3),
              ),
              option(sheetContext, null, 'By status', LucideIcons.circleDot),
              for (final pipeline in pipelines)
                option(
                  sheetContext,
                  pipeline.id,
                  pipeline.name,
                  LucideIcons.squareKanban,
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
