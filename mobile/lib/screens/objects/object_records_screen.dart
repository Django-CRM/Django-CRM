import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/custom_object.dart';
import '../../data/models/object_record_filters.dart';
import '../../providers/auth_provider.dart';
import '../../providers/csv_import_provider.dart';
import '../../providers/custom_objects_provider.dart';
import '../../providers/profile_provider.dart';
import '../../providers/saved_views_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/common.dart';
import '../../widgets/common/export_csv_button.dart';
import '../../widgets/common/saved_views_button.dart';
import '../../widgets/forms/csv_import_sheet.dart';
import '../../widgets/objects/object_board_view.dart';
import '../../widgets/objects/object_filter_sheet.dart';
import '../../widgets/objects/object_record_card.dart';

const _missingObject =
    'This object does not exist, or you do not have access to it.';

/// The records of one custom object, searchable and paged by the server.
class ObjectRecordsScreen extends ConsumerStatefulWidget {
  const ObjectRecordsScreen({super.key, required this.objectKey});

  final String objectKey;

  @override
  ConsumerState<ObjectRecordsScreen> createState() =>
      _ObjectRecordsScreenState();
}

class _ObjectRecordsScreenState extends ConsumerState<ObjectRecordsScreen> {
  final _search = TextEditingController();
  final _scroll = ScrollController();
  Timer? _debounce;
  bool _board = false;

