import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/auth_provider.dart';
import '../../providers/deal_pipelines_provider.dart';
import '../../providers/deals_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../widgets/cards/deal_card.dart';
import '../../widgets/misc/kanban_column.dart';
import '../../widgets/common/common.dart';
import '../../widgets/common/export_csv_button.dart';

enum ViewMode { kanban, list }

const _kViewModePrefKey = 'deals.view_mode';
const _kSortPrefKey = 'deals.sort';

/// Deals List Screen
/// Pipeline view with Kanban board or list layout.
class DealsListScreen extends ConsumerStatefulWidget {
  const DealsListScreen({super.key});

  @override
  ConsumerState<DealsListScreen> createState() => _DealsListScreenState();
}

class _DealsListScreenState extends ConsumerState<DealsListScreen> {
  final TextEditingController _searchController = TextEditingController();
  final ScrollController _kanbanScrollController = ScrollController();
  final ScrollController _listScrollController = ScrollController();

  bool _showSearch = false;
  ViewMode _viewMode = ViewMode.kanban;
  DealSort _sort = DealSort.closeDateAsc;
  bool _isLoadingMore = false;
  int? _lastLoadMoreOffset;

  // Bulk select state, populated when the user long-presses a card.
  final Set<String> _selectedIds = {};

  // Kanban stage pager state.
  int _currentKanbanStage = 0;

  // Search debounce, server-side query fires once typing pauses.
  Timer? _searchDebounce;

  /// The pipeline on screen: the one the list was last fetched for, else the
  /// org's default. `build` watches the pipelines so this stays current.
  DealPipeline? get _pipeline => activeDealPipeline(
    ref.read(dealPipelinesProvider).value ?? const [],
    ref.read(dealsProvider.notifier).pipelineId,
  );

  /// Board columns: every stage but the lost ones, in board order, so won
  /// stages sit at the end where reps see the wins. The list shows them all.
  List<DealPipelineStage> get _kanbanStages =>
      _listStages.where((s) => !s.isLost).toList();
  List<DealPipelineStage> get _listStages => _pipeline?.stages ?? const [];

  Color _colorOf(DealPipelineStage stage) =>
      _pipeline?.colorOf(stage) ?? dealStageColor(stage.kind, 0);

  @override
  void initState() {
    super.initState();
    _listScrollController.addListener(_handleListScroll);
    _kanbanScrollController.addListener(_handleKanbanScroll);
    _restorePrefs();
  }

