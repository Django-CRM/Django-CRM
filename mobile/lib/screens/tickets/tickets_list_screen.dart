import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/theme/theme.dart';
import '../../data/models/lookup_models.dart';
import '../../data/models/ticket.dart';
import '../../providers/lookup_provider.dart';
import '../../providers/tickets_provider.dart';
import '../../routes/app_router.dart';
import '../../services/api_service.dart';
import '../../widgets/cards/ticket_card.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/multi_select_sheet.dart';

const String _filtersPrefsKey = 'tickets_filters_v1';

/// Tickets List Screen, paginated list with server- and client-side filters.
class TicketsListScreen extends ConsumerStatefulWidget {
  const TicketsListScreen({super.key});

  @override
  ConsumerState<TicketsListScreen> createState() => _TicketsListScreenState();
}

class _TicketsListScreenState extends ConsumerState<TicketsListScreen> {
  final TextEditingController _searchController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  TicketListFilters _filters = const TicketListFilters();
  Timer? _searchDebounce;

  // Selection mode, toggled from the app bar. `_selected` only ever holds ids
  // of rows currently loaded in the list, there is no whole-filter selection.
  bool _selectMode = false;
  final Set<String> _selected = {};

  // The six bulk actions, key first (matches the field name the backend
  // reads, `delete` is handled separately) then the label shown in the sheet.
  // Same keys and order as the web bulk bar's ACTIONS in
  // frontend/src/lib/v2/components/BulkActionBar.svelte, so a screenshot from
  // either client describes the same menu.
  static const List<(String key, String label)> _bulkActionOptions = [
    ('assigned_to', 'Reassign'),
    ('priority', 'Set priority'),
    ('status', 'Set status'),
    ('case_type', 'Set type'),
    ('tags', 'Add tags'),
    ('delete', 'Delete'),
  ];

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    // Restore previously-applied filters in the background. If anything is
    // restored, kick the provider with the saved filters; the default first
    // fetch (no filters) is still in flight at this point, but `refresh`
    // replaces it.
    _restoreFilters();
  }

  Future<void> _restoreFilters() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_filtersPrefsKey);
      if (raw == null || raw.isEmpty) return;
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) return;
      final restored = TicketListFilters.fromJson(decoded);
      if (!mounted) return;
      if (!restored.hasAny) return;
      setState(() {
        _filters = restored;
        _searchController.text = restored.search;
      });
      ref.read(ticketsProvider.notifier).refresh(filters: restored);
    } catch (_) {
      // Persisted shape mismatch from an older version, ignore and use the
      // default empty filter.
    }
  }

  Future<void> _persistFilters(TicketListFilters filters) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!filters.hasAny) {
        await prefs.remove(_filtersPrefsKey);
        return;
      }
      await prefs.setString(_filtersPrefsKey, jsonEncode(filters.toJson()));
    } catch (_) {
      // Persistence is best-effort; failing to save shouldn't block the UI.
    }
  }

  @override
  void dispose() {
    _searchController.dispose();
    _scrollController.dispose();
    _searchDebounce?.cancel();
    super.dispose();
  }

  void _onScroll() {
    if (_scrollController.position.pixels >=
        _scrollController.position.maxScrollExtent - 200) {
      ref.read(ticketsProvider.notifier).loadMore(filters: _filters);
    }
  }

  void _applyFilters(TicketListFilters next) {
    setState(() => _filters = next);
    ref.read(ticketsProvider.notifier).refresh(filters: next);
    _persistFilters(next);
  }

  void _onSearchChanged(String value) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      _applyFilters(_filters.copyWith(search: value.trim()));
    });
  }

  void _clearFilters() {
    _searchController.clear();
    _applyFilters(const TicketListFilters());
  }

  @override
  Widget build(BuildContext context) {
    final ticketsAsync = ref.watch(ticketsProvider);
    final data = ticketsAsync.value;
    final tickets = data?.tickets ?? const <Ticket>[];

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Tickets'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: _selectMode
            ? [
                IconButton(
                  tooltip: 'Cancel selection',
                  icon: const Icon(LucideIcons.x),
                  onPressed: _exitSelectMode,
                ),
              ]
            : [
                IconButton(
                  tooltip: 'Select tickets',
                  icon: const Icon(LucideIcons.checkSquare),
                  onPressed: _enterSelectMode,
                ),
                IconButton(
                  tooltip: 'Analytics',
                  icon: const Icon(LucideIcons.barChart3),
                  onPressed: () => context.push(AppRoutes.ticketAnalytics),
                ),
                IconButton(
                  tooltip: 'Approvals',
                  icon: const Icon(LucideIcons.shieldCheck),
                  onPressed: () => context.push(AppRoutes.approvalsInbox),
                ),
                IconButton(
                  tooltip: 'Knowledge base',
                  icon: const Icon(LucideIcons.bookOpen),
                  onPressed: () => context.push(AppRoutes.solutions),
                ),
                IconButton(
                  tooltip: 'New ticket',
                  icon: const Icon(LucideIcons.plus),
                  onPressed: () => context.push(AppRoutes.ticketCreate),
                ),
              ],
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          _buildQuickStatusChips(),
          _buildFilterBar(),
          _buildResultsCount(
            ticketsAsync,
            tickets.length,
            data?.totalCount ?? 0,
          ),
          Expanded(child: _buildList(ticketsAsync, tickets)),
        ],
      ),
      bottomNavigationBar: _selected.isEmpty ? null : _buildBulkBar(),
    );
  }

  // Quick status chips: All / Open / Closed. Backend supports multi-status,
  // so "Open" expands to a single request with `?status=New&status=Assigned
  // &status=Pending` rather than three round-trips.
  static const List<String> _openStatuses = ['New', 'Assigned', 'Pending'];

  String _quickStatusMode() {
    if (_filters.statusList.isEmpty && _filters.status == null) return 'all';
    if (_filters.statusList.isNotEmpty) {
      final s = _filters.statusList.toSet();
      if (s.length == _openStatuses.length && s.containsAll(_openStatuses)) {
        return 'open';
      }
      if (s.length == 1 && s.first == 'Closed') return 'closed';
    } else if (_filters.status == 'Closed') {
      return 'closed';
    }
    return 'custom';
  }

  Widget _buildQuickStatusChips() {
    final mode = _quickStatusMode();
    return Container(
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
      child: Row(
        children: [
          _quickChip(
            label: 'All',
            isActive: mode == 'all',
            onTap: () => _applyFilters(
              _filters.copyWith(clearStatus: true, clearStatusList: true),
            ),
          ),
          const SizedBox(width: 6),
          _quickChip(
            label: 'Open',
            isActive: mode == 'open',
            onTap: () => _applyFilters(
              _filters.copyWith(clearStatus: true, statusList: _openStatuses),
            ),
          ),
          const SizedBox(width: 6),
          _quickChip(
            label: 'Closed',
            isActive: mode == 'closed',
            onTap: () => _applyFilters(
              _filters.copyWith(
                clearStatus: true,
                statusList: const ['Closed'],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _quickChip({
    required String label,
    required bool isActive,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: isActive ? AppColors.primary500 : AppColors.surface,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: isActive ? AppColors.primary500 : AppColors.border,
          ),
        ),
        child: Text(
          label,
          style: AppTypography.caption.copyWith(
            color: isActive ? Colors.white : AppColors.textPrimary,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }

  Widget _buildList(AsyncValue<TicketsListData> async, List<Ticket> tickets) {
    final data = async.value;

    if (async.isLoading && (data == null || data.tickets.isEmpty)) {
      return const Center(child: CircularProgressIndicator());
    }
    if (async.hasError && (data == null || data.tickets.isEmpty)) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 48, color: Colors.grey[400]),
            const SizedBox(height: 16),
            Text(
              'Failed to load tickets',
              style: AppTypography.label.copyWith(color: Colors.grey[600]),
            ),
            const SizedBox(height: 8),
            Text(
              async.error.toString(),
              style: AppTypography.caption,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            TextButton(
              onPressed: () =>
                  ref.read(ticketsProvider.notifier).refresh(filters: _filters),
              child: const Text('Retry'),
            ),
          ],
        ),
      );
    }
    if (tickets.isEmpty) return _buildEmptyState();

    final hasMore = data?.hasMore ?? false;

    return RefreshIndicator(
      onRefresh: () =>
          ref.read(ticketsProvider.notifier).refresh(filters: _filters),
      child: ListView.builder(
        controller: _scrollController,
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 80),
        itemCount: tickets.length + (hasMore ? 1 : 0),
        itemBuilder: (context, index) {
          if (index == tickets.length) {
            return const Padding(
              padding: EdgeInsets.all(12),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final ticketItem = tickets[index];
          return _buildTicketRow(ticketItem);
        },
      ),
    );
  }

  /// A single row in the list. Gates the card's tap: in selection mode a tap
  /// toggles the row instead of pushing the detail screen, and a checkbox
  /// badge overlays its top-left corner to show the selected state.
  ///
  /// The badge is a `Positioned` overlay in a `Stack`, not a leading column in
  /// a `Row`, on purpose: TicketCard's own footer row (type, priority,
  /// timestamp, assignee) already sits close to its overflow point at a
  /// 390px phone width, and a Row that gives the card less than its full
  /// width pushes that row over. TicketCard is out of scope for this change,
  /// so the fix is to never narrow it: a `Positioned` child does not affect
  /// how much space the `Stack` gives its non-positioned child.
  Widget _buildTicketRow(Ticket ticketItem) {
    final card = TicketCard(
      ticketItem: ticketItem,
      onTap: _selectMode
          ? () => _toggleSelected(ticketItem.id)
          : () => context.push('/tickets/${ticketItem.id}'),
    );
    if (!_selectMode) return card;
    final isSelected = _selected.contains(ticketItem.id);
    return Stack(
      children: [
        card,
        Positioned(
          top: 6,
          left: 6,
          child: Container(
            decoration: BoxDecoration(
              color: AppColors.surface,
              shape: BoxShape.circle,
              border: Border.all(color: AppColors.border),
            ),
            child: Checkbox(
              value: isSelected,
              onChanged: (_) => _toggleSelected(ticketItem.id),
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
              visualDensity: VisualDensity.compact,
            ),
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // Selection mode
  // ---------------------------------------------------------------------

  void _enterSelectMode() {
    setState(() => _selectMode = true);
  }

  void _exitSelectMode() {
    setState(() {
      _selectMode = false;
      _selected.clear();
    });
  }

  void _toggleSelected(String id) {
    setState(() {
      if (_selected.contains(id)) {
        _selected.remove(id);
      } else {
        _selected.add(id);
      }
    });
  }

  /// Flat bar per DESIGN_SYSTEM.md: no elevation, a top border instead of a
  /// shadow. Mirrors the Container-plus-SafeArea shape already used for
  /// `deal_detail_screen.dart`'s sticky bottom bar.
  Widget _buildBulkBar() {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 12, 10),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  '${_selected.length} selected',
                  style: AppTypography.label,
                ),
              ),
              TextButton(
                // Themed buttons carry an infinite minimum width, which a Row
                // does not bound. Without this the row fails to lay out and
                // the bar paints nothing. See AppLayout.buttonMinSizeInRow
                // (already relied on by multi_select_sheet.dart).
                style: TextButton.styleFrom(
                  minimumSize: AppLayout.buttonMinSizeInRow,
                ),
                onPressed: () => setState(_selected.clear),
                child: const Text('Clear'),
              ),
              const SizedBox(width: 4),
              FilledButton(
                style: FilledButton.styleFrom(
                  minimumSize: AppLayout.buttonMinSizeInRow,
                ),
                onPressed: _openActionsSheet,
                child: const Text('Actions'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _openActionsSheet() async {
    final action = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => SafeArea(
        child: SingleChildScrollView(
          // A short viewport (a phone in landscape, or a small split-screen
          // window) can be shorter than six rows plus the handle and title.
          // `showModalBottomSheet` without `isScrollControlled` already caps
          // this sheet at 9/16 of the screen height, so scrolling here is the
          // fallback for whatever doesn't fit inside that cap, not the
          // primary way anyone reads a 6-item menu.
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                margin: const EdgeInsets.only(top: 8),
                width: 32,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.gray300,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(12),
                child: Text('Actions', style: AppTypography.label),
              ),
              for (final option in _bulkActionOptions)
                InkWell(
                  onTap: () => Navigator.pop(context, option.$1),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 12,
                    ),
                    child: Text(
                      option.$2,
                      style: AppTypography.body.copyWith(
                        color: option.$1 == 'delete'
                            ? AppColors.danger600
                            : AppColors.textPrimary,
                        fontWeight: option.$1 == 'delete'
                            ? FontWeight.w600
                            : FontWeight.normal,
                      ),
                    ),
                  ),
                ),
              const SizedBox(height: 12),
            ],
          ),
        ),
      ),
    );
    if (!mounted || action == null) return;
    await _runBulkAction(action);
  }

  Future<void> _runBulkAction(String key) async {
    switch (key) {
      case 'assigned_to':
        await _bulkReassign();
      case 'priority':
        await _bulkPickAndApply<TicketPriority>(
          'Set priority',
          TicketPriority.values,
          (p) => p.value,
          (p) => p.label,
          'priority',
          colorOf: (p) => p.color,
        );
      case 'status':
        await _bulkSetStatus();
      case 'case_type':
        await _bulkPickAndApply<TicketType>(
          'Set type',
          TicketType.values,
          (t) => t.value,
          (t) => t.label,
          'case_type',
        );
      case 'tags':
        await _bulkAddTags();
      case 'delete':
        await _bulkDeleteConfirm();
    }
  }

  /// Single-choice scalar field (priority, case type). Reuses the same
  /// `_SimpleFilterSheet` / `_FilterRow` shape the existing filter pickers
  /// use above: tapping a row applies immediately, there is no separate
  /// Apply button, matching `_pickPriority` and `_pickCaseType`.
  Future<void> _bulkPickAndApply<T>(
    String title,
    List<T> options,
    String Function(T) valueOf,
    String Function(T) labelOf,
    String field, {
    Color? Function(T)? colorOf,
  }) async {
    final picked = await showModalBottomSheet<T>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SimpleFilterSheet(
        title: title,
        rows: [
          for (final option in options)
            _FilterRow(
              label: labelOf(option),
              isSelected: false,
              color: colorOf?.call(option),
              onTap: () => Navigator.pop(context, option),
            ),
        ],
      ),
    );
    if (picked == null) return;
    await _applyBulkUpdate({field: valueOf(picked)});
  }

  /// Status is a scalar too, except Closed also needs `closed_on`. The date
  /// picker defaults to today; the backend requires the key for a close and
  /// gates it on any pre_close approval rule regardless of what is sent here.
  Future<void> _bulkSetStatus() async {
    final picked = await showModalBottomSheet<TicketStatus>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SimpleFilterSheet(
        title: 'Set status',
        rows: [
          for (final status in TicketStatus.values)
            _FilterRow(
              label: status.label,
              isSelected: false,
              onTap: () => Navigator.pop(context, status),
            ),
        ],
      ),
    );
    if (picked == null) return;
    if (picked != TicketStatus.closed) {
      await _applyBulkUpdate({'status': picked.value});
      return;
    }
    if (!mounted) return;
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: DateTime(2000),
      lastDate: now.add(const Duration(days: 1)),
    );
    if (date == null) return;
    await _applyBulkUpdate({'status': 'Closed', 'closed_on': _isoDate(date)});
  }

  String _isoDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

  /// `assigned_to` is many-to-many on the backend (it replaces the set), but
  /// the picker here offers one person at a time, same as the web bulk bar's
  /// single-select Reassign dropdown. The field still carries a list.
  Future<void> _bulkReassign() async {
    final users = ref.read(usersProvider);
    final picked = await showModalBottomSheet<UserLookup>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (ctx, controller) => Column(
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
              padding: const EdgeInsets.all(16),
              child: Text('Reassign to', style: AppTypography.h3),
            ),
            Expanded(
              child: ListView.builder(
                controller: controller,
                itemCount: users.length,
                itemBuilder: (_, i) {
                  final u = users[i];
                  return ListTile(
                    leading: UserAvatar(
                      name: u.displayName,
                      size: AvatarSize.xs,
                    ),
                    title: Text(u.displayName),
                    onTap: () => Navigator.pop(context, u),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
    if (picked == null) return;
    await _applyBulkUpdate({
      'assigned_to': [picked.id],
    });
  }

  /// `tags` is many-to-many and appends server-side, so a multi-select is
  /// correct here (unlike Reassign, which replaces). Reuses the same
  /// MultiSelectSheet the filter bar's tag picker already uses.
  Future<void> _bulkAddTags() async {
    final tags = ref.read(tagsProvider);
    final result = await MultiSelectSheet.show<TagLookup>(
      context: context,
      title: 'Add tags',
      items: tags,
      initialSelection: const [],
      labelOf: (t) => t.name,
      searchText: (t) => t.name,
    );
    if (result == null || result.isEmpty) return;
    await _applyBulkUpdate({'tags': result.map((t) => t.id).toList()});
  }

  Future<void> _bulkDeleteConfirm() async {
    final ids = _selected.toList();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete tickets?'),
        content: Text(
          'Permanently delete ${ids.length} '
          'ticket${ids.length == 1 ? '' : 's'}. This cannot be undone.',
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
    final response = await ref.read(ticketsProvider.notifier).bulkDelete(ids);
    await _finishBulkAction(response, isDelete: true);
  }

  Future<void> _applyBulkUpdate(Map<String, dynamic> fields) async {
    final ids = _selected.toList();
    if (ids.isEmpty) return;
    final response = await ref
        .read(ticketsProvider.notifier)
        .bulkUpdate(ids, fields);
    await _finishBulkAction(response, isDelete: false);
  }

  Future<void> _finishBulkAction(
    ApiResponse<Map<String, dynamic>> response, {
    required bool isDelete,
  }) async {
    if (!mounted) return;
    final text = response.success
        ? _bulkSummary(
            (response.data?['results'] as List<dynamic>?) ?? const [],
            isDelete: isDelete,
          )
        : (response.message ?? 'Bulk ${isDelete ? 'delete' : 'update'} failed');
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    setState(() {
      _selected.clear();
      _selectMode = false;
    });
    ref.read(ticketsProvider.notifier).refresh(filters: _filters);
  }

  /// Tallies `results` rows (`{'id':..., 'status':...}`) into the same
  /// wording the web bulk bar shows. Mirrors `summarizeBulk` and
  /// `summaryText` in frontend/src/lib/server/v2/tickets.js and
  /// frontend/src/routes/(app)/tickets/+page.svelte: one headline count, then
  /// a part per nonzero bucket, joined with " · ".
  String _bulkSummary(List<dynamic> results, {required bool isDelete}) {
    var updated = 0;
    var deleted = 0;
    var noAccess = 0;
    var approvalRequired = 0;
    var closedOnRequired = 0;
    var invalid = 0;
    for (final row in results) {
      if (row is! Map) continue;
      switch (row['status']) {
        case 'updated':
          updated++;
        case 'deleted':
          deleted++;
        case 'no_access':
          noAccess++;
        case 'approval_required':
          approvalRequired++;
        case 'closed_on_required':
          closedOnRequired++;
        case 'invalid':
          invalid++;
      }
    }
    final parts = <String>[isDelete ? '$deleted deleted' : '$updated updated'];
    if (noAccess > 0) parts.add('$noAccess skipped (no access)');
    if (approvalRequired > 0) parts.add('$approvalRequired need approval');
    if (closedOnRequired > 0) parts.add('$closedOnRequired missing close date');
    if (invalid > 0) parts.add('$invalid invalid');
    return parts.join(' · ');
  }

  Widget _buildSearchBar() {
    return Container(
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        style: AppTypography.body,
        decoration: InputDecoration(
          hintText: 'Search tickets...',
          hintStyle: AppTypography.body.copyWith(color: AppColors.textTertiary),
          prefixIcon: Icon(
            LucideIcons.search,
            color: AppColors.textTertiary,
            size: 18,
          ),
          suffixIcon: _filters.search.isNotEmpty
              ? IconButton(
                  icon: Icon(
                    LucideIcons.x,
                    color: AppColors.textTertiary,
                    size: 16,
                  ),
                  onPressed: () {
                    _searchController.clear();
                    _applyFilters(_filters.copyWith(search: ''));
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
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide.none,
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: AppColors.primary500, width: 1),
          ),
        ),
      ),
    );
  }

  Widget _buildFilterBar() {
    final accounts = ref.watch(accountOptionsProvider);
    final users = ref.watch(usersProvider);
    final tags = ref.watch(tagsProvider);

    final selectedAccount = accounts
        .where((a) => a.id == _filters.accountId)
        .firstOrNull;
    final assigneeLabels = users
        .where((u) => _filters.assigneeIds.contains(u.id))
        .map((u) => u.displayName)
        .toList();
    final tagLabels = tags
        .where((t) => _filters.tagIds.contains(t.id))
        .map((t) => t.name)
        .toList();

    return Container(
      color: AppColors.surface,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
        child: Row(
          children: [
            _FilterChip(
              label: _filters.priority != null
                  ? TicketPriority.fromString(_filters.priority).label
                  : 'Priority',
              isActive: _filters.priority != null,
              onTap: _pickPriority,
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: _filters.caseType != null
                  ? TicketType.fromString(_filters.caseType).label
                  : 'Type',
              isActive: _filters.caseType != null,
              onTap: _pickCaseType,
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: selectedAccount?.name ?? 'Account',
              isActive: _filters.accountId != null,
              onTap: () => _pickAccount(accounts),
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: assigneeLabels.isEmpty
                  ? 'Assignees'
                  : 'Assignees · ${assigneeLabels.length}',
              isActive: assigneeLabels.isNotEmpty,
              onTap: () => _pickAssignees(users),
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: tagLabels.isEmpty ? 'Tags' : 'Tags · ${tagLabels.length}',
              isActive: tagLabels.isNotEmpty,
              onTap: () => _pickTags(tags),
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: _formatDateRange(),
              isActive:
                  _filters.createdAfter != null ||
                  _filters.createdBefore != null,
              onTap: _pickDateRange,
            ),
            const SizedBox(width: 6),
            _ToggleChip(
              label: 'Watching',
              icon: LucideIcons.eye,
              isActive: _filters.watchingOnly,
              onTap: () => _applyFilters(
                _filters.copyWith(watchingOnly: !_filters.watchingOnly),
              ),
            ),
            const SizedBox(width: 6),
            _ToggleChip(
              label: 'Breaching SLA',
              icon: LucideIcons.alertTriangle,
              isActive: _filters.slaBreached,
              onTap: () => _applyFilters(
                _filters.copyWith(slaBreached: !_filters.slaBreached),
              ),
            ),
            if (_filters.hasAny) ...[
              const SizedBox(width: 6),
              GestureDetector(
                onTap: _clearFilters,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.danger100,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(LucideIcons.x, size: 12, color: AppColors.danger600),
                      const SizedBox(width: 3),
                      Text(
                        'Clear',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.danger600,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  String _formatDateRange() {
    final after = _filters.createdAfter;
    final before = _filters.createdBefore;
    if (after == null && before == null) return 'Date';
    String fmt(DateTime d) =>
        '${d.month.toString().padLeft(2, '0')}/${d.day.toString().padLeft(2, '0')}';
    if (after != null && before != null)
      return '${fmt(after)} → ${fmt(before)}';
    if (after != null) return 'After ${fmt(after)}';
    return 'Before ${fmt(before!)}';
  }

  Widget _buildResultsCount(
    AsyncValue<TicketsListData> async,
    int loadedCount,
    int totalCount,
  ) {
    // Until the first page lands there is no count to state. Printing
    // "0 tickets" over a spinner reads as an empty org, which is what made a
    // failed load take a minute to tell apart from an empty list.
    if (async.value == null) {
      return _countStrip(async.hasError ? '' : 'Loading');
    }
    final unit = totalCount == 1 ? 'ticket' : 'tickets';
    final text = totalCount > 0 && loadedCount < totalCount
        ? '$loadedCount of $totalCount $unit'
        : '$totalCount $unit';
    return _countStrip(_filters.hasAny ? '$text (filtered)' : text);
  }

  Widget _countStrip(String label) {
    return Container(
      width: double.infinity,
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
      child: Text(
        label,
        style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
      ),
    );
  }

  Widget _buildEmptyState() {
    return EmptyState(
      icon: _filters.hasAny ? LucideIcons.search : LucideIcons.ticket,
      title: _filters.hasAny ? 'No results found' : 'No tickets yet',
      description: _filters.hasAny
          ? 'Try adjusting your filters'
          : 'Customer-reported issues will appear here',
      actionLabel: _filters.hasAny ? 'Clear filters' : 'Create ticket',
      onAction: _filters.hasAny
          ? _clearFilters
          : () => context.push(AppRoutes.ticketCreate),
    );
  }

  void _pickCaseType() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SimpleFilterSheet(
        title: 'Filter by Type',
        rows: [
          _FilterRow(
            label: 'All Types',
            isSelected: _filters.caseType == null,
            onTap: () {
              Navigator.pop(context);
              _applyFilters(_filters.copyWith(clearCaseType: true));
            },
          ),
          ...TicketType.values.map(
            (t) => _FilterRow(
              label: t.label,
              isSelected: _filters.caseType == t.value,
              onTap: () {
                Navigator.pop(context);
                _applyFilters(_filters.copyWith(caseType: t.value));
              },
            ),
          ),
        ],
      ),
    );
  }

  void _pickPriority() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SimpleFilterSheet(
        title: 'Filter by Priority',
        rows: [
          _FilterRow(
            label: 'All Priorities',
            isSelected: _filters.priority == null,
            onTap: () {
              Navigator.pop(context);
              _applyFilters(_filters.copyWith(clearPriority: true));
            },
          ),
          ...TicketPriority.values.map(
            (p) => _FilterRow(
              label: p.label,
              isSelected: _filters.priority == p.value,
              color: p.color,
              onTap: () {
                Navigator.pop(context);
                _applyFilters(_filters.copyWith(priority: p.value));
              },
            ),
          ),
        ],
      ),
    );
  }

  void _pickAccount(List<AccountLookup> accounts) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.3,
        maxChildSize: 0.9,
        expand: false,
        builder: (ctx, controller) => Column(
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
              padding: const EdgeInsets.all(16),
              child: Text('Filter by Account', style: AppTypography.h3),
            ),
            _FilterRow(
              label: 'All Accounts',
              isSelected: _filters.accountId == null,
              onTap: () {
                Navigator.pop(context);
                _applyFilters(_filters.copyWith(clearAccountId: true));
              },
            ),
            Expanded(
              child: ListView.builder(
                controller: controller,
                itemCount: accounts.length,
                itemBuilder: (_, i) {
                  final a = accounts[i];
                  return _FilterRow(
                    label: a.name,
                    isSelected: _filters.accountId == a.id,
                    onTap: () {
                      Navigator.pop(context);
                      _applyFilters(_filters.copyWith(accountId: a.id));
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _pickAssignees(List<UserLookup> users) async {
    final initial = users
        .where((u) => _filters.assigneeIds.contains(u.id))
        .toList();
    final result = await MultiSelectSheet.show<UserLookup>(
      context: context,
      title: 'Filter by Assignee',
      items: users,
      initialSelection: initial,
      labelOf: (u) => u.displayName,
      searchText: (u) => '${u.email} ${u.displayName}',
      leadingOf: (u) => UserAvatar(name: u.displayName, size: AvatarSize.xs),
    );
    if (result != null) {
      _applyFilters(
        _filters.copyWith(assigneeIds: result.map((u) => u.id).toList()),
      );
    }
  }

  Future<void> _pickTags(List<TagLookup> tags) async {
    final initial = tags.where((t) => _filters.tagIds.contains(t.id)).toList();
    final result = await MultiSelectSheet.show<TagLookup>(
      context: context,
      title: 'Filter by Tag',
      items: tags,
      initialSelection: initial,
      labelOf: (t) => t.name,
      searchText: (t) => t.name,
    );
    if (result != null) {
      _applyFilters(
        _filters.copyWith(tagIds: result.map((t) => t.id).toList()),
      );
    }
  }

  Future<void> _pickDateRange() async {
    final now = DateTime.now();
    final initial =
        (_filters.createdAfter != null || _filters.createdBefore != null)
        ? DateTimeRange(
            start:
                _filters.createdAfter ?? now.subtract(const Duration(days: 30)),
            end: _filters.createdBefore ?? now,
          )
        : null;
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2000),
      lastDate: now.add(const Duration(days: 1)),
      initialDateRange: initial,
    );
    if (picked != null) {
      _applyFilters(
        _filters.copyWith(
          createdAfter: picked.start,
          createdBefore: picked.end,
        ),
      );
    }
  }
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool isActive;
  final VoidCallback onTap;

  const _FilterChip({
    required this.label,
    required this.isActive,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: AppDurations.fast,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(
          color: isActive ? AppColors.primary100 : AppColors.gray100,
          borderRadius: BorderRadius.circular(4),
          border: isActive
              ? Border.all(color: AppColors.primary300, width: 1)
              : null,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: isActive ? AppColors.primary700 : AppColors.gray700,
                fontWeight: isActive ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
            const SizedBox(width: 3),
            Icon(
              LucideIcons.chevronDown,
              size: 12,
              color: isActive ? AppColors.primary700 : AppColors.gray600,
            ),
          ],
        ),
      ),
    );
  }
}

class _ToggleChip extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool isActive;
  final VoidCallback onTap;

  const _ToggleChip({
    required this.label,
    required this.icon,
    required this.isActive,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: AppDurations.fast,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(
          color: isActive ? AppColors.primary100 : AppColors.gray100,
          borderRadius: BorderRadius.circular(4),
          border: isActive
              ? Border.all(color: AppColors.primary300, width: 1)
              : null,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon,
              size: 12,
              color: isActive ? AppColors.primary700 : AppColors.gray600,
            ),
            const SizedBox(width: 4),
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: isActive ? AppColors.primary700 : AppColors.gray700,
                fontWeight: isActive ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SimpleFilterSheet extends StatelessWidget {
  final String title;
  final List<_FilterRow> rows;

  const _SimpleFilterSheet({required this.title, required this.rows});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      // Scrolls only if a short viewport (or the bulk-action lists this sheet
      // now also serves, e.g. all six TicketStatus rows) doesn't fit under
      // the 9/16-screen cap `showModalBottomSheet` applies by default.
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 8),
              width: 32,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.gray300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(title, style: AppTypography.label),
            ),
            ...rows,
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
  }
}

class _FilterRow extends StatelessWidget {
  final String label;
  final bool isSelected;
  final Color? color;
  final VoidCallback onTap;

  const _FilterRow({
    required this.label,
    required this.isSelected,
    this.color,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Row(
          children: [
            if (color != null) ...[
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 10),
            ],
            Expanded(
              child: Text(
                label,
                style: AppTypography.body.copyWith(
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                  color: isSelected
                      ? AppColors.primary600
                      : AppColors.textPrimary,
                ),
              ),
            ),
            if (isSelected)
              Icon(LucideIcons.check, size: 18, color: AppColors.primary600),
          ],
        ),
      ),
    );
  }
}
