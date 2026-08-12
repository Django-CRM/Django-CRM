import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../config/api_config.dart';
import '../data/models/attachment.dart';
import '../data/models/comment.dart';
import '../data/models/custom_field_definition.dart';
import '../data/models/deal.dart';
import '../services/api_service.dart';
import 'leads_provider.dart' show AssignableUser;

/// How the list view sorts deals within a stage. Backend doesn't expose a
/// sort param so we apply this client-side over the loaded page.
enum DealSort { closeDateAsc, valueDesc, stageAgeDesc, updatedDesc }

extension DealSortX on DealSort {
  String get label {
    switch (this) {
      case DealSort.closeDateAsc:
        return 'Closest close date';
      case DealSort.valueDesc:
        return 'Largest value';
      case DealSort.stageAgeDesc:
        return 'Stalest first';
      case DealSort.updatedDesc:
        return 'Recently updated';
    }
  }
}

/// Filter state for the deals list. Each field maps to a backend query param.
class DealFilters {
  final String? search;
  final DealStage? stage;
  final List<String> assignedToIds;
  final List<String> tagIds;
  final DateTime? createdFrom;
  final DateTime? createdTo;
  final DateTime? closingFrom;
  final DateTime? closingTo;
  final double? amountMin;
  final double? amountMax;
  final bool rottenOnly;

  const DealFilters({
    this.search,
    this.stage,
    this.assignedToIds = const [],
    this.tagIds = const [],
    this.createdFrom,
    this.createdTo,
    this.closingFrom,
    this.closingTo,
    this.amountMin,
    this.amountMax,
    this.rottenOnly = false,
  });

  bool get isEmpty =>
      (search == null || search!.isEmpty) &&
      stage == null &&
      assignedToIds.isEmpty &&
      tagIds.isEmpty &&
      createdFrom == null &&
      createdTo == null &&
      closingFrom == null &&
      closingTo == null &&
      amountMin == null &&
      amountMax == null &&
      !rottenOnly;

  /// How many "active" filter facets to show on the filter button badge.
  /// Search is excluded (it has its own UI affordance).
  int get badgeCount {
    var n = 0;
    if (stage != null) n++;
    if (assignedToIds.isNotEmpty) n++;
    if (tagIds.isNotEmpty) n++;
    if (createdFrom != null || createdTo != null) n++;
    if (closingFrom != null || closingTo != null) n++;
    if (amountMin != null || amountMax != null) n++;
    if (rottenOnly) n++;
    return n;
  }

  DealFilters copyWith({
    Object? search = _sentinel,
    Object? stage = _sentinel,
    List<String>? assignedToIds,
    List<String>? tagIds,
    Object? createdFrom = _sentinel,
    Object? createdTo = _sentinel,
    Object? closingFrom = _sentinel,
    Object? closingTo = _sentinel,
    Object? amountMin = _sentinel,
    Object? amountMax = _sentinel,
    bool? rottenOnly,
  }) {
    return DealFilters(
      search: identical(search, _sentinel) ? this.search : search as String?,
      stage: identical(stage, _sentinel) ? this.stage : stage as DealStage?,
      assignedToIds: assignedToIds ?? this.assignedToIds,
      tagIds: tagIds ?? this.tagIds,
      createdFrom: identical(createdFrom, _sentinel)
          ? this.createdFrom
          : createdFrom as DateTime?,
      createdTo: identical(createdTo, _sentinel)
          ? this.createdTo
          : createdTo as DateTime?,
      closingFrom: identical(closingFrom, _sentinel)
          ? this.closingFrom
          : closingFrom as DateTime?,
      closingTo: identical(closingTo, _sentinel)
          ? this.closingTo
          : closingTo as DateTime?,
      amountMin: identical(amountMin, _sentinel)
          ? this.amountMin
          : amountMin as double?,
      amountMax: identical(amountMax, _sentinel)
          ? this.amountMax
          : amountMax as double?,
      rottenOnly: rottenOnly ?? this.rottenOnly,
    );
  }

