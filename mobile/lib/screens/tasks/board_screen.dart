import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/board.dart';
import '../../data/models/lead.dart' show Priority;
import '../../providers/board_provider.dart';

/// The kanban board.
///
/// A board card is a different record from a task: separate table, separate
/// endpoints, no status and no owner. Nothing here shows up on the tasks list.
///
/// One lane per page, because lanes side by side on a phone are too narrow to
/// read and dragging a card between two of them is a gesture nobody lands.
/// Reordering *within* a lane is a drag, which is the gesture a vertical list
/// actually supports; moving *between* lanes is a menu on the card. Both end
/// in the same PUT, so the server resequences either way and a reload agrees
/// with what was left on screen.
class BoardScreen extends ConsumerStatefulWidget {
  const BoardScreen({super.key});

  @override
  ConsumerState<BoardScreen> createState() => _BoardScreenState();
}

class _BoardScreenState extends ConsumerState<BoardScreen> {
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
    final boardAsync = ref.watch(boardProvider);
    final data = boardAsync.value;
    // A lane can disappear between builds (another device deleted the board,
    // or the picker moved to a shorter one), so never index past the end.
    final lanes = data?.lanes ?? const <BoardLane>[];
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
            icon: const Icon(LucideIcons.refreshCw, size: 20),
            onPressed: _busy
                ? null
                : () => ref.read(boardProvider.notifier).refresh(),
          ),
        ],
      ),
      floatingActionButton: lanes.isEmpty
          ? null
          : FloatingActionButton.extended(
              onPressed: _busy ? null : () => _showAddCard(lanes[laneIndex]),
              icon: const Icon(LucideIcons.plus, size: 18),
              label: const Text('Card'),
            ),
      body: boardAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _ErrorState(
          message: '$error',
          onRetry: () => ref.read(boardProvider.notifier).refresh(),
        ),
        data: (data) =>
            data.hasNoBoards ? _noBoards() : _board(data, lanes, laneIndex),
      ),
    );
  }

  Widget _title(BoardData? data) {
    final name = data?.active?.name ?? 'Board';
    return InkWell(
      onTap: _busy ? null : _showBoardPicker,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Flexible(
            child: Text(
              name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTypography.h3,
            ),
          ),
          const SizedBox(width: 4),
          Icon(
            LucideIcons.chevronDown,
            size: 18,
            color: AppColors.textSecondary,
          ),
        ],
      ),
    );
  }

  /// No boards at all. The web used to send people here for this, and this
  /// screen did not exist, so an org that had never been given a board through
  /// the API could not get one from any client.
  Widget _noBoards() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.squareKanban,
              size: 36,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 12),
            Text('No boards yet', style: AppTypography.h3),
            const SizedBox(height: 6),
            Text(
              'A board is a set of lanes you move cards through. You will own '
              'the one you create, and it starts with To Do, In Progress and '
              'Done.',
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: _busy ? null : _showCreateBoard,
              icon: const Icon(LucideIcons.plus, size: 18),
              label: const Text('Create a board'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _board(BoardData data, List<BoardLane> lanes, int laneIndex) {
    if (lanes.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('This board has no lanes', style: AppTypography.h3),
              const SizedBox(height: 6),
              Text(
                data.canManageLanes
                    ? 'Add one and cards have somewhere to go.'
                    : 'Ask the board owner to add one. Cards need a lane to '
                          'live in.',
                textAlign: TextAlign.center,
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              if (data.canManageLanes) ...[
                const SizedBox(height: 20),
                FilledButton.icon(
                  onPressed: _busy ? null : _showAddLane,
                  icon: const Icon(LucideIcons.plus, size: 18),
                  label: const Text('Add a lane'),
                ),
              ],
            ],
          ),
        ),
      );
    }

    return Column(
      children: [
        _laneStrip(data, lanes, laneIndex),
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

  /// The lane tabs. Doubles as the "where am I" indicator a PageView needs and
  /// as the way to reach a lane without swiping through the ones between.
  Widget _laneStrip(BoardData data, List<BoardLane> lanes, int laneIndex) {
    return Container(
      height: 48,
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.gray100)),
      ),
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        children: [
          for (var i = 0; i < lanes.length; i++)
            _laneTab(lanes[i], selected: i == laneIndex, onTap: () => _goTo(i)),
          if (data.canManageLanes)
            Padding(
              padding: const EdgeInsets.only(left: 4),
              child: ActionChip(
                avatar: const Icon(LucideIcons.plus, size: 14),
                label: const Text('Lane'),
                onPressed: _busy ? null : _showAddLane,
              ),
            ),
        ],
      ),
    );
  }

  Widget _laneTab(
    BoardLane lane, {
    required bool selected,
    required VoidCallback onTap,
  }) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
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
                  color: lane.swatch,
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
                _laneCount(lane),
                style: AppTypography.caption.copyWith(
                  color: lane.isOverLimit
                      ? AppColors.warning700
                      : AppColors.textTertiary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// `4` on its own, or `4/3` when the lane carries a WIP limit. The limit is
  /// advisory: no endpoint enforces it, so this reports it and never blocks a
  /// move.
  String _laneCount(BoardLane lane) => lane.limit == null
      ? '${lane.cards.length}'
      : '${lane.cards.length}/${lane.limit}';

  Widget _lanePage(BoardLane lane) {
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

    return ReorderableListView.builder(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 96),
      itemCount: lane.cards.length,
      onReorderItem: (oldIndex, newIndex) => _reorder(lane, oldIndex, newIndex),
      itemBuilder: (context, index) {
        final card = lane.cards[index];
        return _cardTile(card, key: ValueKey(card.id));
      },
    );
  }

  Widget _cardTile(BoardCard card, {required Key key}) {
    final chips = <String>[
      if (card.accountName != null && card.accountName!.isNotEmpty)
        card.accountName!,
      ...card.assignees,
    ];
    // Flutter refuses to paint a rounded Border whose sides differ in colour,
    // so the priority accent is a strip clipped by the rounded corners rather
    // than a left side on the border.
    return Container(
      key: key,
      margin: const EdgeInsets.only(bottom: 8),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.border),
      ),
      child: Stack(
        children: [
          Positioned(
            left: 0,
            top: 0,
            bottom: 0,
            width: 3,
            child: ColoredBox(color: card.priority.color),
          ),
          _cardBody(card, chips),
        ],
      ),
    );
  }

  Widget _cardBody(BoardCard card, List<String> chips) {
    return InkWell(
      onTap: _busy ? null : () => _showCardActions(card),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              card.title,
              style: AppTypography.body.copyWith(
                fontWeight: FontWeight.w500,
                decoration: card.isCompleted
                    ? TextDecoration.lineThrough
                    : null,
              ),
            ),
            if (card.description.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                card.description,
                maxLines: 2,
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
                if (card.dueDate != null)
                  _pill(
                    _dueLabel(card.dueDate!),
                    // Overdue is the server's answer, measured on its clock.
                    card.isOverdue
                        ? AppColors.danger500
                        : AppColors.textSecondary,
                  ),
                for (final chip in chips)
                  Text(
                    chip,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
                  ),
              ],
            ),
          ],
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

  String _dueLabel(DateTime due) {
    final local = due.toLocal();
    return '${local.day}/${local.month}';
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

  // ------------------------------------------------------------------ writes

  /// `onReorderItem` hands over the index with the dragged card already taken
  /// out of the list, which is the same list the server splices into. The
  /// older `onReorder` reports it as if the card were still there, so it needs
  /// a subtraction this does not.
  Future<void> _reorder(BoardLane lane, int oldIndex, int newIndex) async {
    final card = lane.cards[oldIndex];
    await _write(
      () => ref
          .read(boardProvider.notifier)
          .moveCard(cardId: card.id, laneId: lane.id, index: newIndex),
      failure: 'Could not move that card.',
    );
  }

  void _showCardActions(BoardCard card) {
    final data = ref.read(boardProvider).value;
    final others = (data?.lanes ?? const <BoardLane>[])
        .where((lane) => lane.id != card.laneId)
        .toList();
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
                child: Text(card.title, style: AppTypography.h3),
              ),
              if (card.description.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                  child: Text(
                    card.description,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ),
              if (others.isNotEmpty) ...[
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                  child: Text('Move to', style: AppTypography.labelSmall),
                ),
                for (final lane in others)
                  ListTile(
                    leading: Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: lane.swatch,
                        shape: BoxShape.circle,
                      ),
                    ),
                    title: Text(lane.name),
                    onTap: () {
                      Navigator.pop(sheetContext);
                      _move(card, lane);
                    },
                  ),
              ],
              ListTile(
                leading: Icon(
                  LucideIcons.trash2,
                  size: 20,
                  color: AppColors.danger600,
                ),
                title: const Text('Delete card'),
                onTap: () {
                  Navigator.pop(sheetContext);
                  _confirmDelete(card);
                },
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  /// Moving to another lane appends: the card goes to the end, which is where
  /// someone reading that lane top to bottom expects the newest arrival.
  Future<void> _move(BoardCard card, BoardLane target) async {
    final moved = await _write(
      () => ref
          .read(boardProvider.notifier)
          .moveCard(
            cardId: card.id,
            laneId: target.id,
            index: target.cards.length,
          ),
      success: 'Moved to ${target.name}.',
      failure: 'Could not move that card.',
    );
    if (!moved || !mounted) return;
    final lanes = ref.read(boardProvider).value?.lanes ?? const <BoardLane>[];
    final index = lanes.indexWhere((lane) => lane.id == target.id);
    if (index >= 0) _goTo(index);
  }

  void _confirmDelete(BoardCard card) {
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Delete card?'),
        content: Text('"${card.title}" will be removed from this board.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              Navigator.pop(dialogContext);
              _write(
                () => ref.read(boardProvider.notifier).deleteCard(card.id),
                success: 'Card deleted.',
                failure: 'Could not delete that card.',
              );
            },
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
  }

  void _showBoardPicker() {
    final data = ref.read(boardProvider).value;
    final boards = data?.boards ?? const <BoardSummary>[];
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
                child: Text('Boards', style: AppTypography.h3),
              ),
              for (final board in boards)
                ListTile(
                  leading: Icon(
                    board.id == data?.active?.id
                        ? LucideIcons.check
                        : LucideIcons.squareKanban,
                    size: 20,
                    color: board.id == data?.active?.id
                        ? AppColors.primary600
                        : AppColors.textSecondary,
                  ),
                  title: Text(board.name),
                  subtitle: Text(
                    '${board.taskCount} '
                    '${board.taskCount == 1 ? 'card' : 'cards'}',
                  ),
                  onTap: () {
                    Navigator.pop(sheetContext);
                    setState(() => _laneIndex = 0);
                    ref.read(boardProvider.notifier).select(board.id);
                  },
                ),
              ListTile(
                leading: Icon(
                  LucideIcons.plus,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                title: const Text('New board'),
                onTap: () {
                  Navigator.pop(sheetContext);
                  _showCreateBoard();
                },
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  void _showCreateBoard() {
    _showNameSheet(
      title: 'New board',
      hint: 'Product launch',
      buttonLabel: 'Create board',
      onSubmit: (name) => _write(
        () => ref.read(boardProvider.notifier).createBoard(name: name),
        success: 'Board created.',
        failure: 'Could not create that board.',
        onSuccess: () => setState(() => _laneIndex = 0),
      ),
    );
  }

  void _showAddLane() {
    _showNameSheet(
      title: 'New lane',
      hint: 'In Review',
      buttonLabel: 'Add lane',
      onSubmit: (name) => _write(
        () => ref.read(boardProvider.notifier).createLane(name: name),
        success: 'Lane added.',
        failure: 'Could not add that lane.',
      ),
    );
  }

  /// One field, one button. Both the board and the lane sheets are exactly
  /// that, and the server refuses the rest (a duplicate lane name, a member
  /// adding a lane) in its own words.
  void _showNameSheet({
    required String title,
    required String hint,
    required String buttonLabel,
    required void Function(String name) onSubmit,
  }) {
    final controller = TextEditingController();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) => Padding(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(sheetContext).viewInsets.bottom,
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: AppTypography.h3),
                const SizedBox(height: 12),
                TextField(
                  controller: controller,
                  autofocus: true,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: InputDecoration(
                    labelText: 'Name',
                    hintText: hint,
                  ),
                ),
                const SizedBox(height: 20),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    onPressed: () {
                      final name = controller.text.trim();
                      Navigator.pop(sheetContext);
                      if (name.isEmpty) {
                        _snack('Give it a name first.');
                        return;
                      }
                      onSubmit(name);
                    },
                    child: Text(buttonLabel),
                  ),
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
        ),
      ),
    ).whenComplete(controller.dispose);
  }

  void _showAddCard(BoardLane lane) {
    final titleController = TextEditingController();
    final descriptionController = TextEditingController();
    var priority = Priority.medium;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheetContext) => Padding(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(sheetContext).viewInsets.bottom,
        ),
        child: StatefulBuilder(
          builder: (builderContext, setSheetState) => SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('New card in ${lane.name}', style: AppTypography.h3),
                  const SizedBox(height: 12),
                  TextField(
                    controller: titleController,
                    autofocus: true,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(labelText: 'Title'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: descriptionController,
                    maxLines: 3,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      labelText: 'Description',
                      alignLabelWithHint: true,
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Priority', style: AppTypography.labelSmall),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    children: Priority.values
                        .map(
                          (p) => ChoiceChip(
                            label: Text(p.label),
                            selected: priority == p,
                            onSelected: (_) =>
                                setSheetState(() => priority = p),
                          ),
                        )
                        .toList(),
                  ),
                  const SizedBox(height: 20),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: () {
                        final title = titleController.text.trim();
                        final description = descriptionController.text.trim();
                        Navigator.pop(sheetContext);
                        if (title.isEmpty) {
                          _snack('A card needs a title.');
                          return;
                        }
                        _write(
                          () => ref
                              .read(boardProvider.notifier)
                              .createCard(
                                laneId: lane.id,
                                title: title,
                                description: description,
                                priority: boardPriorityValue(priority),
                              ),
                          success: 'Card added to ${lane.name}.',
                          failure: 'Could not add that card.',
                        );
                      },
                      child: const Text('Add card'),
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
              ),
            ),
          ),
        ),
      ),
    ).whenComplete(() {
      titleController.dispose();
      descriptionController.dispose();
    });
  }

  /// Every write goes through here: one busy flag, and the server's own words
  /// on failure. It knows what this screen cannot, that a lane name is taken
  /// or that a member may not add one.
  Future<bool> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() action, {
    String? success,
    required String failure,
    VoidCallback? onSuccess,
  }) async {
    setState(() => _busy = true);
    final response = await action();
    if (!mounted) return response.success;
    setState(() => _busy = false);
    if (response.success) {
      onSuccess?.call();
      if (success != null) _snack(success);
    } else {
      _snack(response.message ?? failure);
    }
    return response.success;
  }

  void _snack(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
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
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.circleAlert,
              size: 36,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 12),
            Text(
              message,
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),
            FilledButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