  Future<void> _restorePrefs() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_kViewModePrefKey);
    final sort = prefs.getString(_kSortPrefKey);
    if (!mounted) return;
    setState(() {
      if (saved == 'list') _viewMode = ViewMode.list;
      if (sort != null) {
        _sort = DealSort.values.firstWhere(
          (s) => s.name == sort,
          orElse: () => DealSort.closeDateAsc,
        );
      }
    });
    // When restoring Kanban, ensure we have all pages. Pagination on the
    // horizontal scroll alone won't backfill a column the user hasn't seen.
    if (_viewMode == ViewMode.kanban) {
      // Defer until after the first frame so the loading indicator covers it.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _ensureAllLoadedForKanban();
      });
    }
  }

  Future<void> _savePref(String key, String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(key, value);
  }

  @override
  void dispose() {
    _searchController.dispose();
    _searchDebounce?.cancel();
    _kanbanScrollController
      ..removeListener(_handleKanbanScroll)
      ..dispose();
    _listScrollController
      ..removeListener(_handleListScroll)
      ..dispose();
    super.dispose();
  }

  /// Sort comparator used inside each stage group.
  int _compareDeals(Deal a, Deal b) {
    switch (_sort) {
      case DealSort.closeDateAsc:
        final ad = a.closeDate;
        final bd = b.closeDate;
        if (ad == null && bd == null) return 0;
        if (ad == null) return 1;
        if (bd == null) return -1;
        return ad.compareTo(bd);
      case DealSort.valueDesc:
        return b.value.compareTo(a.value);
      case DealSort.stageAgeDesc:
        final ad = a.stageChangedAt ?? a.createdAt;
        final bd = b.stageChangedAt ?? b.createdAt;
        return ad.compareTo(bd); // older first
      case DealSort.updatedDesc:
        return b.updatedAt.compareTo(a.updatedAt);
    }
  }

  /// Deals keyed by stage code, for the stages of the pipeline on screen.
  Map<String, List<Deal>> _groupByStage(List<Deal> deals) {
    final Map<String, List<Deal>> grouped = {
      for (final s in _listStages) s.code: <Deal>[],
    };
    for (final d in deals) {
      grouped[d.stage]?.add(d);
    }
    for (final entry in grouped.entries) {
      entry.value.sort(_compareDeals);
    }
    return grouped;
  }

  void _handleListScroll() {
    if (!_listScrollController.hasClients) return;
    final position = _listScrollController.position;
    if (position.extentAfter > 400) return;
    _maybeLoadMore();
  }

  void _handleKanbanScroll() {
    if (!_kanbanScrollController.hasClients) return;
    final pos = _kanbanScrollController.position;
    // Update which stage is "current" based on scroll offset (PageScrollPhysics
    // snaps to columns so the offset is a clean multiple of columnWidth).
    final columnWidth = MediaQuery.of(context).size.width * 0.85 + 12;
    final last = _kanbanStages.length - 1;
    final idx = last < 0
        ? 0
        : (pos.pixels / columnWidth).round().clamp(0, last);
    if (idx != _currentKanbanStage) {
      setState(() => _currentKanbanStage = idx);
    }
    // Eager-load pages so columns to the right have data when the user gets
    // there.
    if (pos.extentAfter < 600) {
      _maybeLoadMore();
    }
  }

  Future<void> _maybeLoadMore() async {
    final data = ref.read(dealsProvider).value;
    if (data == null || !data.hasMore) return;
    if (ref.read(dealsLoadingProvider)) return;
    if (_lastLoadMoreOffset == data.currentOffset) return;
    if (_isLoadingMore) return;
    _isLoadingMore = true;
    _lastLoadMoreOffset = data.currentOffset;
    try {
      await ref.read(dealsProvider.notifier).loadMore();
    } finally {
      if (mounted) _isLoadingMore = false;
    }
  }

  Future<void> _ensureAllLoadedForKanban() async {
    final data = ref.read(dealsProvider).value;
    if (data == null || !data.hasMore) return;
    if (ref.read(dealsLoadingProvider)) return;
    await ref.read(dealsProvider.notifier).loadAll();
  }

  Future<void> _handleDealMoved(Deal deal, DealPipelineStage newStage) async {
    if (deal.stage == newStage.code) return;
    final previous = _pipeline?.stageByCode(deal.stage);

    // Closed stages are destructive. Confirm before firing.
    if (newStage.isClosed) {
      final confirmed = await _confirmCloseStage(deal, newStage);
      if (!confirmed) return;
    }

    final result = await ref
        .read(dealsProvider.notifier)
        .updateDealStage(deal.id, newStage);
    if (!mounted) return;

    if (!result.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(result.error ?? 'Failed to move "${deal.title}"'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }

    // Picking a card up selects it (KanbanColumn's onDragStarted), so drop the
    // selection once the move lands. Otherwise the selection app bar is left
    // stranded over a board the user has already finished editing. Mirrors
    // _bulkChangeStage, which also clears once the stage change completes.
    _clearSelection();

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Moved "${deal.title}" to ${newStage.label}'),
        behavior: SnackBarBehavior.floating,
        action: previous == null
            ? null
            : SnackBarAction(
                label: 'Undo',
                onPressed: () {
                  ref
                      .read(dealsProvider.notifier)
                      .updateDealStage(deal.id, previous);
                },
              ),
      ),
    );
  }

  Future<bool> _confirmCloseStage(Deal deal, DealPipelineStage target) async {
    final won = target.isWon;
    final result = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(won ? 'Mark as Won?' : 'Mark as Lost?'),
        content: Text(
          won
              ? 'Closing "${deal.title}" as Won will lock in ${_formatValue(deal)}. You can still reopen it later.'
              : 'Closing "${deal.title}" as Lost will move it out of the active pipeline.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(
              won ? 'Mark Won' : 'Mark Lost',
              style: TextStyle(
                color: won ? AppColors.success600 : AppColors.danger600,
              ),
            ),
          ),
        ],
      ),
    );
    return result ?? false;
  }

  String _formatValue(Deal deal) {
    final s = deal.currency.symbol;
    if (deal.value >= 1000) {
      return '$s${(deal.value / 1000).toStringAsFixed(0)}K';
    }
    return '$s${deal.value.toStringAsFixed(0)}';
  }

  // ---------------------------------------------------------------------
  // Search wiring, debounce input, then drive provider's filters server-side.
  // ---------------------------------------------------------------------

  void _onSearchChanged(String value) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 350), () {
      final notifier = ref.read(dealsProvider.notifier);
      notifier.setFilters(
        notifier.filters.copyWith(search: value.isEmpty ? null : value),
      );
    });
  }

  // ---------------------------------------------------------------------
  // Bulk select helpers
  // ---------------------------------------------------------------------

  bool get _selectionMode => _selectedIds.isNotEmpty;

  void _toggleSelection(Deal deal) {
    setState(() {
      if (_selectedIds.contains(deal.id)) {
        _selectedIds.remove(deal.id);
      } else {
        _selectedIds.add(deal.id);
      }
    });
  }

  void _clearSelection() {
    setState(_selectedIds.clear);
  }

  Future<void> _bulkChangeStage() async {
    final stages = _listStages;
    final stage = await showModalBottomSheet<DealPipelineStage>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: stages.map((s) {
              return ListTile(
                leading: Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    color: _colorOf(s),
                    shape: BoxShape.circle,
                  ),
                ),
                title: Text(s.label),
                onTap: () => Navigator.pop(context, s),
              );
            }).toList(),
          ),
        ),
      ),
    );
    if (stage == null) return;
    final notifier = ref.read(dealsProvider.notifier);
    final ids = List<String>.from(_selectedIds);
    var failures = 0;
    for (final id in ids) {
      final r = await notifier.updateDealStage(id, stage);
      if (!r.success) failures++;
    }
    if (!mounted) return;
    _clearSelection();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          failures == 0
              ? 'Moved ${ids.length} deal${ids.length == 1 ? '' : 's'} to ${stage.label}'
              : '$failures of ${ids.length} failed to move',
        ),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _bulkDelete() async {
    final ids = List<String>.from(_selectedIds);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete deals?'),
        content: Text(
          'Permanently delete ${ids.length} deal${ids.length == 1 ? '' : 's'}? This cannot be undone.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    final notifier = ref.read(dealsProvider.notifier);
    var failures = 0;
    for (final id in ids) {
      final r = await notifier.deleteDeal(id);
      if (!r.success) failures++;
    }
    if (!mounted) return;
    _clearSelection();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          failures == 0
              ? 'Deleted ${ids.length} deal${ids.length == 1 ? '' : 's'}'
              : '$failures of ${ids.length} failed to delete',
        ),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    // Read provider state ONCE per build to avoid re-watching from multiple
    // getters, the previous _filteredDeals getter watched on every call.
    final allDeals = ref.watch(dealsListProvider);
    // Watched so the columns follow a pipeline load or an admin's edit.
    final pipelines = ref.watch(dealPipelinesProvider).value ?? const [];
    final dealsByStage = _groupByStage(allDeals);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: _selectionMode ? _buildSelectionAppBar() : _buildNormalAppBar(),
      body: Column(
        children: [
          // Collapsible search bar
          AnimatedCrossFade(
            firstChild: const SizedBox.shrink(),
            secondChild: _buildSearchBar(),
            crossFadeState: _showSearch
                ? CrossFadeState.showSecond
                : CrossFadeState.showFirst,
            duration: AppDurations.normal,
          ),

          // Pipeline switcher, only when there is a choice to make.
          if (pipelines.length > 1 && !_selectionMode)
            _buildPipelineSwitcher(pipelines),

          // Quick-filter row ("Mine" + filter + sort)
          _buildQuickFilterRow(),

          // Pipeline summary
          _buildPipelineSummary(),

          // View content
          Expanded(
            child: AnimatedSwitcher(
              duration: AppDurations.normal,
              child: _viewMode == ViewMode.kanban
                  ? _buildKanbanView(dealsByStage)
                  : _buildListView(dealsByStage, allDeals),
            ),
          ),

          // Stage pager for kanban
          if (_viewMode == ViewMode.kanban && !_selectionMode)
            _buildStagePager(),
        ],
      ),
    );
  }

  PreferredSizeWidget _buildNormalAppBar() {
    return AppBar(
      title: const Text('Deals'),
      backgroundColor: AppColors.surface,
      elevation: 0,
      scrolledUnderElevation: 1,
      actions: [
        IconButton(
          icon: Icon(
            _showSearch ? LucideIcons.x : LucideIcons.search,
            size: 22,
          ),
          onPressed: () {
            setState(() {
              _showSearch = !_showSearch;
              if (!_showSearch) {
                _searchController.clear();
                _onSearchChanged('');
              }
            });
          },
        ),
        IconButton(
          icon: Icon(
            _viewMode == ViewMode.kanban
                ? LucideIcons.list
                : LucideIcons.layoutGrid,
            size: 22,
          ),
          onPressed: _toggleViewMode,
        ),
        ExportCsvButton(
          endpoint: ApiConfig.opportunitiesExport,
          filePrefix: 'deals',
          query: () => ref.read(dealsProvider.notifier).filterQuery(),
        ),
        IconButton(
          icon: const Icon(LucideIcons.plus, size: 22),
          onPressed: () async {
            final result = await context.push('/deals/create');
            if (result == true) {
              await ref.read(dealsProvider.notifier).refresh();
            }
          },
        ),
      ],
    );
  }

  PreferredSizeWidget _buildSelectionAppBar() {
    return AppBar(
      backgroundColor: AppColors.surface,
      elevation: 0,
      scrolledUnderElevation: 1,
      leading: IconButton(
        icon: const Icon(LucideIcons.x),
        onPressed: _clearSelection,
      ),
      title: Text('${_selectedIds.length} selected'),
      actions: [
        IconButton(
          icon: const Icon(LucideIcons.arrowRightLeft, size: 20),
          tooltip: 'Change stage',
          onPressed: _bulkChangeStage,
        ),
        IconButton(
          icon: Icon(LucideIcons.trash2, size: 20, color: AppColors.danger600),
          tooltip: 'Delete',
          onPressed: _bulkDelete,
        ),
      ],
    );
  }

  Future<void> _toggleViewMode() async {
    setState(() {
      _viewMode = _viewMode == ViewMode.kanban
          ? ViewMode.list
          : ViewMode.kanban;
    });
    await _savePref(_kViewModePrefKey, _viewMode.name);
    if (_viewMode == ViewMode.kanban) {
      _ensureAllLoadedForKanban();
    }
  }

  // ---------------------------------------------------------------------
  // Search bar / quick filters
  // ---------------------------------------------------------------------

  Widget _buildSearchBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: TextField(
        controller: _searchController,
        autofocus: true,
        style: AppTypography.body,
        onChanged: _onSearchChanged,
        decoration: InputDecoration(
          hintText: 'Search deals...',
          hintStyle: AppTypography.body.copyWith(color: AppColors.textTertiary),
          prefixIcon: Icon(
            LucideIcons.search,
            color: AppColors.textTertiary,
            size: 18,
          ),
          suffixIcon: _searchController.text.isNotEmpty
              ? IconButton(
                  icon: Icon(
                    LucideIcons.x,
                    color: AppColors.textTertiary,
                    size: 16,
                  ),
                  onPressed: () {
                    _searchController.clear();
                    _onSearchChanged('');
                  },
                )
              : null,
          filled: true,
          fillColor: AppColors.gray100,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide.none,
          ),
        ),
      ),
    );
  }

  Widget _buildQuickFilterRow() {
    final filters = ref.watch(dealsProvider.notifier).filters;
    final me = ref.watch(currentUserProvider);
    String? myProfileId;
    if (me != null) {
      for (final u in ref.watch(usersProvider)) {
        if (u.email.toLowerCase() == me.email.toLowerCase()) {
          myProfileId = u.id;
          break;
        }
      }
    }
    final mineActive =
        myProfileId != null &&
        filters.assignedToIds.length == 1 &&
        filters.assignedToIds.first == myProfileId;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            // Mine
            _QuickChip(
              icon: LucideIcons.user,
              label: 'Mine',
              selected: mineActive,
              onTap: myProfileId == null
                  ? null
                  : () {
                      final notifier = ref.read(dealsProvider.notifier);
                      notifier.setFilters(
                        notifier.filters.copyWith(
                          assignedToIds: mineActive ? const [] : [myProfileId!],
                        ),
                      );
                    },
            ),
            const SizedBox(width: 6),
            // Rotten
            _QuickChip(
              icon: LucideIcons.alertOctagon,
              label: 'Stalled',
              selected: filters.rottenOnly,
              onTap: () {
                final notifier = ref.read(dealsProvider.notifier);
                notifier.setFilters(
                  notifier.filters.copyWith(rottenOnly: !filters.rottenOnly),
                );
              },
            ),
            const SizedBox(width: 6),
            // Filter sheet
            _QuickChip(
              icon: LucideIcons.slidersHorizontal,
              label: filters.badgeCount == 0
                  ? 'Filters'
                  : 'Filters · ${filters.badgeCount}',
              selected: filters.badgeCount > 0,
              onTap: _openFilterSheet,
            ),
            const SizedBox(width: 6),
            // Sort
            _QuickChip(
              icon: LucideIcons.arrowDownUp,
              label: _sort.label,
              selected: false,
              onTap: _openSortSheet,
            ),
            if (!filters.isEmpty) ...[
              const SizedBox(width: 6),
              TextButton.icon(
                onPressed: () {
                  _searchController.clear();
                  ref
                      .read(dealsProvider.notifier)
                      .setFilters(const DealFilters());
                },
                icon: const Icon(LucideIcons.x, size: 14),
                label: const Text('Clear'),
                style: TextButton.styleFrom(
                  foregroundColor: AppColors.textSecondary,
                  minimumSize: const Size(0, 28),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _openSortSheet() async {
    final chosen = await showModalBottomSheet<DealSort>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12, bottom: 8),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text('Sort by', style: AppTypography.h3),
            ),
            for (final s in DealSort.values)
              ListTile(
                title: Text(s.label),
                trailing: _sort == s
                    ? Icon(LucideIcons.check, color: AppColors.primary600)
                    : null,
                onTap: () => Navigator.pop(context, s),
              ),
          ],
        ),
      ),
    );
    if (chosen == null) return;
    setState(() => _sort = chosen);
    _savePref(_kSortPrefKey, chosen.name);
  }

  Future<void> _openFilterSheet() async {
    final notifier = ref.read(dealsProvider.notifier);
    final updated = await showModalBottomSheet<DealFilters>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _DealFilterSheet(
        initial: notifier.filters,
        stages: _listStages,
        colorOf: _colorOf,
        users: ref.read(usersProvider),
        tags: ref.read(tagsProvider),
      ),
    );
    if (updated == null) return;
    notifier.setFilters(updated);
  }

  // ---------------------------------------------------------------------
  // Pipeline switcher
  // ---------------------------------------------------------------------

  Widget _buildPipelineSwitcher(List<DealPipeline> pipelines) {
    final current = _pipeline;
    return Material(
      color: AppColors.surface,
      child: InkWell(
        onTap: () => _openPipelineSheet(pipelines),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 44),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Row(
              children: [
                Icon(
                  LucideIcons.squareKanban,
                  size: 16,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    current?.name ?? 'Pipeline',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.label.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                Icon(
                  LucideIcons.chevronsUpDown,
                  size: 16,
                  color: AppColors.textSecondary,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _openPipelineSheet(List<DealPipeline> pipelines) async {
    final currentId = _pipeline?.id;
    final chosen = await showModalBottomSheet<DealPipeline>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text('Pipeline', style: AppTypography.h3),
              ),
              for (final p in pipelines)
                ListTile(
                  title: Text(p.name),
                  subtitle: p.isDefault ? const Text('Default') : null,
                  trailing: p.id == currentId
                      ? Icon(LucideIcons.check, color: AppColors.primary600)
                      : null,
                  onTap: () => Navigator.pop(context, p),
                ),
            ],
          ),
        ),
      ),
    );
    if (chosen == null || chosen.id == currentId) return;
    setState(() => _currentKanbanStage = 0);
    if (_kanbanScrollController.hasClients) _kanbanScrollController.jumpTo(0);
    await ref.read(dealsProvider.notifier).setPipeline(chosen.id);
    if (_viewMode == ViewMode.kanban) await _ensureAllLoadedForKanban();
  }

  /// Pull-to-refresh reloads the pipelines too, so an admin's change to the
  /// stages reaches the columns without restarting the app.
  Future<void> _refreshAll() async {
    await ref.read(dealPipelinesProvider.notifier).refresh();
    await ref.read(dealsProvider.notifier).refresh();
  }

  // ---------------------------------------------------------------------
  // Pipeline summary chips
  // ---------------------------------------------------------------------

  Widget _buildPipelineSummary() {
    final summary = ref.watch(pipelineSummaryProvider);
    final activeCount = ref.watch(activeDealsTotalCountProvider);
    final primary = summary.primary;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            if (primary != null) ...[
              _SummaryChip(
                icon: LucideIcons.dollarSign,
                label: _formatCurrency(
                  primary.totalValue,
                  primary.currency.symbol,
                ),
                sublabel: summary.loadedSubset ? '~ Pipeline' : 'Pipeline',
              ),
              const SizedBox(width: 8),
              _SummaryChip(
                icon: LucideIcons.target,
                label: _formatCurrency(
                  primary.weightedValue,
                  primary.currency.symbol,
                ),
                sublabel: 'Forecast',
              ),
              const SizedBox(width: 8),
            ],
            _SummaryChip(
              icon: LucideIcons.briefcase,
              label: '$activeCount',
              sublabel: 'Active',
            ),
            if (summary.isMixed) ...[
              const SizedBox(width: 8),
              _SummaryChip(
                icon: LucideIcons.globe,
                label: '${summary.buckets.length}',
                sublabel: 'Currencies',
              ),
            ],
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Kanban
  // ---------------------------------------------------------------------

  Widget _buildKanbanView(Map<String, List<Deal>> dealsByStage) {
    final isLoading = ref.watch(dealsLoadingProvider);
    final error = ref.watch(dealsErrorProvider);
    final screenWidth = MediaQuery.of(context).size.width;
    final columnWidth = screenWidth * 0.85;
    final hasAny = dealsByStage.values.any((l) => l.isNotEmpty);

    if (isLoading && !hasAny) {
      return const Center(child: CircularProgressIndicator());
    }

    if (error != null && !hasAny) {
      return _errorState(error);
    }

    return RefreshIndicator(
      onRefresh: () async {
        await _refreshAll();
        await _ensureAllLoadedForKanban();
      },
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverFillRemaining(
            child: SingleChildScrollView(
              controller: _kanbanScrollController,
              scrollDirection: Axis.horizontal,
              physics: const PageScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(8, 8, 8, 16),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: _kanbanStages.map((stage) {
                  return KanbanColumn(
                    stage: stage,
                    color: _colorOf(stage),
                    deals: dealsByStage[stage.code] ?? const [],
                    width: columnWidth,
                    selectedIds: _selectedIds,
                    onDealTap: (deal) {
                      if (_selectionMode) {
                        _toggleSelection(deal);
                      } else {
                        context.push('/deals/${deal.id}');
                      }
                    },
                    onDealLongPress: _toggleSelection,
                    onDealMoved: _handleDealMoved,
                  );
                }).toList(),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStagePager() {
    final stages = _kanbanStages;
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: List.generate(stages.length, (i) {
          final stage = stages[i];
          final active = i == _currentKanbanStage;
          return Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: AnimatedContainer(
              duration: AppDurations.fast,
              width: active ? 24 : 8,
              height: 8,
              decoration: BoxDecoration(
                color: active ? _colorOf(stage) : AppColors.gray300,
                borderRadius: BorderRadius.circular(4),
              ),
            ),
          );
        }),
      ),
    );
  }

  // ---------------------------------------------------------------------
  // List
  // ---------------------------------------------------------------------

  Widget _buildListView(
    Map<String, List<Deal>> dealsByStage,
    List<Deal> allDeals,
  ) {
    final isLoading = ref.watch(dealsLoadingProvider);
    final error = ref.watch(dealsErrorProvider);

    if (isLoading && allDeals.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (error != null && allDeals.isEmpty) {
      return _errorState(error);
    }

    final filters = ref.watch(dealsProvider.notifier).filters;
    if (allDeals.isEmpty) {
      return EmptyState(
        icon: LucideIcons.briefcase,
        title: filters.isEmpty ? 'No deals yet' : 'No results found',
        description: filters.isEmpty
            ? 'Start by creating your first deal'
            : 'Try adjusting your filters',
        actionLabel: filters.isEmpty ? 'Add Deal' : 'Clear filters',
        onAction: filters.isEmpty
            ? () async {
                final result = await context.push('/deals/create');
                if (result == true) {
                  await ref.read(dealsProvider.notifier).refresh();
                }
              }
            : () {
                _searchController.clear();
                ref
                    .read(dealsProvider.notifier)
                    .setFilters(const DealFilters());
              },
      );
    }

    final stages = _listStages;
    return RefreshIndicator(
      onRefresh: _refreshAll,
      child: ListView.builder(
        controller: _listScrollController,
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 100),
        itemCount: stages.length,
        itemBuilder: (context, index) {
          final stage = stages[index];
          final stageDeals = dealsByStage[stage.code] ?? const [];
          if (stageDeals.isEmpty) return const SizedBox.shrink();

          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _buildStageHeader(stage, stageDeals),
              ...stageDeals.map((deal) {
                return DealCard(
                  deal: deal,
                  isSelected: _selectedIds.contains(deal.id),
                  onTap: () {
                    if (_selectionMode) {
                      _toggleSelection(deal);
                    } else {
                      context.push('/deals/${deal.id}');
                    }
                  },
                  onLongPress: () => _toggleSelection(deal),
                );
              }),
              const SizedBox(height: 16),
            ],
          );
        },
      ),
    );
  }

  Widget _buildStageHeader(DealPipelineStage stage, List<Deal> deals) {
    // Dominant-currency total for this stage row to keep mixed-currency orgs
    // honest. Falls back to org symbol when the stage has no deals.
    final Map<Currency, double> totals = {};
    for (final d in deals) {
      totals[d.currency] = (totals[d.currency] ?? 0) + d.value;
    }
    final entries = totals.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final primary = entries.isEmpty
        ? null
        : (currency: entries.first.key, total: entries.first.value);
    final mixed = entries.length > 1;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.gray100,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              color: _colorOf(stage),
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              stage.label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTypography.label.copyWith(fontWeight: FontWeight.w600),
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: AppColors.gray200,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(
              '${deals.length}',
              style: AppTypography.caption.copyWith(
                fontWeight: FontWeight.w600,
                color: AppColors.textSecondary,
              ),
            ),
          ),
          const Spacer(),
          if (primary != null)
            Text(
              '${_formatCurrency(primary.total, primary.currency.symbol)}${mixed ? ' + mix' : ''}',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
                fontWeight: FontWeight.w500,
              ),
            ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------
  // Shared
  // ---------------------------------------------------------------------

  Widget _errorState(String error) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(LucideIcons.alertCircle, size: 48, color: AppColors.danger500),
          const SizedBox(height: 16),
          Text(
            error,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: 16),
          ElevatedButton(onPressed: _refreshAll, child: const Text('Retry')),
        ],
      ),
    );
  }

  String _formatCurrency(double value, String symbol) {
    if (value >= 1000000) {
      return '$symbol${(value / 1000000).toStringAsFixed(1)}M';
    } else if (value >= 1000) {
      return '$symbol${(value / 1000).toStringAsFixed(0)}K';
    } else {
      return '$symbol${value.toStringAsFixed(0)}';
    }
  }
}