  static const _sentinel = Object();
}

/// Bundle returned by [DealsNotifier.getDealDetail]. The detail endpoint
/// returns the opportunity alongside lookups (custom-field schema,
/// assignable users) that the detail screen needs to render and act on.
class DealDetail {
  final Deal deal;
  final List<CustomFieldDefinition> customFieldDefinitions;
  final List<AssignableUser> assignableUsers;
  final bool commentPermission;

  const DealDetail({
    required this.deal,
    this.customFieldDefinitions = const [],
    this.assignableUsers = const [],
    this.commentPermission = false,
  });
}

/// Paginated deals snapshot, wrapped by AsyncValue for loading/error.
class DealsListData {
  final List<Deal> deals;
  final int totalCount;
  final bool hasMore;
  final int currentOffset;

  const DealsListData({
    this.deals = const [],
    this.totalCount = 0,
    this.hasMore = true,
    this.currentOffset = 0,
  });

  DealsListData copyWith({
    List<Deal>? deals,
    int? totalCount,
    bool? hasMore,
    int? currentOffset,
  }) {
    return DealsListData(
      deals: deals ?? this.deals,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      currentOffset: currentOffset ?? this.currentOffset,
    );
  }
}

/// AsyncNotifier driving the deals list + CRUD. The list page is the only
/// place that watches this; detail/form screens use the notifier directly
/// for one-shot fetches and mutations.
class DealsNotifier extends AsyncNotifier<DealsListData> {
  final ApiService _apiService = ApiService();
  static const int _pageSize = 20;

  DealFilters _filters = const DealFilters();
  DealFilters get filters => _filters;

  @override
  Future<DealsListData> build() => _fetchPage(offset: 0);

  /// Replace filters and refetch from offset 0. Used by the search box, the
  /// filter sheet, and the "Assigned to me" quick chip.
  Future<void> setFilters(DealFilters filters) async {
    _filters = filters;
    await refresh();
  }

