import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../config/api_config.dart';
import '../data/api_envelope.dart';
import '../data/models/attachment.dart';
import '../data/models/ticket.dart';
import '../data/models/comment.dart';
import '../data/models/email_message.dart';
import '../data/models/solution.dart';
import '../data/models/time_entry.dart';
import '../services/api_service.dart';

/// Paginated tickets snapshot, wrapped by AsyncValue.
class TicketsListData {
  final List<Ticket> tickets;
  final int totalCount;
  final bool hasMore;
  final int currentOffset;

  const TicketsListData({
    this.tickets = const [],
    this.totalCount = 0,
    this.hasMore = true,
    this.currentOffset = 0,
  });

  TicketsListData copyWith({
    List<Ticket>? tickets,
    int? totalCount,
    bool? hasMore,
    int? currentOffset,
  }) {
    return TicketsListData(
      tickets: tickets ?? this.tickets,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      currentOffset: currentOffset ?? this.currentOffset,
    );
  }
}

/// The query the "also open" rail asks: open tickets on one account.
///
/// `limit=6` and not 5, because this ticket is usually among them and is
/// dropped afterwards. Repeated `status=` params, the spelling the list view
/// reads (`choice_list_param`), and the same three statuses the web rail uses.
String alsoOpenQueryUrl(String accountId) {
  final statuses = openTicketStatuses
      .map((s) => 'status=${Uri.encodeQueryComponent(s.value)}')
      .join('&');
  return '${ApiConfig.tickets}?limit=6&slim=true'
      '&account=${Uri.encodeQueryComponent(accountId)}&$statuses';
}

/// Drop the ticket being read, keep five. Same as the web rail.
List<Ticket> pickAlsoOpen(
  Iterable<Ticket> rows, {
  required String excludeTicketId,
}) => rows.where((t) => t.id != excludeTicketId).take(5).toList();

class TicketsNotifier extends AsyncNotifier<TicketsListData> {
  final ApiService _apiService = ApiService();
  static const int _pageSize = 20;

  @override
  Future<TicketsListData> build() =>
      _fetchPage(offset: 0, filters: const TicketListFilters());

