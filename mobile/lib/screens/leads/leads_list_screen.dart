import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/models.dart';
import '../../providers/csv_import_provider.dart';
import '../../providers/leads_provider.dart';
import '../../providers/profile_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/cards/lead_card.dart';
import '../../widgets/common/common.dart';
import '../../widgets/common/export_csv_button.dart';
import '../../widgets/forms/csv_import_sheet.dart';

/// Leads list screen: searchable, filterable, paginated against the server.
class LeadsListScreen extends ConsumerStatefulWidget {
  const LeadsListScreen({super.key, this.initialView});

  /// One of `AppRoutes.viewFollowUps` / `AppRoutes.viewHot`, when the user
  /// arrived by tapping the matching dashboard badge. Anything else, including
  /// null, opens the list unfiltered.
  final String? initialView;

  @override
  ConsumerState<LeadsListScreen> createState() => _LeadsListScreenState();
}

class _LeadsListScreenState extends ConsumerState<LeadsListScreen> {
  final TextEditingController _searchController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  LeadFilters _filters = const LeadFilters();
  Timer? _searchDebounce;
  // The dashboard badge this list was opened from, while its filter is still
  // the one in force. Cleared the moment the user changes anything.
  String? _activeView;

  static const _searchDebounceMs = 350;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _applyInitialView();
  }

  /// Open already narrowed to what a dashboard badge counted.
  ///
  /// The notifier outlives this screen, so its filters have to be pushed at it
  /// rather than passed in; the post-frame hop is because the first build is
  /// still running when initState does. The definitions live on `LeadFilters`
  /// so they can be tested against the server's, which is the only thing that
  /// keeps the badge and the list showing the same number.
  void _applyInitialView() {
    final LeadFilters? initial = switch (widget.initialView) {
      AppRoutes.viewFollowUps => LeadFilters.followUpsOn(DateTime.now()),
      AppRoutes.viewHot => LeadFilters.hot(),
      _ => null,
    };
    if (initial == null) return;
    _filters = initial;
    _activeView = widget.initialView;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(leadsProvider.notifier).setFilters(initial);
    });
  }

  void _onScroll() {
    if (_scrollController.position.pixels >=
        _scrollController.position.maxScrollExtent - 200) {
      ref.read(leadsProvider.notifier).loadMore();
    }
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _applyFilters(LeadFilters next) {
    setState(() {
      _filters = next;
      _activeView = null;
    });
    ref.read(leadsProvider.notifier).setFilters(next);
  }

  void _onSearchChanged(String value) {
    setState(() {}); // refresh clear-button visibility
    _searchDebounce?.cancel();
    _searchDebounce = Timer(
      const Duration(milliseconds: _searchDebounceMs),
      () => _applyFilters(_filters.withSearch(value.trim())),
    );
  }

  void _clearAll() {
    _searchController.clear();
    _applyFilters(const LeadFilters());
  }

  @override
  Widget build(BuildContext context) {
    final leadsAsync = ref.watch(leadsProvider);
    // Eagerly subscribe so the profile is fetched before the user opens the
    // Owner filter sheet. Otherwise the "My leads" option wouldn't render
    // until the profile call completes.
    ref.watch(profileProvider);
    final data = leadsAsync.value;
    final leads = data?.leads ?? const <Lead>[];

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Leads'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          // The pipeline board groups these same leads by stage, so it is a
          // place to go, like the tasks board, rather than a list mode.
          IconButton(
            tooltip: 'Pipeline board',
            icon: const Icon(LucideIcons.squareKanban),
            onPressed: () => context.push(AppRoutes.leadBoard),
          ),
          // Offered to every member, as on the web; the import views answer
          // 403 for anyone without admin or sales access.
          IconButton(
            tooltip: 'Import from CSV',
            icon: const Icon(LucideIcons.upload),
            onPressed: () => showCsvImportSheet(
              context,
              CsvImportTarget.leads,
              onImported: () => ref.read(leadsProvider.notifier).refresh(),
            ),
          ),
          ExportCsvButton(
            endpoint: ApiConfig.leadsExport,
            filePrefix: 'leads',
            query: () async => ref.read(leadsProvider.notifier).filterQuery,
          ),
          IconButton(
            icon: const Icon(LucideIcons.plus),
            onPressed: () => context.push(AppRoutes.leadCreate),
          ),
        ],
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          _buildFilterBar(),
          _buildResultsCount(leadsAsync, leads.length, data?.totalCount ?? 0),
          Expanded(child: _buildLeadsList(leadsAsync, leads)),
        ],
      ),
    );
  }

  Widget _buildLeadsList(AsyncValue<LeadsListData> async, List<Lead> leads) {
    final data = async.value;

    if (async.isLoading && (data == null || data.leads.isEmpty)) {
      return const Center(child: CircularProgressIndicator());
    }

    if (async.hasError && (data == null || data.leads.isEmpty)) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 48, color: Colors.grey[400]),
            const SizedBox(height: 16),
            Text(
              'Failed to load leads',
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
              onPressed: () => ref.read(leadsProvider.notifier).refresh(),
              child: const Text('Retry'),
            ),
          ],
        ),
      );
    }

    if (leads.isEmpty) return _buildEmptyState();

    final hasMore = data?.hasMore ?? false;

    return RefreshIndicator(
      onRefresh: () => ref.read(leadsProvider.notifier).refresh(),
      child: ListView.builder(
        controller: _scrollController,
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 80),
        itemCount: leads.length + (hasMore ? 1 : 0),
        itemBuilder: (context, index) {
          if (index == leads.length) {
            return const Padding(
              padding: EdgeInsets.all(12),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final lead = leads[index];
          return LeadCard(
            lead: lead,
            onTap: () => context.push('/leads/${lead.id}'),
            onTagTap: (tagId, tagLabel) =>
                _applyFilters(_filters.withTag(id: tagId, label: tagLabel)),
          );
        },
      ),
    );
  }

  Widget _buildSearchBar() {
    final hasQuery = _searchController.text.isNotEmpty;
    return Container(
      color: AppColors.surfaceDim,
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        style: AppTypography.body,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: 'Search name, company, or email',
          hintStyle: AppTypography.body.copyWith(color: AppColors.textTertiary),
          prefixIcon: Icon(
            LucideIcons.search,
            color: AppColors.textTertiary,
            size: 18,
          ),
          suffixIcon: hasQuery
              ? IconButton(
                  icon: Icon(
                    LucideIcons.x,
                    color: AppColors.textTertiary,
                    size: 16,
                  ),
                  onPressed: () {
                    _searchController.clear();
                    _searchDebounce?.cancel();
                    _applyFilters(_filters.withSearch(null));
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
    return Container(
      color: AppColors.surface,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
        child: Row(
          children: [
            if (_activeView != null) ...[
              _FilterChip(
                label: _activeView == AppRoutes.viewHot
                    ? 'Hot leads'
                    : 'Follow-ups today',
                isActive: true,
                icon: _activeView == AppRoutes.viewHot
                    ? LucideIcons.flame
                    : LucideIcons.calendarClock,
                onTap: _clearAll,
              ),
              const SizedBox(width: 6),
            ],
            _FilterChip(
              label: _filters.assignedToLabel ?? 'Owner',
              isActive: _filters.assignedToId != null,
              icon: LucideIcons.user,
              onTap: _showAssigneeFilter,
            ),
            const SizedBox(width: 6),
            _FilterChip(
              // With more than one status selected there is no single label to
              // show, and printing one of them would name a filter narrower
              // than the list actually is.
              label: switch (_filters.statuses.length) {
                0 => 'Status',
                1 => _filters.statuses.first.displayName,
                final n => '$n statuses',
              },
              isActive: _filters.statuses.isNotEmpty,
              onTap: _showStatusFilter,
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: _filters.rating?.displayName ?? 'Rating',
              isActive: _filters.rating != null,
              icon: _filters.rating == LeadRating.hot
                  ? LucideIcons.flame
                  : null,
              onTap: _showRatingFilter,
            ),
            const SizedBox(width: 6),
            _FilterChip(
              label: _filters.source?.displayName ?? 'Source',
              isActive: _filters.source != null,
              onTap: _showSourceFilter,
            ),
            if (_filters.tagId != null) ...[
              const SizedBox(width: 6),
              _FilterChip(
                label: 'Tag: ${_filters.tagLabel ?? ''}',
                isActive: true,
                icon: LucideIcons.tag,
                onClear: () => _applyFilters(_filters.cleared(tag: true)),
                onTap: () => _applyFilters(_filters.cleared(tag: true)),
              ),
            ],
            if (_filters.isActive) ...[
              const SizedBox(width: 6),
              GestureDetector(
                onTap: _clearAll,
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

  Widget _buildResultsCount(
    AsyncValue<LeadsListData> async,
    int loadedCount,
    int totalCount,
  ) {
    // Until the first page lands there is no count to state. Printing
    // "0 leads" over a spinner reads as an empty org, which is what made a
    // failed load take a minute to tell apart from an empty list.
    if (async.value == null) {
      return _countStrip(async.hasError ? '' : 'Loading');
    }
    final isFiltered = _filters.isActive;
    final label = isFiltered
        ? '$loadedCount of $totalCount lead${totalCount == 1 ? '' : 's'} (filtered)'
        : '$totalCount lead${totalCount == 1 ? '' : 's'}';
    return _countStrip(label);
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
    final isFiltered = _filters.isActive;
    return EmptyState(
      icon: isFiltered ? LucideIcons.search : LucideIcons.users,
      title: isFiltered ? 'No leads match' : 'No leads yet',
      description: isFiltered
          ? 'Try adjusting your search or filters'
          : 'Start by adding your first lead',
      actionLabel: isFiltered ? 'Clear filters' : 'Add Lead',
      onAction: isFiltered
          ? _clearAll
          : () => context.push(AppRoutes.leadCreate),
    );
  }

  // ─── Filter sheets ────────────────────────────────────────────────────────

  void _showAssigneeFilter() {
    final profileId = ref.read(profileProvider).value?.id;
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _FilterBottomSheet(
        title: 'Filter by Owner',
        options: [
          _FilterOption(
            label: 'Anyone',
            isSelected: _filters.assignedToId == null,
            onTap: () {
              _applyFilters(_filters.cleared(assignedTo: true));
              Navigator.pop(context);
            },
          ),
          if (profileId != null && profileId.isNotEmpty)
            _FilterOption(
              label: 'My leads',
              icon: LucideIcons.user,
              iconColor: AppColors.primary600,
              isSelected: _filters.assignedToId == profileId,
              onTap: () {
                _applyFilters(
                  _filters.withAssignee(id: profileId, label: 'Me'),
                );
                Navigator.pop(context);
              },
            ),
        ],
      ),
    );
  }

  void _showStatusFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _FilterBottomSheet(
        title: 'Filter by Status',
        options: [
          _FilterOption(
            label: 'All statuses',
            isSelected: _filters.status == null,
            onTap: () {
              _applyFilters(_filters.cleared(status: true));
              Navigator.pop(context);
            },
          ),
          ...LeadStatus.values.map(
            (status) => _FilterOption(
              label: status.displayName,
              isSelected: _filters.status == status,
              color: status.color,
              onTap: () {
                _applyFilters(_filters.withStatus(status));
                Navigator.pop(context);
              },
            ),
          ),
        ],
      ),
    );
  }

  void _showRatingFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _FilterBottomSheet(
        title: 'Filter by Rating',
        options: [
          _FilterOption(
            label: 'Any rating',
            isSelected: _filters.rating == null,
            onTap: () {
              _applyFilters(_filters.cleared(rating: true));
              Navigator.pop(context);
            },
          ),
          ...LeadRating.values.map(
            (rating) => _FilterOption(
              label: rating.displayName,
              icon: rating == LeadRating.hot ? LucideIcons.flame : null,
              iconColor: rating.color,
              color: rating == LeadRating.hot ? null : rating.color,
              isSelected: _filters.rating == rating,
              onTap: () {
                _applyFilters(_filters.withRating(rating));
                Navigator.pop(context);
              },
            ),
          ),
        ],
      ),
    );
  }

  void _showSourceFilter() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => _FilterBottomSheet(
        title: 'Filter by Source',
        options: [
          _FilterOption(
            label: 'All sources',
            isSelected: _filters.source == null,
            onTap: () {
              _applyFilters(_filters.cleared(source: true));
              Navigator.pop(context);
            },
          ),
          ...LeadSource.values
              .where((s) => s != LeadSource.none)
              .map(
                (source) => _FilterOption(
                  label: source.displayName,
                  isSelected: _filters.source == source,
                  onTap: () {
                    _applyFilters(_filters.withSource(source));
                    Navigator.pop(context);
                  },
                ),
              ),
        ],
      ),
    );
  }
}

/// Filter chip widget. If [onClear] is provided, the chevron is replaced with
/// an X and tapping clears the filter; otherwise tapping opens the picker.
class _FilterChip extends StatelessWidget {
  final String label;
  final bool isActive;
  final IconData? icon;
  final VoidCallback onTap;
  final VoidCallback? onClear;

  const _FilterChip({
    required this.label,
    required this.isActive,
    this.icon,
    required this.onTap,
    this.onClear,
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
            if (icon != null) ...[
              Icon(
                icon,
                size: 12,
                color: isActive ? AppColors.primary700 : AppColors.gray600,
              ),
              const SizedBox(width: 3),
            ],
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: isActive ? AppColors.primary700 : AppColors.gray700,
                fontWeight: isActive ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
            const SizedBox(width: 3),
            Icon(
              onClear != null ? LucideIcons.x : LucideIcons.chevronDown,
              size: 12,
              color: isActive ? AppColors.primary700 : AppColors.gray600,
            ),
          ],
        ),
      ),
    );
  }
}

class _FilterBottomSheet extends StatelessWidget {
  final String title;
  final List<_FilterOption> options;

  const _FilterBottomSheet({required this.title, required this.options});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
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
          ...options,
          const SizedBox(height: 12),
        ],
      ),
    );
  }
}

class _FilterOption extends StatelessWidget {
  final String label;
  final bool isSelected;
  final Color? color;
  final IconData? icon;
  final Color? iconColor;
  final VoidCallback onTap;

  const _FilterOption({
    required this.label,
    required this.isSelected,
    this.color,
    this.icon,
    this.iconColor,
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
            if (icon != null) ...[
              Icon(icon, size: 16, color: iconColor ?? AppColors.textSecondary),
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