  static const _debounceMs = 300;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_scroll.position.pixels >= _scroll.position.maxScrollExtent - 200) {
      ref.read(objectRecordsProvider(widget.objectKey).notifier).loadMore();
    }
  }

  void _onSearchChanged(String value) {
    setState(() {});
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: _debounceMs), () async {
      await ref
          .read(objectRecordsProvider(widget.objectKey).notifier)
          .setQuery(value);
      _syncBoard();
    });
  }

  void _clearSearch() {
    _search.clear();
    _onSearchChanged('');
  }

  ObjectRecordsNotifier get _records =>
      ref.read(objectRecordsProvider(widget.objectKey).notifier);

  /// The board reads the list's filters, so it is told whenever they move.
  void _syncBoard() {
    if (!_board) return;
    ref
        .read(objectBoardProvider(widget.objectKey).notifier)
        .setFilters(_records.filters);
  }

  void _showBoard(bool on) {
    setState(() => _board = on);
    if (on) _syncBoard();
  }

  Future<void> _openFilters(ObjectDefinition def) async {
    final next = await showObjectFilterSheet(
      context,
      definition: def,
      current: _records.filters,
    );
    if (next == null || !mounted) return;
    await _records.setFilters(next.copyWith(query: _records.filters.query));
    _syncBoard();
  }

  void _applySavedView(
    ObjectDefinition def,
    Map<String, List<String>> filters,
  ) {
    final next = ObjectRecordFilters.fromSavedView(filters, {
      for (final f in filterableFields(def)) f.key,
    });
    _debounce?.cancel();
    _search.text = next.query;
    setState(() {});
    _records.setFilters(next).then((_) => _syncBoard());
  }

  Future<void> _import(ObjectDefinition def) async {
    await showCsvImportSheet(
      context,
      CsvImportTarget.forObject(
        key: widget.objectKey,
        label: def.summary.label,
        labelPlural: def.summary.labelPlural,
        nameLabel: def.summary.nameLabel,
        fields: def.fields,
      ),
      onImported: () async {
        await _records.refresh();
        _syncBoard();
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final definition = ref.watch(objectDefinitionProvider(widget.objectKey));

    return definition.when(
      loading: () => _shell(
        title: 'Records',
        body: const Center(child: CircularProgressIndicator()),
      ),
      error: (e, _) => _shell(
        title: 'Records',
        body: _ErrorView(
          message: 'Failed to load this object',
          onRetry: () =>
              ref.invalidate(objectDefinitionProvider(widget.objectKey)),
        ),
      ),
      data: (def) {
        if (def == null) {
          return _shell(
            title: 'Records',
            body: const EmptyState(
              icon: LucideIcons.boxes,
              title: _missingObject,
            ),
          );
        }
        final canWrite = canWriteObject(
          def.summary,
          shownCustomObjects(ref.watch(customObjectsProvider)),
        );
        // As on the web: an admin, or a member with sales access. The import
        // views refuse anyone else.
        final canImport =
            canWrite &&
            (ref.watch(isOrgAdminProvider) ||
                (ref.watch(profileProvider).value?.hasSalesAccess ?? false));
        final filtered =
            ref.watch(objectRecordsProvider(widget.objectKey)).value?.filters ??
            const ObjectRecordFilters();
        return _shell(
          title: def.summary.labelPlural,
          actions: _actions(def, canImport, filtered.count),
          body: Column(
            children: [
              _searchBar(def.summary),
              if (def.boardField != null) _modeToggle(),
              Expanded(
                child: _board && def.boardField != null
                    ? ObjectBoardView(
                        objectKey: widget.objectKey,
                        definition: def,
                        canMove: canWrite,
                      )
                    : _list(def),
              ),
            ],
          ),
          fab: canWrite
              ? AppFAB(
                  icon: LucideIcons.plus,
                  label: 'New ${def.summary.label}',
                  onPressed: () => context.push(
                    AppRoutes.objectRecordCreate(widget.objectKey),
                  ),
                )
              : null,
        );
      },
    );
  }

  List<Widget> _actions(ObjectDefinition def, bool canImport, int active) => [
    IconButton(
      tooltip: active == 0 ? 'Filters' : 'Filters ($active on)',
      icon: Badge(
        isLabelVisible: active > 0,
        label: Text('$active'),
        child: const Icon(LucideIcons.listFilter),
      ),
      onPressed: () => _openFilters(def),
    ),
    if (canImport)
      IconButton(
        tooltip: 'Import from a spreadsheet',
        icon: const Icon(LucideIcons.upload),
        onPressed: () => _import(def),
      ),
    SavedViewsButton(
      list: SavedViewList.object(widget.objectKey),
      keys: objectSavedViewKeys(def),
      multi: const {'assigned_to', 'tags'},
      currentQuery: () async => _records.filters.toQuery(),
      onApply: (filters) => _applySavedView(def, filters),
    ),
    ExportCsvButton(
      endpoint: ApiConfig.objectRecordsExport(widget.objectKey),
      filePrefix: widget.objectKey,
      query: () async => _records.filters.toQuery(),
    ),
  ];

  Widget _shell({
    required String title,
    required Widget body,
    Widget? fab,
    List<Widget> actions = const [],
  }) {
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        actions: actions,
        title: Text(title),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: body,
      floatingActionButton: fab,
    );
  }

  Widget _modeToggle() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
      child: SegmentedButton<bool>(
        showSelectedIcon: false,
        style: SegmentedButton.styleFrom(minimumSize: const Size(0, 44)),
        segments: const [
          ButtonSegment(value: false, label: Text('List')),
          ButtonSegment(value: true, label: Text('Board')),
        ],
        selected: {_board},
        onSelectionChanged: (v) => _showBoard(v.first),
      ),
    );
  }

  Widget _searchBar(ObjectSummary summary) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
      child: TextField(
        controller: _search,
        onChanged: _onSearchChanged,
        style: AppTypography.body,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: 'Search ${summary.labelPlural.toLowerCase()}',
          hintStyle: AppTypography.body.copyWith(color: AppColors.textTertiary),
          prefixIcon: Icon(
            LucideIcons.search,
            color: AppColors.textTertiary,
            size: 18,
          ),
          suffixIcon: _search.text.isEmpty
              ? null
              : IconButton(
                  tooltip: 'Clear search',
                  icon: Icon(
                    LucideIcons.x,
                    color: AppColors.textTertiary,
                    size: 16,
                  ),
                  onPressed: _clearSearch,
                ),
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

  Widget _list(ObjectDefinition def) {
    final async = ref.watch(objectRecordsProvider(widget.objectKey));
    final notifier = ref.read(objectRecordsProvider(widget.objectKey).notifier);
    final data = async.value;

    if (async.isLoading && (data == null || data.records.isEmpty)) {
      return const Center(child: CircularProgressIndicator());
    }
    if (async.hasError && (data == null || data.records.isEmpty)) {
      return _ErrorView(
        message: 'Failed to load records',
        onRetry: notifier.refresh,
      );
    }
    final records = data?.records ?? const <ObjectRecord>[];
    if (records.isEmpty) {
      final searching = !(data?.filters.isEmpty ?? true);
      return RefreshIndicator(
        onRefresh: notifier.refresh,
        child: LayoutBuilder(
          builder: (context, box) => SingleChildScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            child: SizedBox(
              height: box.maxHeight,
              child: EmptyState(
                icon: LucideIcons.boxes,
                title: searching
                    ? 'No matching ${def.summary.labelPlural.toLowerCase()}'
                    : 'No ${def.summary.labelPlural.toLowerCase()} yet',
              ),
            ),
          ),
        ),
      );
    }

    final hasMore = data?.hasMore ?? false;
    return RefreshIndicator(
      onRefresh: notifier.refresh,
      child: ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 80),
        itemCount: records.length + (hasMore ? 1 : 0),
        itemBuilder: (context, i) {
          if (i == records.length) {
            return const Padding(
              padding: EdgeInsets.all(12),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          return ObjectRecordCard(
            record: records[i],
            fields: def.customFields,
            onTap: () => context.push(
              AppRoutes.objectRecord(widget.objectKey, records[i].id),
            ),
          );
        },
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(LucideIcons.alertCircle, size: 48, color: AppColors.gray400),
          const SizedBox(height: 16),
          Text(message, style: AppTypography.label),
          const SizedBox(height: 8),
          TextButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
