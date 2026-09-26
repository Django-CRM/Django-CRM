import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/deal_pipeline.dart';
import '../services/api_service.dart';
import 'deals_provider.dart';

/// The org's deal pipelines with their stages, and every write on them.
///
/// Read by the deals board (its columns), the deal form (its pickers), the
/// deal detail (its stepper) and the settings screens. One request serves all
/// of them, because the list endpoint carries every stage.
///
/// Reading is open to every member. Every write is admin-only server-side
/// (403 otherwise), so the settings screens hiding their controls from a
/// member is UX, not what keeps a member out.

/// The sentence to show for a refused write.
///
/// The views answer `{"error": true, "errors": "sentence"}` for a refusal (a
/// stage that still holds deals, the default pipeline) and
/// `{"error": true, "errors": {"field": ["sentence"]}}` for a validation
/// failure. POST keeps that body on failure; PATCH and DELETE keep only the
/// sentence `ApiService` made of it, which is used as it stands. A 403 already
/// carries the server's own "Only admins can change deal pipelines.".
String dealPipelineMessage(ApiResponse<Map<String, dynamic>> response) {
  if (response.statusCode == 404) {
    return 'That pipeline or stage no longer exists. Refresh to see the '
        'current list.';
  }
  final errors = response.data?['errors'];
  if (errors is String && errors.trim().isNotEmpty) return errors;
  if (errors is Map) {
    for (final value in errors.values) {
      if (value is List && value.isNotEmpty) return value.first.toString();
      if (value is String && value.trim().isNotEmpty) return value;
    }
  }
  final raw = response.message?.trim() ?? '';
  return raw.isEmpty ? 'Something went wrong.' : raw;
}

class DealPipelinesNotifier extends AsyncNotifier<List<DealPipeline>> {
  final ApiService _api = ApiService();

  @override
  Future<List<DealPipeline>> build() => _fetch();

  Future<List<DealPipeline>> _fetch() async {
    final response = await _api.get(ApiConfig.dealPipelines);
    // A server that predates configurable pipelines has no such endpoint and
    // still stores the six seeded codes, so the app keeps working against it.
    if (response.statusCode == 404) return [DealPipeline.legacy];
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load the pipelines.');
    }
    final rows = response.data!['pipelines'];
    return rows is List
        ? rows
              .whereType<Map<String, dynamic>>()
              .map(DealPipeline.fromJson)
              .toList()
        : const [];
  }

  /// Reload without passing through a loading state, so what is on screen
  /// stays there while a write's follow-up read is in flight.
  Future<void> refresh() async {
    state = await AsyncValue.guard(_fetch);
  }

  /// Each returns null on success, else the sentence to show.
  Future<String?> createPipeline(String name) =>
      _write(() => _api.post(ApiConfig.dealPipelines, {'name': name.trim()}));

  Future<String?> renamePipeline(String id, String name) => _write(
    () => _api.patch(ApiConfig.dealPipeline(id), {'name': name.trim()}),
  );

  Future<String?> deletePipeline(String id) =>
      _write(() => _api.delete(ApiConfig.dealPipeline(id)));

  Future<String?> createStage(
    String pipelineId,
    Map<String, dynamic> payload,
  ) => _write(
    () => _api.post(ApiConfig.dealPipelineStages(pipelineId), payload),
  );

  Future<String?> updateStage(String stageId, Map<String, dynamic> payload) =>
      _write(() => _api.patch(ApiConfig.dealStage(stageId), payload));

  Future<String?> deleteStage(String stageId) =>
      _write(() => _api.delete(ApiConfig.dealStage(stageId)));

  /// Move the stage at [from] to [to] within pipeline [pipelineId].
  ///
  /// The server takes the whole order or nothing: every stage id of the
  /// pipeline, each once. The new order shows at once and is reloaded from
  /// the server if refused, so the screen never claims an order the board
  /// does not have.
  Future<String?> moveStage(String pipelineId, int from, int to) async {
    final current = state.value;
    if (current == null) return null;
    final index = current.indexWhere((p) => p.id == pipelineId);
    if (index < 0) return null;
    final stages = [...current[index].stages];
    if (from < 0 || from >= stages.length) return null;
    if (to < 0 || to >= stages.length || to == from) return null;
    stages.insert(to, stages.removeAt(from));
    state = AsyncData([
      for (final (i, p) in current.indexed)
        i == index ? p.withStages(stages) : p,
    ]);

    final response = await _api.post(
      ApiConfig.dealPipelineStagesReorder(pipelineId),
      {'stage_ids': stages.map((s) => s.id).toList()},
    );
    if (!response.success) {
      await refresh();
      return dealPipelineMessage(response);
    }
    ref.invalidate(dealsProvider);
    return null;
  }

  Future<String?> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() send,
  ) async {
    final response = await send();
    if (!response.success) return dealPipelineMessage(response);
    // Stage labels and kinds ride on every deal row, and the board's columns
    // come from here, so the deals list is reloaded against the new shape.
    ref.invalidate(dealsProvider);
    await refresh();
    return null;
  }
}

final dealPipelinesProvider =
    AsyncNotifierProvider<DealPipelinesNotifier, List<DealPipeline>>(
      DealPipelinesNotifier.new,
    );
