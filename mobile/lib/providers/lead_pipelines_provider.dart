import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/lead_pipeline.dart';
import '../services/api_service.dart';
import 'lead_board_provider.dart';

/// Lead pipeline administration: the list, one pipeline's stages, and every
/// write on them.
///
/// Reading is open to every member. Every write is admin-only server-side
/// (`is_org_admin` or a superuser, else 403), so the screens hiding their
/// controls from a member is UX, not what keeps a member out.
///
/// Nothing here is optimistic except a reorder, which reloads the pipeline
/// when refused. A write that succeeds also drops the board's cached lanes,
/// since a renamed, added, removed or reordered stage is a board lane.

/// Shown for a 403.
const leadPipelineAdminOnlyMessage = 'Only an admin can change lead pipelines.';

/// The server's own sentence for a refused write.
///
/// These views answer in two shapes: `{"error": "sentence"}` for a refusal
/// (a pipeline or stage that still holds leads, a partial reorder list) and
/// `{"error": true, "errors": {"field": ["sentence"]}}` for a validation
/// failure (a duplicate stage name). A DELETE keeps no body on failure, and
/// `ApiService` renders the first shape as "Error: sentence", so that prefix
/// is dropped rather than shown. A 403 or 404 gets a fixed sentence, because
/// the server's ("Permission denied", "Not found.") says nothing to act on.
String leadPipelineMessage(ApiResponse<Map<String, dynamic>> response) {
  if (response.statusCode == 403) return leadPipelineAdminOnlyMessage;
  if (response.statusCode == 404) {
    return 'That pipeline or stage no longer exists. Refresh to see the '
        'current list.';
  }
  final data = response.data;
  final errors = data?['errors'];
  if (errors is Map) {
    for (final value in errors.values) {
      if (value is List && value.isNotEmpty) return value.first.toString();
      if (value is String && value.trim().isNotEmpty) return value;
    }
  }
  if (errors is String && errors.trim().isNotEmpty) return errors;
  final error = data?['error'];
  if (error is String && error.trim().isNotEmpty) return error;
  final raw = response.message?.trim() ?? '';
  if (raw.startsWith('Error: ')) return raw.substring('Error: '.length);
  return raw.isEmpty ? 'Something went wrong.' : raw;
}

/// The org's active lead pipelines, with their counts.
class LeadPipelinesNotifier extends AsyncNotifier<List<LeadPipeline>> {
  final ApiService _api = ApiService();

  @override
  Future<List<LeadPipeline>> build() => _fetch();

  Future<List<LeadPipeline>> _fetch() async {
    final response = await _api.get(ApiConfig.leadPipelines);
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load the pipelines.');
    }
    final rows = response.data!['pipelines'];
    return rows is List
        ? rows
              .whereType<Map<String, dynamic>>()
              .map(LeadPipeline.fromJson)
              .toList()
        : const [];
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Returns null on success, else the sentence to show.
  Future<String?> createPipeline(Map<String, dynamic> payload) =>
      _write(() => _api.post(ApiConfig.leadPipelines, payload));

  Future<String?> updatePipeline(String id, Map<String, dynamic> payload) =>
      _write(() => _api.put(ApiConfig.leadPipeline(id), payload));

  Future<String?> deletePipeline(String id) =>
      _write(() => _api.delete(ApiConfig.leadPipeline(id)));

  Future<String?> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() send,
  ) async {
    final response = await send();
    if (!response.success) return leadPipelineMessage(response);
    ref.invalidate(leadBoardProvider);
    await refresh();
    return null;
  }
}

final leadPipelinesProvider =
    AsyncNotifierProvider<LeadPipelinesNotifier, List<LeadPipeline>>(
      LeadPipelinesNotifier.new,
    );

/// One pipeline and its stages, and the stage writes.
class LeadPipelineDetailNotifier extends AsyncNotifier<LeadPipelineDetail> {
  LeadPipelineDetailNotifier(this.pipelineId);

  final String pipelineId;
  final ApiService _api = ApiService();

  @override
  Future<LeadPipelineDetail> build() => _fetch();

  Future<LeadPipelineDetail> _fetch() async {
    final response = await _api.get(ApiConfig.leadPipeline(pipelineId));
    if (response.statusCode == 404) {
      throw Exception('This pipeline no longer exists.');
    }
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load the pipeline.');
    }
    return LeadPipelineDetail.fromJson(response.data!);
  }

  /// Reload without passing through a loading state, so the stages stay on
  /// screen while a write's follow-up read is in flight.
  Future<void> refresh() async {
    state = await AsyncValue.guard(_fetch);
  }

  Future<String?> createStage(Map<String, dynamic> payload) => _write(
    () => _api.post(ApiConfig.leadPipelineStages(pipelineId), payload),
  );

  Future<String?> updateStage(String stageId, Map<String, dynamic> payload) =>
      _write(() => _api.put(ApiConfig.leadStage(stageId), payload));

  Future<String?> deleteStage(String stageId) =>
      _write(() => _api.delete(ApiConfig.leadStage(stageId)));

  /// Move the stage at [from] to [to], both positions in the current list.
  ///
  /// The server takes the whole order or nothing: every stage id of the
  /// pipeline, each once. The list is shown in its new order at once and
  /// reloaded from the server if the move is refused, so a refused move never
  /// leaves the screen claiming an order the board does not have.
  Future<String?> moveStage(int from, int to) async {
    final current = state.value;
    if (current == null) return null;
    final stages = [...current.stages];
    if (from < 0 || from >= stages.length) return null;
    if (to < 0 || to >= stages.length || to == from) return null;
    stages.insert(to, stages.removeAt(from));
    state = AsyncData(current.withStages(stages));

    final response = await _api.post(
      ApiConfig.leadPipelineStagesReorder(pipelineId),
      {'stage_ids': stages.map((s) => s.id).toList()},
    );
    if (!response.success) {
      await refresh();
      return leadPipelineMessage(response);
    }
    ref.invalidate(leadBoardProvider);
    return null;
  }

  Future<String?> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() send,
  ) async {
    final response = await send();
    if (!response.success) return leadPipelineMessage(response);
    ref.invalidate(leadBoardProvider);
    ref.invalidate(leadPipelinesProvider);
    await refresh();
    return null;
  }
}

final leadPipelineDetailProvider = AsyncNotifierProvider.autoDispose
    .family<LeadPipelineDetailNotifier, LeadPipelineDetail, String>(
      LeadPipelineDetailNotifier.new,
    );
