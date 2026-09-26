import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../config/api_config.dart';
import '../data/models/attachment.dart';
import '../data/models/custom_field_definition.dart';
import '../data/models/lead.dart';
import '../data/models/lead_board.dart' show LeadStageRef;
import '../data/models/comment.dart';
import '../services/api_service.dart';

/// Bundle returned by [LeadsNotifier.getLeadDetail]. The detail endpoint
/// returns the lead alongside lookups (custom-field schema, attachments,
/// assignable users) that the detail screen needs to render and act on.
class LeadDetail {
  final Lead lead;
  final List<CustomFieldDefinition> customFieldDefinitions;
  final List<AssignableUser> assignableUsers;

  /// The pipeline stage the lead is in, or null when it is in none.
  final LeadStageRef? stage;

  const LeadDetail({
    required this.lead,
    this.customFieldDefinitions = const [],
    this.assignableUsers = const [],
    this.stage,
  });
}

/// Result of [LeadsNotifier.findLeadByEmail], just enough to render the
/// "duplicate found" hint and link to the existing lead.
class LeadEmailMatch {
  final String id;
  final String label;
  const LeadEmailMatch({required this.id, required this.label});
}

/// Minimal profile shape used by the assignee picker. Mirrors what the
/// backend `users` array returns under the lead detail endpoint.
class AssignableUser {
  final String id;
  final String label;
  final String? profilePic;
  const AssignableUser({
    required this.id,
    required this.label,
    this.profilePic,
  });

  factory AssignableUser.fromJson(Map<String, dynamic> json) {
    final details = json['user_details'];
    String label = '';
    String? pic;
    if (details is Map<String, dynamic>) {
      label = (details['name'] as String?)?.trim().isNotEmpty == true
          ? details['name'] as String
          : (details['email'] as String? ?? '');
      pic = details['profile_pic'] as String?;
    }
    return AssignableUser(
      id: json['id']?.toString() ?? '',
      label: label.isEmpty ? 'Unknown' : label,
      profilePic: pic,
    );
  }
}

/// Server-side filter state for the leads list. Each non-null field becomes
/// a query param on `/api/leads/`.
class LeadFilters {
  final String? search;
  // A set rather than one value, because the dashboard's Hot Leads badge means
  // "assigned or in process", which is narrower than the open-leads list's own
  // "not converted, not closed". The filter sheet is still single-select and
  // stores its choice as a one-element set.
  final Set<LeadStatus> statuses;
  final LeadSource? source;
  final LeadRating? rating;
  // Assignee profile id. null = anyone (no filter).
  final String? assignedToId;
  // Display label for the active assignee filter (e.g. "Me"). Not sent to
  // backend; used by the UI to render the chip without a profile lookup.
  final String? assignedToLabel;
  final String? tagId;
  final String? tagLabel;
  // ISO date (yyyy-MM-dd). Follow-ups due on exactly this day.
  final String? nextFollowUp;

  const LeadFilters({
    this.search,
    this.statuses = const {},
    this.source,
    this.rating,
    this.assignedToId,
    this.assignedToLabel,
    this.tagId,
    this.tagLabel,
    this.nextFollowUp,
  });

  /// The single selected status, or null when none or several are selected.
  /// The filter sheet is single-select, so this is what it reads and shows.
  LeadStatus? get status => statuses.length == 1 ? statuses.first : null;

  /// The dashboard's "Hot Leads" badge: rated hot, and still being worked.
  /// `ApiHomeView` scopes that count to these two statuses, which is narrower
  /// than the open-leads list's own "not converted, not closed", so leaving
  /// them off would show a bigger number than the badge that opened the list.
  factory LeadFilters.hot() => const LeadFilters(
    rating: LeadRating.hot,
    statuses: {LeadStatus.assigned, LeadStatus.inProcess},
  );

  /// The dashboard's "Follow-ups" badge: a follow-up falling on the given day.
  factory LeadFilters.followUpsOn(DateTime day) => LeadFilters(
    nextFollowUp:
        '${day.year.toString().padLeft(4, '0')}-'
        '${day.month.toString().padLeft(2, '0')}-'
        '${day.day.toString().padLeft(2, '0')}',
  );

  bool get isEmpty =>
      (search == null || search!.isEmpty) &&
      statuses.isEmpty &&
      source == null &&
      rating == null &&
      (assignedToId == null || assignedToId!.isEmpty) &&
      tagId == null &&
      nextFollowUp == null;

  bool get isActive => !isEmpty;