// ---------------------------------------------------------------------
// Quick filter chip
// ---------------------------------------------------------------------

class _QuickChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  const _QuickChip({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: AnimatedContainer(
        duration: AppDurations.fast,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: selected ? AppColors.primary50 : AppColors.gray50,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: selected ? AppColors.primary500 : AppColors.border,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon,
              size: 14,
              color: onTap == null
                  ? AppColors.gray400
                  : selected
                  ? AppColors.primary600
                  : AppColors.textSecondary,
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: onTap == null
                    ? AppColors.gray400
                    : selected
                    ? AppColors.primary600
                    : AppColors.textPrimary,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------
// Summary chip
// ---------------------------------------------------------------------

class _SummaryChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final String sublabel;

  const _SummaryChip({
    required this.icon,
    required this.label,
    required this.sublabel,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.gray50,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 18, color: AppColors.primary600),
          const SizedBox(width: 8),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: AppTypography.label.copyWith(
                  fontWeight: FontWeight.w600,
                ),
              ),
              Text(
                sublabel,
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------
// Filter sheet
// ---------------------------------------------------------------------

class _DealFilterSheet extends StatefulWidget {
  final DealFilters initial;

  /// The stages of the pipeline on screen. A stage filter is one of these.
  final List<DealPipelineStage> stages;
  final Color Function(DealPipelineStage) colorOf;
  final List<UserLookup> users;
  final List<TagLookup> tags;

  const _DealFilterSheet({
    required this.initial,
    required this.stages,
    required this.colorOf,
    required this.users,
    required this.tags,
  });

  @override
  State<_DealFilterSheet> createState() => _DealFilterSheetState();
}

class _DealFilterSheetState extends State<_DealFilterSheet> {
  late DealFilters _draft;
  final _amountMinController = TextEditingController();
  final _amountMaxController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _draft = widget.initial;
    if (_draft.amountMin != null) {
      _amountMinController.text = _draft.amountMin!.toStringAsFixed(0);
    }
    if (_draft.amountMax != null) {
      _amountMaxController.text = _draft.amountMax!.toStringAsFixed(0);
    }
  }

  @override
  void dispose() {
    _amountMinController.dispose();
    _amountMaxController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.85,
      minChildSize: 0.5,
      maxChildSize: 0.95,
      expand: false,
      builder: (context, scrollController) => SafeArea(
        top: false,
        child: Column(
          children: [
            Container(
              margin: const EdgeInsets.only(top: 12),
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Row(
                children: [
                  Text('Filter deals', style: AppTypography.h3),
                  const Spacer(),
                  TextButton(
                    // Themed buttons carry an infinite minimum width, which a Row
                    // does not bound. Without this the row fails to lay out and the
                    // screen paints nothing. See AppLayout.buttonMinSizeInRow.
                    style: TextButton.styleFrom(
                      minimumSize: AppLayout.buttonMinSizeInRow,
                    ),
                    onPressed: () =>
                        setState(() => _draft = const DealFilters()),
                    child: const Text('Reset'),
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                controller: scrollController,
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                children: [
                  _sectionHeader('Stage'),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final s in widget.stages)
                        _SelectableChip(
                          label: s.label,
                          color: widget.colorOf(s),
                          selected: _draft.stage == s.code,
                          onTap: () => setState(
                            () => _draft = _draft.copyWith(
                              stage: _draft.stage == s.code ? null : s.code,
                            ),
                          ),
                        ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  _sectionHeader('Assigned to'),
                  if (widget.users.isEmpty)
                    Text(
                      'No users loaded',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    )
                  else
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final u in widget.users)
                          _SelectableChip(
                            label: u.displayName,
                            selected: _draft.assignedToIds.contains(u.id),
                            onTap: () => setState(() {
                              final next = List<String>.from(
                                _draft.assignedToIds,
                              );
                              if (next.contains(u.id)) {
                                next.remove(u.id);
                              } else {
                                next.add(u.id);
                              }
                              _draft = _draft.copyWith(assignedToIds: next);
                            }),
                          ),
                      ],
                    ),
                  const SizedBox(height: 16),
                  if (widget.tags.isNotEmpty) ...[
                    _sectionHeader('Tags'),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final t in widget.tags)
                          _SelectableChip(
                            label: t.name,
                            selected: _draft.tagIds.contains(t.id),
                            onTap: () => setState(() {
                              final next = List<String>.from(_draft.tagIds);
                              if (next.contains(t.id)) {
                                next.remove(t.id);
                              } else {
                                next.add(t.id);
                              }
                              _draft = _draft.copyWith(tagIds: next);
                            }),
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                  ],
                  _sectionHeader('Created'),
                  _dateRangePicker(
                    fromValue: _draft.createdFrom,
                    toValue: _draft.createdTo,
                    onFromChanged: (d) => setState(
                      () => _draft = _draft.copyWith(createdFrom: d),
                    ),
                    onToChanged: (d) =>
                        setState(() => _draft = _draft.copyWith(createdTo: d)),
                  ),
                  const SizedBox(height: 16),
                  _sectionHeader('Closing'),
                  _dateRangePicker(
                    fromValue: _draft.closingFrom,
                    toValue: _draft.closingTo,
                    onFromChanged: (d) => setState(
                      () => _draft = _draft.copyWith(closingFrom: d),
                    ),
                    onToChanged: (d) =>
                        setState(() => _draft = _draft.copyWith(closingTo: d)),
                  ),
                  const SizedBox(height: 16),
                  _sectionHeader('Amount'),
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _amountMinController,
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'Min',
                            border: OutlineInputBorder(),
                          ),
                          onChanged: (v) {
                            final parsed = double.tryParse(v);
                            _draft = _draft.copyWith(amountMin: parsed);
                          },
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: TextField(
                          controller: _amountMaxController,
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'Max',
                            border: OutlineInputBorder(),
                          ),
                          onChanged: (v) {
                            final parsed = double.tryParse(v);
                            _draft = _draft.copyWith(amountMax: parsed);
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  SwitchListTile.adaptive(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Only stalled deals'),
                    subtitle: Text(
                      'Deals rotting in their stage, past its expected days',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                    value: _draft.rottenOnly,
                    onChanged: (v) =>
                        setState(() => _draft = _draft.copyWith(rottenOnly: v)),
                  ),
                ],
              ),
            ),
            Container(
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: AppColors.border)),
              ),
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
              child: Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Cancel'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton(
                      onPressed: () => Navigator.pop(context, _draft),
                      child: const Text('Apply'),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sectionHeader(String title) {
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 8),
      child: Text(
        title,
        style: AppTypography.overline.copyWith(
          color: AppColors.textSecondary,
          letterSpacing: 1.1,
        ),
      ),
    );
  }

  Widget _dateRangePicker({
    required DateTime? fromValue,
    required DateTime? toValue,
    required ValueChanged<DateTime?> onFromChanged,
    required ValueChanged<DateTime?> onToChanged,
  }) {
    final fmt = DateFormat('MMM d, yyyy');
    Widget chip(String label, DateTime? value, ValueChanged<DateTime?> setter) {
      return InkWell(
        onTap: () async {
          final picked = await showDatePicker(
            context: context,
            initialDate: value ?? DateTime.now(),
            firstDate: DateTime(2000),
            lastDate: DateTime(2100),
          );
          if (picked != null) setter(picked);
        },
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: AppColors.gray50,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: [
              Icon(
                LucideIcons.calendar,
                size: 16,
                color: AppColors.textSecondary,
              ),
              const SizedBox(width: 8),
              Text(
                value == null ? label : fmt.format(value),
                style: AppTypography.body.copyWith(
                  color: value == null
                      ? AppColors.textSecondary
                      : AppColors.textPrimary,
                ),
              ),
              if (value != null) ...[
                const SizedBox(width: 6),
                InkWell(
                  onTap: () => setter(null),
                  child: Icon(
                    LucideIcons.x,
                    size: 14,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ],
          ),
        ),
      );
    }

    return Row(
      children: [
        Expanded(child: chip('From', fromValue, onFromChanged)),
        const SizedBox(width: 8),
        Expanded(child: chip('To', toValue, onToChanged)),
      ],
    );
  }
}

class _SelectableChip extends StatelessWidget {
  final String label;
  final Color? color;
  final bool selected;
  final VoidCallback onTap;

  const _SelectableChip({
    required this.label,
    this.color,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppColors.primary50 : AppColors.gray50,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: selected ? AppColors.primary500 : AppColors.border,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (color != null) ...[
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 6),
            ],
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: selected ? AppColors.primary600 : AppColors.textPrimary,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