  /// Reload the first page (pull-to-refresh / after CRUD).
  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetchPage(offset: 0));
  }

  /// Append the next page if there's more and we're not already loading.
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore) return;
    if (state.isLoading) return;

    state = await AsyncValue.guard(() async {
      final next = await _fetchPage(offset: current.currentOffset);
      return current.copyWith(
        deals: [...current.deals, ...next.deals],
        totalCount: next.totalCount,
        hasMore: next.hasMore,
        currentOffset: next.currentOffset,
      );
    });
  }

  /// Fetch every remaining page sequentially. Used by Kanban view, which
  /// can't paginate naturally (horizontal scroll is already used for stage
  /// columns), so we need the whole set in-memory to populate every column.
  Future<void> loadAll() async {
    while (true) {
      final current = state.value;
      if (current == null || !current.hasMore) return;
      if (state.isLoading) return;
      await loadMore();
      final after = state.value;
      if (after == null || after.currentOffset == current.currentOffset) {
        return; // safety. Avoid infinite loop on a backend that misreports.
      }
    }
  }

  Future<DealsListData> _fetchPage({required int offset}) async {
    final queryParams = <String, dynamic>{
      'limit': _pageSize.toString(),
      'offset': offset.toString(),
    };
    final f = _filters;
    if (f.search != null && f.search!.isNotEmpty) {
      queryParams['name'] = f.search!;
    }
    if (f.stage != null) {
      queryParams['stage'] = f.stage!.value;
    }
    if (f.assignedToIds.isNotEmpty) {
      queryParams['assigned_to'] = f.assignedToIds;
    }
    if (f.tagIds.isNotEmpty) {
      queryParams['tags'] = f.tagIds;
    }
    if (f.createdFrom != null) {
      queryParams['created_at__gte'] = _formatDate(f.createdFrom!);
    }
    if (f.createdTo != null) {
      queryParams['created_at__lte'] = _formatDate(f.createdTo!);
    }
    if (f.closingFrom != null) {
      queryParams['closed_on__gte'] = _formatDate(f.closingFrom!);
    }
    if (f.closingTo != null) {
      queryParams['closed_on__lte'] = _formatDate(f.closingTo!);
    }
    if (f.amountMin != null) {
      queryParams['amount__gte'] = f.amountMin!.toString();
    }
    if (f.amountMax != null) {
      queryParams['amount__lte'] = f.amountMax!.toString();
    }
    if (f.rottenOnly) {
      queryParams['rotten'] = 'true';
    }

    final url = Uri.parse(ApiConfig.opportunities)
        .replace(
          queryParameters: queryParams.map(
            (k, v) => MapEntry(k, v is List ? v : v.toString()),
          ),
        )
        .toString();
    final response = await _apiService.get(url);

    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load deals');
    }

    final data = response.data!;
    List<dynamic> dealsList = [];
    int dealsCount = 0;

    if (data['opportunities'] != null) {
      dealsList = data['opportunities'] as List<dynamic>? ?? [];
      dealsCount = data['opportunities_count'] as int? ?? dealsList.length;
    } else if (data['results'] != null) {
      dealsList = data['results'] as List<dynamic>? ?? [];
      dealsCount = data['count'] as int? ?? dealsList.length;
    }

    final newDeals = <Deal>[];
    for (final item in dealsList) {
      try {
        if (item is Map<String, dynamic>) {
          newDeals.add(Deal.fromJson(item));
        }
      } catch (_) {
        // Skip invalid deals
      }
    }

    return DealsListData(
      deals: newDeals,
      totalCount: dealsCount,
      hasMore: newDeals.length >= _pageSize,
      currentOffset: offset + newDeals.length,
    );
  }

  String _formatDate(DateTime d) {
    return '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  }

  /// Fetch a single deal from the API. Returns just the [Deal]; screens
  /// that need the surrounding lookups (custom-field schema, assignable
  /// users, comments, attachments, contacts) should call [getDealDetail].
  Future<Deal?> getDeal(String id) async {
    final detail = await getDealDetail(id);
    return detail?.deal;
  }

  /// Fetch a deal together with everything the detail screen needs to
  /// render and act on it: comments, attachments, custom-field
  /// definitions, contacts and the assignable user list. Keeps the lookups
  /// in one place rather than scattering parallel calls across the screen.
  Future<DealDetail?> getDealDetail(String id) async {
    try {
      final url = '${ApiConfig.opportunities}$id/';
      final response = await _apiService.get(url);

      if (!response.success || response.data == null) {
        // ignore: avoid_print
        print(
          '[deals_provider] getDealDetail($id) HTTP failed: '
          'status=${response.statusCode} message=${response.message}',
        );
        return null;
      }

      final data = response.data!;
      final dealData = data['opportunity_obj'] as Map<String, dynamic>?;
      if (dealData == null) {
        // ignore: avoid_print
        print('[deals_provider] getDealDetail($id): missing opportunity_obj');
        return null;
      }

      // The deal itself is the only failure that should sink the whole load.
      Deal deal = Deal.fromJson(dealData);

      // Top-level comments override anything that might have been inlined
      // on the deal. They're freshly queried.
      try {
        final raw = data['comments'] as List<dynamic>?;
        if (raw != null) {
          deal = deal.copyWith(
            comments: raw
                .whereType<Map<String, dynamic>>()
                .map(Comment.fromJson)
                .toList(),
          );
        }
      } catch (e, st) {
        // ignore: avoid_print
        print('[deals_provider] comments parse failed: $e\n$st');
      }

      try {
        final raw = data['attachments'] as List<dynamic>?;
        if (raw != null) {
          deal = deal.copyWith(
            attachments: raw
                .whereType<Map<String, dynamic>>()
                .map(Attachment.fromJson)
                .toList(),
          );
        }
      } catch (e, st) {
        // ignore: avoid_print
        print('[deals_provider] attachments parse failed: $e\n$st');
      }

      List<CustomFieldDefinition> defs = const [];
      try {
        final defsData = data['custom_field_definitions'] as List<dynamic>?;
        defs = (defsData ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(CustomFieldDefinition.fromJson)
            .toList();
      } catch (e, st) {
        // ignore: avoid_print
        print(
          '[deals_provider] custom_field_definitions parse failed: $e\n$st',
        );
      }

      List<AssignableUser> users = const [];
      try {
        final usersData = data['users'] as List<dynamic>?;
        users = (usersData ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AssignableUser.fromJson)
            .toList();
      } catch (e, st) {
        // ignore: avoid_print
        print('[deals_provider] users parse failed: $e\n$st');
      }

      return DealDetail(
        deal: deal,
        customFieldDefinitions: defs,
        assignableUsers: users,
        commentPermission: data['comment_permission'] == true,
      );
    } catch (e, st) {
      // ignore: avoid_print
      print('[deals_provider] getDealDetail($id) threw: $e\n$st');
      return null;
    }
  }

  /// Add a comment to a deal via the POST `/<id>/` endpoint (the same
  /// endpoint that handles attachment uploads).
  Future<({bool success, String? error})> addComment(
    String dealId,
    String comment,
  ) async {
    try {
      final url = '${ApiConfig.opportunities}$dealId/';
      final response = await _apiService.post(url, {'comment': comment});
      if (response.success) return (success: true, error: null);
      return (
        success: false,
        error: response.message ?? 'Failed to add comment',
      );
    } catch (e) {
      return (success: false, error: 'Failed to add comment: ${e.toString()}');
    }
  }

  Future<({bool success, String? error})> updateComment(
    String commentId,
    String comment,
  ) async {
    try {
      final url = ApiConfig.opportunityComment(commentId);
      final response = await _apiService.patch(url, {'comment': comment});
      if (response.success) return (success: true, error: null);
      return (
        success: false,
        error: response.message ?? 'Failed to update comment',
      );
    } catch (e) {
      return (
        success: false,
        error: 'Failed to update comment: ${e.toString()}',
      );
    }
  }

  Future<({bool success, String? error})> deleteComment(
    String commentId,
  ) async {
    try {
      final url = ApiConfig.opportunityComment(commentId);
      final response = await _apiService.delete(url);
      if (response.success) return (success: true, error: null);
      return (
        success: false,
        error: response.message ?? 'Failed to delete comment',
      );
    } catch (e) {
      return (
        success: false,
        error: 'Failed to delete comment: ${e.toString()}',
      );
    }
  }

  /// Create a new deal, list refreshes on success.
  Future<({bool success, String? error, Deal? deal})> createDeal(
    Deal deal,
  ) async {
    try {
      final response = await _apiService.post(
        ApiConfig.opportunities,
        deal.toJson(),
      );

      if (response.success && response.data != null) {
        final isError = response.data!['error'] as bool? ?? true;
        if (!isError) {
          await refresh();
          return (success: true, error: null, deal: null);
        }
      }

      String errorMsg = response.message ?? 'Failed to create deal';
      if (response.data != null && response.data!['errors'] != null) {
        final errors = response.data!['errors'] as Map<String, dynamic>;
        errorMsg = errors.values
            .map((v) => v is List ? v.join(', ') : v.toString())
            .join('; ');
      }
      return (success: false, error: errorMsg, deal: null);
    } catch (e) {
      return (
        success: false,
        error: 'Failed to create deal: ${e.toString()}',
        deal: null,
      );
    }
  }

  /// Update an existing deal, list refreshes on success.
  Future<({bool success, String? error, Deal? deal})> updateDeal(
    String id,
    Deal deal,
  ) async {
    try {
      final url = '${ApiConfig.opportunities}$id/';
      final response = await _apiService.put(url, deal.toJson());

      if (response.success && response.data != null) {
        final isError = response.data!['error'] as bool? ?? true;
        if (!isError) {
          await refresh();
          return (success: true, error: null, deal: null);
        }
      }

      String errorMsg = response.message ?? 'Failed to update deal';
      if (response.data != null && response.data!['errors'] != null) {
        final errors = response.data!['errors'] as Map<String, dynamic>;
        errorMsg = errors.values
            .map((v) => v is List ? v.join(', ') : v.toString())
            .join('; ');
      }
      return (success: false, error: errorMsg, deal: null);
    } catch (e) {
      return (
        success: false,
        error: 'Failed to update deal: ${e.toString()}',
        deal: null,
      );
    }
  }

  /// Quick stage change, optimistic local update for snappy UX.
  ///
  /// Goes through the board's move endpoint rather than the deal's own
  /// `PATCH`, which is what dragging a card means and what the web board
  /// sends. Three things follow from that, none of which the deal edit path
  /// could do:
  ///
  /// * Dropping into Closed Won or Closed Lost stamps `closed_by`, and fills
  ///   `closed_on` when the deal never carried an expected close date. On the
  ///   edit path a deal with no close date was refused outright, so a drag to
  ///   Won on a fresh deal could not succeed at all.
  /// * A won deal still has to record an amount. The server answers 400 naming
  ///   the field, which the caller shows; there is no amount to collect
  ///   mid-drag, so the deal has to be opened.
  /// * `probability` is set server-side from the destination stage, so it is
  ///   no longer sent from here. `Opportunity.save()` only fills a 0/None one,
  ///   which is why the move endpoint sets it explicitly.
  ///
  /// Sending no neighbours appends to the destination column, which is where a
  /// stage change with no position in mind belongs.
  Future<({bool success, String? error})> updateDealStage(
    String id,
    DealStage stage,
  ) async {
    try {
      final response = await _apiService.patch(ApiConfig.opportunityMove(id), {
        'column_id': stage.value,
      });

      if (response.success && response.data != null) {
        final isError = response.data!['error'] as bool? ?? true;
        if (!isError) {
          final current = state.value;
          if (current != null) {
            final updatedDeals = current.deals.map((d) {
              if (d.id == id) {
                return d.copyWith(
                  stage: stage,
                  probability: stage.defaultProbability,
                  updatedAt: DateTime.now(),
                );
              }
              return d;
            }).toList();
            state = AsyncValue.data(current.copyWith(deals: updatedDeals));
          }
          return (success: true, error: null);
        }
      }

      return (
        success: false,
        error: response.message ?? 'Failed to update stage',
      );
    } catch (e) {
      return (success: false, error: 'Failed to update stage: ${e.toString()}');
    }
  }

  /// Delete a deal, local state mutation (no full refresh).
  Future<({bool success, String? error})> deleteDeal(String id) async {
    try {
      final url = '${ApiConfig.opportunities}$id/';
      final response = await _apiService.delete(url);

      if (response.success) {
        final current = state.value;
        if (current != null) {
          state = AsyncValue.data(
            current.copyWith(
              deals: current.deals.where((d) => d.id != id).toList(),
              totalCount: current.totalCount - 1,
            ),
          );
        }
        return (success: true, error: null);
      }

      return (
        success: false,
        error: response.message ?? 'Failed to delete deal',
      );
    } catch (e) {
      return (success: false, error: 'Failed to delete deal: ${e.toString()}');
    }
  }
}

final dealsProvider = AsyncNotifierProvider<DealsNotifier, DealsListData>(
  DealsNotifier.new,
);

/// Convenience providers. Read from the AsyncValue so screen code stays the
/// same shape as before the riverpod 3 migration.
final dealsListProvider = Provider<List<Deal>>((ref) {
  return ref.watch(dealsProvider).value?.deals ?? const [];
});

final dealsLoadingProvider = Provider<bool>((ref) {
  return ref.watch(dealsProvider).isLoading;
});

final dealsErrorProvider = Provider<String?>((ref) {
  return ref.watch(dealsProvider).error?.toString();
});

/// Grouped deals by stage, derived.
final dealsByStageProvider = Provider<Map<DealStage, List<Deal>>>((ref) {
  final deals = ref.watch(dealsListProvider);
  final Map<DealStage, List<Deal>> grouped = {};
  for (final stage in DealStage.values) {
    grouped[stage] = deals.where((deal) => deal.stage == stage).toList();
  }
  return grouped;
});

/// Active deals count (loaded subset). Prefer [activeDealsTotalCountProvider]
/// when you want the server-side count.
final activeDealsCountProvider = Provider<int>((ref) {
  final deals = ref.watch(dealsListProvider);
  return deals.where((d) => !d.stage.isClosed).length;
});

/// Authoritative active count. Derived from server `opportunities_count`
/// minus locally-known closed deals. Best-effort when the API doesn't
/// distinguish open/closed in its count.
final activeDealsTotalCountProvider = Provider<int>((ref) {
  final data = ref.watch(dealsProvider).value;
  if (data == null) return 0;
  // Backend `opportunities_count` is the total matching the current filters,
  // not just the loaded page. If the filter set already pins to non-closed
  // stages (rotten=true, or stage != closed_*), prefer that; otherwise fall
  // back to scanning the loaded subset.
  final loadedClosed = data.deals.where((d) => d.stage.isClosed).length;
  return (data.totalCount - loadedClosed).clamp(0, data.totalCount);
});

/// Snapshot of per-currency totals for the currently-loaded active deals.
/// The list view summarizes pipeline by currency so mixed-currency orgs
/// don't see nonsense like USD + EUR summed.
class PipelineBucket {
  final Currency currency;
  final double totalValue;
  final double weightedValue;
  final int count;
  const PipelineBucket({
    required this.currency,
    required this.totalValue,
    required this.weightedValue,
    required this.count,
  });
}

class PipelineSummary {
  final List<PipelineBucket> buckets;
  final bool loadedSubset;
  const PipelineSummary({required this.buckets, required this.loadedSubset});

  /// Currency with the largest active total, used as the "primary" chip
  /// when there are multiple currencies in play.
  PipelineBucket? get primary {
    if (buckets.isEmpty) return null;
    final sorted = [...buckets]
      ..sort((a, b) => b.totalValue.compareTo(a.totalValue));
    return sorted.first;
  }

  bool get isMixed => buckets.length > 1;
}

final pipelineSummaryProvider = Provider<PipelineSummary>((ref) {
  final data = ref.watch(dealsProvider).value;
  if (data == null) {
    return const PipelineSummary(buckets: [], loadedSubset: true);
  }
  final Map<Currency, _BucketAccum> acc = {};
  for (final d in data.deals) {
    if (d.stage.isClosed) continue;
    final a = acc.putIfAbsent(d.currency, _BucketAccum.new);
    a.total += d.value;
    a.weighted += d.value * (d.probability / 100.0);
    a.count += 1;
  }
  final buckets =
      acc.entries
          .map(
            (e) => PipelineBucket(
              currency: e.key,
              totalValue: e.value.total,
              weightedValue: e.value.weighted,
              count: e.value.count,
            ),
          )
          .toList()
        ..sort((a, b) => b.totalValue.compareTo(a.totalValue));
  // We can only mark "complete" when we've loaded every page; that lets the
  // UI add a tilde to the chip when the totals are an undercount.
  final loadedSubset = data.hasMore;
  return PipelineSummary(buckets: buckets, loadedSubset: loadedSubset);
});

class _BucketAccum {
  double total = 0;
  double weighted = 0;
  int count = 0;
}