  /// Like copyWith but lets callers explicitly clear individual fields by
  /// passing the sentinel below. Dart copyWith can't distinguish "no change"
  /// from "set to null", so we use named clear flags.
  LeadFilters cleared({
    bool search = false,
    bool status = false,
    bool source = false,
    bool rating = false,
    bool assignedTo = false,
    bool tag = false,
    bool nextFollowUp = false,
  }) {
    return LeadFilters(
      search: search ? null : this.search,
      statuses: status ? const {} : statuses,
      source: source ? null : this.source,
      rating: rating ? null : this.rating,
      assignedToId: assignedTo ? null : assignedToId,
      assignedToLabel: assignedTo ? null : assignedToLabel,
      tagId: tag ? null : tagId,
      tagLabel: tag ? null : tagLabel,
      nextFollowUp: nextFollowUp ? null : this.nextFollowUp,
    );
  }

  LeadFilters withSearch(String? value) => LeadFilters(
    search: (value == null || value.isEmpty) ? null : value,
    statuses: statuses,
    source: source,
    rating: rating,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withStatus(LeadStatus? value) =>
      withStatuses(value == null ? const {} : {value});

  LeadFilters withStatuses(Set<LeadStatus> value) => LeadFilters(
    search: search,
    statuses: value,
    source: source,
    rating: rating,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withSource(LeadSource? value) => LeadFilters(
    search: search,
    statuses: statuses,
    source: value,
    rating: rating,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withRating(LeadRating? value) => LeadFilters(
    search: search,
    statuses: statuses,
    source: source,
    rating: value,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withAssignee({String? id, String? label}) => LeadFilters(
    search: search,
    statuses: statuses,
    source: source,
    rating: rating,
    assignedToId: id,
    assignedToLabel: label,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withTag({String? id, String? label}) => LeadFilters(
    search: search,
    statuses: statuses,
    source: source,
    rating: rating,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: id,
    tagLabel: label,
    nextFollowUp: nextFollowUp,
  );

  LeadFilters withNextFollowUp(String? value) => LeadFilters(
    search: search,
    statuses: statuses,
    source: source,
    rating: rating,
    assignedToId: assignedToId,
    assignedToLabel: assignedToLabel,
    tagId: tagId,
    tagLabel: tagLabel,
    nextFollowUp: value,
  );
}

/// Paginated leads snapshot, wrapped by AsyncValue.
class LeadsListData {
  final List<Lead> leads;
  final int totalCount;
  final bool hasMore;
  final int currentOffset;

  const LeadsListData({
    this.leads = const [],
    this.totalCount = 0,
    this.hasMore = true,
    this.currentOffset = 0,
  });

  LeadsListData copyWith({
    List<Lead>? leads,
    int? totalCount,
    bool? hasMore,
    int? currentOffset,
  }) {
    return LeadsListData(
      leads: leads ?? this.leads,
      totalCount: totalCount ?? this.totalCount,
      hasMore: hasMore ?? this.hasMore,
      currentOffset: currentOffset ?? this.currentOffset,
    );
  }
}

class LeadsNotifier extends AsyncNotifier<LeadsListData> {
  final ApiService _apiService = ApiService();
  static const int _pageSize = 20;

  LeadFilters _filters = const LeadFilters();
  LeadFilters get filters => _filters;

  @override
  Future<LeadsListData> build() => _fetchPage(offset: 0);

  /// Replace filters and refetch from offset 0.
  Future<void> setFilters(LeadFilters filters) async {
    _filters = filters;
    await refresh();
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetchPage(offset: 0));
  }

  Future<void> loadMore() async {
    final current = state.value;
    if (current == null || !current.hasMore) return;
    if (state.isLoading) return;

    state = await AsyncValue.guard(() async {
      final next = await _fetchPage(offset: current.currentOffset);
      return current.copyWith(
        leads: [...current.leads, ...next.leads],
        totalCount: next.totalCount,
        hasMore: next.hasMore,
        currentOffset: next.currentOffset,
      );
    });
  }

  /// The list's filters as the API reads them, without paging. The list
  /// asks with these and so does its CSV export, so the file holds what the
  /// screen shows. A `List` value is a repeated parameter, which is how the
  /// API takes more than one status.
  Map<String, Object> get filterQuery {
    final f = _filters;
    return {
      if (f.search != null && f.search!.isNotEmpty) 'search': f.search!,
      if (f.statuses.isNotEmpty)
        'status': f.statuses.map((s) => s.value).toList(),
      if (f.source != null) 'source': f.source!.value,
      if (f.rating != null) 'rating': f.rating!.value,
      if (f.assignedToId != null && f.assignedToId!.isNotEmpty)
        'assigned_to': f.assignedToId!,
      if (f.tagId != null && f.tagId!.isNotEmpty) 'tags': f.tagId!,
      if (f.nextFollowUp != null && f.nextFollowUp!.isNotEmpty)
        'next_follow_up': f.nextFollowUp!,
    };
  }

  Future<LeadsListData> _fetchPage({required int offset}) async {
    // `dynamic` so a value can be a List: `Uri.replace` turns one into a
    // repeated parameter.
    final queryParams = <String, dynamic>{
      'limit': _pageSize.toString(),
      'offset': offset.toString(),
      ...filterQuery,
    };

    final url = Uri.parse(
      ApiConfig.leads,
    ).replace(queryParameters: queryParams).toString();
    final response = await _apiService.get(url);

    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Failed to load leads');
    }

    final data = response.data!;

    // Backend returns open_leads + close_leads in two sub-objects.
    final openLeadsData = data['open_leads'] as Map<String, dynamic>?;
    final openLeadsList = openLeadsData?['open_leads'] as List<dynamic>? ?? [];
    final openLeadsCount = openLeadsData?['leads_count'] as int? ?? 0;

    final closeLeadsData = data['close_leads'] as Map<String, dynamic>?;
    final closeLeadsList =
        closeLeadsData?['close_leads'] as List<dynamic>? ?? [];

    final allLeadsList = [...openLeadsList, ...closeLeadsList];
    final newLeads = allLeadsList
        .map((json) => Lead.fromJson(json as Map<String, dynamic>))
        .toList();

    final totalCount =
        openLeadsCount + (closeLeadsData?['leads_count'] as int? ?? 0);

    return LeadsListData(
      leads: newLeads,
      totalCount: totalCount,
      hasMore: newLeads.length >= _pageSize,
      currentOffset: offset + newLeads.length,
    );
  }

  /// Fetch a single lead from the API (with comments + attachments). Returns
  /// just the lead. Callers that also need custom-field schema or the list of
  /// assignable users should use [getLeadDetail] instead.
  Future<Lead?> getLeadById(String id) async {
    final detail = await getLeadDetail(id);
    return detail?.lead;
  }

  /// Fetch a lead together with everything the detail screen needs to render
  /// and act on it: top-level comments and attachments, custom-field
  /// definitions, and the assignable user list. Keeps lookups in one place
  /// rather than scattering parallel calls across the screen.
  Future<LeadDetail?> getLeadDetail(String id) async {
    try {
      final url = '${ApiConfig.leads}$id/';
      final response = await _apiService.get(url);

      if (!response.success || response.data == null) {
        // ignore: avoid_print
        print(
          '[leads_provider] getLeadDetail($id) HTTP failed: '
          'status=${response.statusCode} message=${response.message}',
        );
        return null;
      }

      final data = response.data!;
      final leadData = data['lead_obj'] as Map<String, dynamic>?;
      if (leadData == null) {
        // ignore: avoid_print
        print('[leads_provider] getLeadDetail($id): missing lead_obj');
        return null;
      }

      // Parse the lead itself. This is the only failure that should sink
      // the whole detail load. Everything else is a soft-fail.
      final Lead baseLead = Lead.fromJson(leadData);
      Lead lead = baseLead;

      // Backend returns comments at the top level. Prefer those over
      // anything embedded on the lead since they're freshly queried.
      try {
        final commentsData = data['comments'] as List<dynamic>?;
        if (commentsData != null && commentsData.isNotEmpty) {
          lead = lead.copyWith(
            comments: commentsData
                .whereType<Map<String, dynamic>>()
                .map(Comment.fromJson)
                .toList(),
          );
        }
      } catch (e, st) {
        // ignore: avoid_print
        print('[leads_provider] comments parse failed: $e\n$st');
      }

      try {
        final attachmentsData = data['attachments'] as List<dynamic>?;
        if (attachmentsData != null && attachmentsData.isNotEmpty) {
          lead = lead.copyWith(
            attachments: attachmentsData
                .whereType<Map<String, dynamic>>()
                .map(Attachment.fromJson)
                .toList(),
          );
        }
      } catch (e, st) {
        // ignore: avoid_print
        print('[leads_provider] attachments parse failed: $e\n$st');
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
          '[leads_provider] custom_field_definitions parse failed: $e\n$st',
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
        print('[leads_provider] users parse failed: $e\n$st');
      }

      return LeadDetail(
        lead: lead,
        customFieldDefinitions: defs,
        assignableUsers: users,
        // `lead_obj.stage` is only an id; the names travel beside it.
        stage: LeadStageRef.fromJson(data['pipeline_stage']),
      );
    } catch (e, st) {
      // ignore: avoid_print
      print('[leads_provider] getLeadDetail($id) threw: $e\n$st');
      return null;
    }
  }

  /// Lightweight duplicate-check: query the list endpoint with `?email=` and
  /// return a stub for the first match (or null). Used by the create form to
  /// warn the user that a lead with the same email already exists. icontains
  /// at the DB level may match `bob@x.com` for `b@x.com`, so we re-filter on
  /// the client for an exact (case-insensitive) match.
  Future<LeadEmailMatch?> findLeadByEmail(String email) async {
    final trimmed = email.trim();
    if (trimmed.isEmpty) return null;
    try {
      final url = Uri.parse(
        ApiConfig.leads,
      ).replace(queryParameters: {'email': trimmed, 'limit': '5'}).toString();
      final response = await _apiService.get(url);
      if (!response.success || response.data == null) return null;

      final data = response.data!;
      Iterable<dynamic> rows = const [];
      final openLeads = data['open_leads'] as Map<String, dynamic>?;
      if (openLeads != null) {
        rows = [...((openLeads['open_leads'] as List<dynamic>?) ?? const [])];
      }
      final closeLeads = data['close_leads'] as Map<String, dynamic>?;
      if (closeLeads != null) {
        rows = [
          ...rows,
          ...((closeLeads['close_leads'] as List<dynamic>?) ?? const []),
        ];
      }

      final needle = trimmed.toLowerCase();
      for (final raw in rows) {
        if (raw is! Map<String, dynamic>) continue;
        final candidateEmail = (raw['email'] as String? ?? '').toLowerCase();
        if (candidateEmail != needle) continue;

        final id = raw['id']?.toString();
        if (id == null || id.isEmpty) continue;
        final first = (raw['first_name'] as String? ?? '').trim();
        final last = (raw['last_name'] as String? ?? '').trim();
        final company = (raw['company_name'] as String? ?? '').trim();
        final name = [first, last].where((s) => s.isNotEmpty).join(' ').trim();
        final label = [
          if (name.isNotEmpty) name,
          if (company.isNotEmpty) '($company)',
        ].join(' ').trim();
        return LeadEmailMatch(id: id, label: label.isEmpty ? trimmed : label);
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> createLead(
    Map<String, dynamic> leadData,
  ) async {
    try {
      final response = await _apiService.post(ApiConfig.leads, leadData);
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// PATCH, never PUT. `LeadDetailView.put` clears `contacts`, `teams`,
  /// `tags` and `assigned_to` whether or not the body mentions them. The edit
  /// form sends neither contacts nor teams, and the detail screen's sheets
  /// send a single key, so a PUT from either one stripped the rest. PATCH
  /// touches a relation only when its key is present, and saves the other
  /// fields before converting when the body also sets `converted`.
  Future<ApiResponse<Map<String, dynamic>>> updateLead(
    String id,
    Map<String, dynamic> leadData,
  ) async {
    try {
      final url = '${ApiConfig.leads}$id/';
      final response = await _apiService.patch(url, leadData);
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Quick status change, optimistic local update.
  Future<ApiResponse<Map<String, dynamic>>> updateLeadStatus(
    String id,
    LeadStatus status,
  ) async {
    try {
      final url = '${ApiConfig.leads}$id/';
      final response = await _apiService.patch(url, {'status': status.value});

      if (response.success) {
        final current = state.value;
        if (current != null) {
          final updatedLeads = current.leads.map((l) {
            if (l.id == id) return l.copyWith(status: status);
            return l;
          }).toList();
          state = AsyncValue.data(current.copyWith(leads: updatedLeads));
        }
      }
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> deleteLead(String id) async {
    try {
      final url = '${ApiConfig.leads}$id/';
      final response = await _apiService.delete(url);
      if (response.success) {
        final current = state.value;
        if (current != null) {
          state = AsyncValue.data(
            current.copyWith(
              leads: current.leads.where((l) => l.id != id).toList(),
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

  Future<ApiResponse<Map<String, dynamic>>> addComment(
    String leadId,
    String comment,
  ) async {
    try {
      final url = '${ApiConfig.leads}$leadId/';
      return await _apiService.post(url, {'comment': comment});
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> updateComment(
    String commentId,
    String comment,
  ) async {
    try {
      final url = ApiConfig.leadComment(commentId);
      return await _apiService.patch(url, {'comment': comment});
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  Future<ApiResponse<Map<String, dynamic>>> deleteComment(
    String commentId,
  ) async {
    try {
      final url = ApiConfig.leadComment(commentId);
      return await _apiService.delete(url);
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final leadsProvider = AsyncNotifierProvider<LeadsNotifier, LeadsListData>(
  LeadsNotifier.new,
);

/// Convenience providers. Read from the AsyncValue.
final leadsListProvider = Provider<List<Lead>>((ref) {
  return ref.watch(leadsProvider).value?.leads ?? const [];
});

final leadsLoadingProvider = Provider<bool>((ref) {
  return ref.watch(leadsProvider).isLoading;
});

final leadsErrorProvider = Provider<String?>((ref) {
  return ref.watch(leadsProvider).error?.toString();
});