  Future<void> refresh({
    TicketListFilters filters = const TicketListFilters(),
  }) async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(
      () => _fetchPage(offset: 0, filters: filters),
    );
  }

  Future<void> loadMore({
    TicketListFilters filters = const TicketListFilters(),
  }) async {
    final current = state.value;
    if (current == null || !current.hasMore) return;
    if (state.isLoading) return;

    state = await AsyncValue.guard(() async {
      final next = await _fetchPage(
        offset: current.currentOffset,
        filters: filters,
      );
      return current.copyWith(
        tickets: [...current.tickets, ...next.tickets],
        totalCount: next.totalCount,
        hasMore: next.hasMore,
        currentOffset: next.currentOffset,
      );
    });
  }

  Future<TicketsListData> _fetchPage({
    required int offset,
    required TicketListFilters filters,
  }) async {
    // Watching-only flips us to a different endpoint scoped to the current
    // profile's watched cases. Same filter params apply to both.
    final endpoint = filters.watchingOnly
        ? ApiConfig.ticketsWatching
        : ApiConfig.tickets;

    final singleParams = <String, String>{
      'limit': _pageSize.toString(),
      'offset': offset.toString(),
    };
    if (filters.search.isNotEmpty) singleParams['search'] = filters.search;
    if (filters.statusList.isEmpty && filters.status != null) {
      singleParams['status'] = filters.status!;
    }
    if (filters.priority != null) singleParams['priority'] = filters.priority!;
    if (filters.accountId != null) singleParams['account'] = filters.accountId!;
    if (filters.caseType != null) singleParams['case_type'] = filters.caseType!;
    if (filters.slaBreached) singleParams['sla_breached'] = 'true';
    if (filters.createdAfter != null) {
      singleParams['created_at__gte'] = filters.createdAfter!
          .toUtc()
          .toIso8601String();
    }
    if (filters.createdBefore != null) {
      singleParams['created_at__lte'] = filters.createdBefore!
          .toUtc()
          .toIso8601String();
    }

    // Multi-value params must be repeated. http's Uri only takes the last
    // value when keys collide in a Map, so we encode by hand.
    final extra = <String>[];
    if (filters.statusList.isNotEmpty) {
      for (final s in filters.statusList) {
        extra.add('status=${Uri.encodeQueryComponent(s)}');
      }
    }
    for (final id in filters.assigneeIds) {
      extra.add('assigned_to=${Uri.encodeQueryComponent(id)}');
    }
    for (final id in filters.tagIds) {
      extra.add('tags=${Uri.encodeQueryComponent(id)}');
    }

    final base = Uri.parse(
      endpoint,
    ).replace(queryParameters: singleParams).toString();
    final url = extra.isEmpty ? base : '$base&${extra.join('&')}';

    final response = await _apiService.get(url);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load tickets');
    }

    final data = response.data!;
    final ticketsList = data['cases'] as List<dynamic>? ?? [];
    final newTickets = ticketsList
        .map((j) => Ticket.fromJson(j as Map<String, dynamic>))
        .toList();
    // CaseListView returns `cases_count` (total matching) and `offset` (next
    // page offset, or null when this was the final page). The watching view
    // returns `count` and no offset. Treat its response as a single page.
    final totalCount =
        (data['cases_count'] as int?) ??
        (data['count'] as int?) ??
        newTickets.length;
    final nextOffset = data['offset'] as int?;
    final hasMore = filters.watchingOnly ? false : nextOffset != null;

    return TicketsListData(
      tickets: newTickets,
      totalCount: totalCount,
      hasMore: hasMore,
      currentOffset: nextOffset ?? (offset + ticketsList.length),
    );
  }

  /// Fetch a single ticket (just the Ticket object).
  Future<Ticket?> getTicketById(String id) async {
    final detail = await getTicketDetail(id);
    return detail?.ticketObj;
  }

  /// Fetch the full ticket detail (incl. comments + activities + permissions).
  Future<TicketDetailResult?> getTicketDetail(String id) async {
    try {
      final response = await _apiService.get(ApiConfig.ticketDetail(id));
      if (!response.success || response.data == null) return null;

      final ticketData = response.data!['cases_obj'] as Map<String, dynamic>?;
      if (ticketData == null) return null;
      var ticketObj = Ticket.fromJson(ticketData);

      // Public + internal comments are sent in two separate arrays. We tag
      // each so the UI can render an "Internal" badge without an extra
      // round-trip; `Comment` itself doesn't carry that flag yet.
      final List<_TaggedComment> tagged = [];
      for (final c in (response.data!['comments'] as List<dynamic>? ?? [])) {
        tagged.add(
          _TaggedComment(
            comment: Comment.fromJson(c as Map<String, dynamic>),
            isInternal: false,
          ),
        );
      }
      for (final c
          in (response.data!['internal_notes'] as List<dynamic>? ?? [])) {
        tagged.add(
          _TaggedComment(
            comment: Comment.fromJson(c as Map<String, dynamic>),
            isInternal: true,
          ),
        );
      }
      tagged.sort(
        (a, b) => b.comment.commentedOn.compareTo(a.comment.commentedOn),
      );
      if (tagged.isNotEmpty) {
        ticketObj = ticketObj.copyWith(
          comments: tagged.map((t) => t.comment).toList(),
        );
      }

      final activities = (response.data!['activities'] as List<dynamic>? ?? [])
          .map((a) => a as Map<String, dynamic>)
          .toList();

      final emails = (response.data!['email_messages'] as List<dynamic>? ?? [])
          .whereType<Map<String, dynamic>>()
          .map(EmailMessage.fromJson)
          .toList();

      final mergedFrom =
          (response.data!['merged_from_cases'] as List<dynamic>? ?? [])
              .whereType<Map<String, dynamic>>()
              .map(
                (m) => MergedFromSummary(
                  id: m['id']?.toString() ?? '',
                  name: m['name']?.toString() ?? '',
                  mergedAt: m['merged_at'] != null
                      ? DateTime.tryParse(m['merged_at'].toString())
                      : null,
                ),
              )
              .toList();

      final linkedSolutions =
          (response.data!['solutions'] as List<dynamic>? ?? [])
              .whereType<Map<String, dynamic>>()
              .map(Solution.fromJson)
              .toList();

      // `CaseDetailView` has returned these all along. Nothing on the phone
      // read them, so a screenshot attached to a ticket was invisible here.
      final attachments =
          (response.data!['attachments'] as List<dynamic>? ?? [])
              .whereType<Map<String, dynamic>>()
              .map(Attachment.fromJson)
              .toList();

      return TicketDetailResult(
        ticketObj: ticketObj,
        activities: activities,
        attachments: attachments,
        emails: emails,
        mergedFromCases: mergedFrom,
        linkedSolutions: linkedSolutions,
        commentPermission:
            response.data!['comment_permission'] as bool? ?? false,
        internalCommentIds: tagged
            .where((t) => t.isInternal)
            .map((t) => t.comment.id)
            .toSet(),
      );
    } catch (_) {
      return null;
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> createTicket(
    Map<String, dynamic> ticketData,
  ) async {
    try {
      final response = await _apiService.post(ApiConfig.tickets, ticketData);
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// `PATCH /api/cases/<id>/`, not PUT.
  ///
  /// PUT is a full replace: `CaseDetailView.put` clears `contacts`, `teams`,
  /// `assigned_to` and `tags` unconditionally and re-adds only what the body
  /// carries. This form sent no `contacts` and no `teams`, so every edit made
  /// from the phone silently unlinked every contact and every team on the
  /// ticket, whatever the edit was actually about. PATCH touches an M2M only
  /// when the key is present, which is what a partial form means.
  ///
  /// The web app has always used PATCH. Assignment notification is on both
  /// verbs now (`cases.views.notify_newly_assigned`), so switching loses
  /// nothing.
  Future<ApiResponse<Map<String, dynamic>>> updateTicket(
    String id,
    Map<String, dynamic> ticketData,
  ) async {
    try {
      final response = await _apiService.patch(
        ApiConfig.ticketDetail(id),
        ticketData,
      );
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> addComment(
    String ticketId,
    String comment, {
    bool isInternal = false,
  }) async {
    try {
      final response = await _apiService.post(
        ApiConfig.ticketDetail(ticketId),
        {'comment': comment, 'is_internal': isInternal},
      );
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Fetch watcher list for a ticket.
  /// Returns ({watcherCount, isCurrentUserWatching, watchers}) or null on error.
  /// The other open tickets on the same account, most recent first.
  ///
  /// A second request because the detail endpoint has no idea what else is
  /// happening on that account. The web rail asks the same question the same
  /// way, so both clients rank the same rows: open statuses only, this ticket
  /// dropped, five kept.
  ///
  /// Degrades to an empty list rather than failing the screen. It is context
  /// beside the ticket, not the ticket.
  Future<List<Ticket>> getAlsoOpenOnAccount({
    required String accountId,
    required String excludeTicketId,
  }) async {
    if (accountId.isEmpty) return const [];
    try {
      final response = await _apiService.get(alsoOpenQueryUrl(accountId));
      if (!response.success || response.data == null) return const [];
      return pickAlsoOpen(
        listFromEnvelope(response.data!, ['cases']).map(Ticket.fromJson),
        excludeTicketId: excludeTicketId,
      );
    } catch (_) {
      return const [];
    }
  }

  Future<TicketWatchers?> getWatchers(String id) async {
    try {
      final response = await _apiService.get(ApiConfig.ticketWatchers(id));
      if (!response.success || response.data == null) return null;
      final data = response.data!;
      final list = (data['watchers'] as List<dynamic>? ?? [])
          .whereType<Map<String, dynamic>>()
          .toList();
      return TicketWatchers(
        watchers: list,
        count: list.length,
        isCurrentUserWatching:
            data['is_current_user_watching'] as bool? ?? false,
      );
    } catch (_) {
      return null;
    }
  }

  /// Fetch all time entries for a ticket (most recent first).
  Future<List<TimeEntry>> fetchTimeEntries(String id) async {
    try {
      // Backend returns a bare array, not an envelope.
      final response = await _apiService.getList(
        ApiConfig.ticketTimeEntries(id),
      );
      if (!response.success || response.data == null) return const [];
      return response.data!
          .whereType<Map<String, dynamic>>()
          .map(TimeEntry.fromJson)
          .toList();
    } catch (_) {
      return const [];
    }
  }

  /// Fetch ticket time summary (totals + per-profile).
  Future<TimeSummary?> fetchTimeSummary(String id) async {
    try {
      final response = await _apiService.get(ApiConfig.ticketTimeSummary(id));
      if (!response.success || response.data == null) return null;
      return TimeSummary.fromJson(response.data!);
    } catch (_) {
      return null;
    }
  }

  /// Start a running timer on a ticket.
  Future<ApiResponse<Map<String, dynamic>>> startTimer(
    String id, {
    bool billable = false,
    String description = '',
  }) async {
    try {
      return await _apiService.post(ApiConfig.ticketTimerStart(id), {
        'billable': billable,
        'description': description,
      });
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Stop a running timer by entry id.
  Future<ApiResponse<Map<String, dynamic>>> stopTimer(String entryId) async {
    try {
      return await _apiService.post(ApiConfig.timeEntryStop(entryId), const {});
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Add a manual completed time entry.
  Future<ApiResponse<Map<String, dynamic>>> addManualTimeEntry(
    String id, {
    required DateTime startedAt,
    required DateTime endedAt,
    bool billable = false,
    String description = '',
  }) async {
    try {
      return await _apiService.post(ApiConfig.ticketTimeEntries(id), {
        'started_at': startedAt.toUtc().toIso8601String(),
        'ended_at': endedAt.toUtc().toIso8601String(),
        'billable': billable,
        'description': description,
      });
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Fetch parent/child tree (rooted at the closest visible ancestor).
  Future<TicketTreeNode?> fetchTree(String id) async {
    try {
      final response = await _apiService.get(ApiConfig.ticketTree(id));
      if (!response.success || response.data == null) return null;
      final root = response.data!['root'] as Map<String, dynamic>?;
      final focusId = response.data!['focus_id']?.toString();
      if (root == null) return null;
      return TicketTreeNode.fromJson(root, focusId: focusId);
    } catch (_) {
      return null;
    }
  }

  /// Link to a parent (pass null to detach).
  Future<ApiResponse<Map<String, dynamic>>> linkParent(
    String id,
    String? parentId,
  ) async {
    try {
      return await _apiService.post(ApiConfig.ticketLinkParent(id), {
        'parent_id': parentId,
      });
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Close this ticket; optionally cascade-close its open descendants.
  ///
  /// `cascade` is always sent. The endpoint falls back to the org's
  /// `auto_close_children_on_parent_close` only when the key is ABSENT, so
  /// omitting it would hand the decision to a setting the person confirming
  /// never saw. The prompt seeds its checkbox from that setting instead.
  ///
  /// `resolutionComment` lands in the `PARENT_CLOSED_CASCADE` activity row on
  /// every cascaded child, so it is the only explanation anyone opening one of
  /// those tickets will find. This used to send nothing at all, which left that
  /// field empty on every ticket the phone ever cascade-closed.
  Future<ApiResponse<Map<String, dynamic>>> closeWithChildren(
    String id, {
    bool cascade = true,
    String resolutionComment = '',
  }) async {
    try {
      return await _apiService.post(ApiConfig.ticketCloseWithChildren(id), {
        'cascade': cascade,
        'resolution_comment': resolutionComment.trim(),
      });
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Merge `sourceId` as a duplicate INTO `intoId`. Source becomes inactive
  /// and its comments/attachments/emails move to the target.
  Future<ApiResponse<Map<String, dynamic>>> mergeInto(
    String sourceId,
    String intoId,
  ) async {
    try {
      return await _apiService.post(
        ApiConfig.ticketMerge(sourceId, intoId),
        const {},
      );
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Reverse a prior merge; called on the source ticket id.
  Future<ApiResponse<Map<String, dynamic>>> unmerge(String sourceId) async {
    try {
      return await _apiService.post(
        ApiConfig.ticketUnmerge(sourceId),
        const {},
      );
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Toggle the current user's watch state on a ticket.
  Future<bool> setWatching(String id, bool watch) async {
    try {
      final response = watch
          ? await _apiService.post(ApiConfig.ticketWatch(id), const {})
          : await _apiService.delete(ApiConfig.ticketWatch(id));
      return response.success;
    } catch (_) {
      return false;
    }
  }

  /// Bulk-update the given tickets in one call. `fields` is applied
  /// server-side: `tags` appends to each ticket's existing tags, `assigned_to`
  /// replaces them, and every other field is a plain overwrite.
  ///
  /// Returns the raw `{error, updated, results}` envelope untouched. This
  /// layer does not summarize it or refresh local state; the caller (the
  /// bulk-actions UI) does both, since it also owns the current selection.
  Future<ApiResponse<Map<String, dynamic>>> bulkUpdate(
    List<String> ids,
    Map<String, dynamic> fields,
  ) async {
    try {
      return await _apiService.post(ApiConfig.casesBulkUpdate, {
        'ids': ids,
        'fields': fields,
      });
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Bulk-delete the given tickets in one call. Per-record authorization
  /// (admin or creator) is enforced server-side, so a caller with no rights
  /// on some of the ids gets those reported as failures in `results` rather
  /// than the whole request being refused.
  ///
  /// Returns the raw `{error, deleted, results}` envelope untouched, same as
  /// [bulkUpdate].
  Future<ApiResponse<Map<String, dynamic>>> bulkDelete(
    List<String> ids,
  ) async {
    try {
      return await _apiService.post(ApiConfig.casesBulkDelete, {'ids': ids});
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> deleteTicket(String id) async {
    try {
      final response = await _apiService.delete(ApiConfig.ticketDetail(id));
      if (response.success) {
        final current = state.value;
        if (current != null) {
          state = AsyncValue.data(
            current.copyWith(
              tickets: current.tickets.where((t) => t.id != id).toList(),
              totalCount: current.totalCount > 0 ? current.totalCount - 1 : 0,
            ),
          );
        }
      }
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final ticketsProvider = AsyncNotifierProvider<TicketsNotifier, TicketsListData>(
  TicketsNotifier.new,
);

/// Convenience providers. Read from the AsyncValue.
final ticketsListProvider = Provider<List<Ticket>>((ref) {
  return ref.watch(ticketsProvider).value?.tickets ?? const [];
});

final ticketsLoadingProvider = Provider<bool>((ref) {
  return ref.watch(ticketsProvider).isLoading;
});

final ticketsErrorProvider = Provider<String?>((ref) {
  return ref.watch(ticketsProvider).error?.toString();
});

/// Bundled detail-fetch result. The mobile detail screen needs more than
/// just the Ticket object, also activities and the per-user permission to
/// add comments.
class TicketDetailResult {
  final Ticket ticketObj;
  final List<Map<String, dynamic>> activities;
  final List<EmailMessage> emails;
  final List<MergedFromSummary> mergedFromCases;
  final List<Solution> linkedSolutions;
  final List<Attachment> attachments;
  final bool commentPermission;
  final Set<String> internalCommentIds;

  const TicketDetailResult({
    required this.ticketObj,
    required this.activities,
    this.emails = const [],
    this.mergedFromCases = const [],
    this.linkedSolutions = const [],
    this.attachments = const [],
    required this.commentPermission,
    required this.internalCommentIds,
  });

  TicketDetailResult copyWith({List<Attachment>? attachments}) {
    return TicketDetailResult(
      ticketObj: ticketObj,
      activities: activities,
      emails: emails,
      mergedFromCases: mergedFromCases,
      linkedSolutions: linkedSolutions,
      attachments: attachments ?? this.attachments,
      commentPermission: commentPermission,
      internalCommentIds: internalCommentIds,
    );
  }
}

/// Recursive tree node returned by `/api/cases/{id}/tree/`.
class TicketTreeNode {
  final String id;
  final String name;
  final String? status;
  final String? priority;
  final bool isProblem;
  final bool isActive;
  final bool truncated;
  final List<TicketTreeNode> children;
  final String? focusId;

  const TicketTreeNode({
    required this.id,
    required this.name,
    this.status,
    this.priority,
    this.isProblem = false,
    this.isActive = true,
    this.truncated = false,
    this.children = const [],
    this.focusId,
  });

  bool get isFocused => focusId != null && focusId == id;

  factory TicketTreeNode.fromJson(
    Map<String, dynamic> json, {
    String? focusId,
  }) {
    return TicketTreeNode(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      status: json['status'] as String?,
      priority: json['priority'] as String?,
      isProblem: json['is_problem'] as bool? ?? false,
      isActive: json['is_active'] as bool? ?? true,
      truncated: json['truncated'] as bool? ?? false,
      children: ((json['children'] as List<dynamic>?) ?? const [])
          .whereType<Map<String, dynamic>>()
          .map((c) => TicketTreeNode.fromJson(c, focusId: focusId))
          .toList(),
      focusId: focusId,
    );
  }

  /// The node for [id] anywhere in this tree, or null.
  TicketTreeNode? find(String id) {
    if (id.isEmpty) return null;
    if (this.id == id) return this;
    for (final child in children) {
      final hit = child.find(id);
      if (hit != null) return hit;
    }
    return null;
  }

  /// The tickets a cascading close of [id] would actually close.
  ///
  /// **This tree's root is not the ticket being closed.** `CaseTreeView` walks
  /// UP to the highest ancestor in the org, so the subtree that closes has to
  /// be found inside the response first. Walking from the root instead would
  /// list the ticket's parent, siblings and cousins as about to close, none of
  /// which `_open_descendants` touches.
  ///
  /// Mirrors `_open_descendants` in `cases/parent_views.py`, including the two
  /// easy mistakes: a node counts only when it is open AND active, and
  /// recursion goes through closed nodes anyway, so an open grandchild under a
  /// closed child still cascades.
  ///
  /// `frontend/src/routes/(app)/tickets/[id]/close.js` carries the same rules.
  List<TicketTreeNode> openDescendantsOf(String id) {
    final focus = find(id);
    if (focus == null) return const [];
    final out = <TicketTreeNode>[];
    void walk(TicketTreeNode node) {
      for (final child in node.children) {
        if (child.status != 'Closed' && child.isActive) out.add(child);
        walk(child);
      }
    }

    walk(focus);
    return out;
  }

  /// True when the API stopped at its depth cap inside [id]'s subtree, so the
  /// list above is a floor. The close itself has no such cap.
  bool subtreeTruncatedFor(String id) {
    final focus = find(id);
    if (focus == null) return false;
    bool walk(TicketTreeNode node) => node.truncated || node.children.any(walk);
    return walk(focus);
  }
}

/// Lightweight reference to a ticket that was merged into the current one.
class MergedFromSummary {
  final String id;
  final String name;
  final DateTime? mergedAt;
  const MergedFromSummary({
    required this.id,
    required this.name,
    this.mergedAt,
  });
}

/// Bundle of filters applied to the tickets list.
///
/// All filters are server-side now (backend accepts the same params on
/// `/cases/` and `/cases/watching/`). `statusList` is the multi-status form
/// used by the Open/Closed quick chips; if it's empty we fall back to the
/// single-value `status` field.
class TicketListFilters {
  final String search;
  final String? status;
  final List<String> statusList;
  final String? priority;
  final String? accountId;
  final String? caseType;
  final List<String> assigneeIds;
  final List<String> tagIds;
  final bool watchingOnly;
  final bool slaBreached;
  final DateTime? createdAfter;
  final DateTime? createdBefore;

  const TicketListFilters({
    this.search = '',
    this.status,
    this.statusList = const [],
    this.priority,
    this.accountId,
    this.caseType,
    this.assigneeIds = const [],
    this.tagIds = const [],
    this.watchingOnly = false,
    this.slaBreached = false,
    this.createdAfter,
    this.createdBefore,
  });

  bool get hasAny =>
      search.isNotEmpty ||
      status != null ||
      statusList.isNotEmpty ||
      priority != null ||
      accountId != null ||
      caseType != null ||
      assigneeIds.isNotEmpty ||
      tagIds.isNotEmpty ||
      watchingOnly ||
      slaBreached ||
      createdAfter != null ||
      createdBefore != null;

  TicketListFilters copyWith({
    String? search,
    String? status,
    List<String>? statusList,
    String? priority,
    String? accountId,
    String? caseType,
    List<String>? assigneeIds,
    List<String>? tagIds,
    bool? watchingOnly,
    bool? slaBreached,
    DateTime? createdAfter,
    DateTime? createdBefore,
    bool clearStatus = false,
    bool clearStatusList = false,
    bool clearPriority = false,
    bool clearAccountId = false,
    bool clearCaseType = false,
    bool clearCreatedAfter = false,
    bool clearCreatedBefore = false,
  }) {
    return TicketListFilters(
      search: search ?? this.search,
      status: clearStatus ? null : (status ?? this.status),
      statusList: clearStatusList ? const [] : (statusList ?? this.statusList),
      priority: clearPriority ? null : (priority ?? this.priority),
      accountId: clearAccountId ? null : (accountId ?? this.accountId),
      caseType: clearCaseType ? null : (caseType ?? this.caseType),
      assigneeIds: assigneeIds ?? this.assigneeIds,
      tagIds: tagIds ?? this.tagIds,
      watchingOnly: watchingOnly ?? this.watchingOnly,
      slaBreached: slaBreached ?? this.slaBreached,
      createdAfter: clearCreatedAfter
          ? null
          : (createdAfter ?? this.createdAfter),
      createdBefore: clearCreatedBefore
          ? null
          : (createdBefore ?? this.createdBefore),
    );
  }

  /// JSON-encode for SharedPreferences. ISO-8601 for dates; default field
  /// values are not omitted so we get a stable round-trip on decode.
  Map<String, dynamic> toJson() => {
    'search': search,
    if (status != null) 'status': status,
    'statusList': statusList,
    if (priority != null) 'priority': priority,
    if (accountId != null) 'accountId': accountId,
    if (caseType != null) 'caseType': caseType,
    'assigneeIds': assigneeIds,
    'tagIds': tagIds,
    'watchingOnly': watchingOnly,
    'slaBreached': slaBreached,
    if (createdAfter != null) 'createdAfter': createdAfter!.toIso8601String(),
    if (createdBefore != null)
      'createdBefore': createdBefore!.toIso8601String(),
  };

  factory TicketListFilters.fromJson(Map<String, dynamic> json) {
    return TicketListFilters(
      search: json['search'] as String? ?? '',
      status: json['status'] as String?,
      statusList: ((json['statusList'] as List<dynamic>?) ?? const [])
          .whereType<String>()
          .toList(),
      priority: json['priority'] as String?,
      accountId: json['accountId'] as String?,
      caseType: json['caseType'] as String?,
      assigneeIds: ((json['assigneeIds'] as List<dynamic>?) ?? const [])
          .whereType<String>()
          .toList(),
      tagIds: ((json['tagIds'] as List<dynamic>?) ?? const [])
          .whereType<String>()
          .toList(),
      watchingOnly: json['watchingOnly'] as bool? ?? false,
      slaBreached: json['slaBreached'] as bool? ?? false,
      createdAfter: json['createdAfter'] != null
          ? DateTime.tryParse(json['createdAfter'] as String)
          : null,
      createdBefore: json['createdBefore'] != null
          ? DateTime.tryParse(json['createdBefore'] as String)
          : null,
    );
  }
}

/// Watcher list response, used by the detail screen's sidebar.
class TicketWatchers {
  final List<Map<String, dynamic>> watchers;
  final int count;
  final bool isCurrentUserWatching;
  const TicketWatchers({
    required this.watchers,
    required this.count,
    required this.isCurrentUserWatching,
  });
}

class _TaggedComment {
  final Comment comment;
  final bool isInternal;
  const _TaggedComment({required this.comment, required this.isInternal});
}
