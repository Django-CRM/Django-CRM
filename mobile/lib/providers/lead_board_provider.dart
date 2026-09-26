import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/lead_board.dart';
import '../services/api_service.dart';

export '../services/api_service.dart' show ApiResponse;

/// One pipeline's board, read in the two calls it takes: the pipeline list
/// (for the picker, and to resolve which pipeline is open) and that
/// pipeline's lanes.
class LeadBoardData {
  const LeadBoardData({
    this.pipelines = const [],
    this.active,
    this.lanes = const [],
  });

  final List<LeadPipelineSummary> pipelines;

  /// The pipeline on screen. Null only when the org has none.
  final LeadPipelineSummary? active;

  /// "No stage" first, then the stages in order.
  final List<LeadBoardLane> lanes;

  bool get hasNoPipelines => pipelines.isEmpty;

  /// The lanes a lead can be moved into: every stage, never "No stage".
  List<LeadBoardLane> get stages =>
      lanes.where((lane) => !lane.isUnstaged).toList();
}

/// The lead board's state and its one write.
///
/// A move refreshes on success and leaves the board alone on failure, so a
/// refused move never shows a card in a lane the server did not put it in.
/// Who may move which lead, and into which stage, is decided by the server;
/// the screen shows its answer.
class LeadBoardNotifier extends AsyncNotifier<LeadBoardData> {
  final ApiService _apiService = ApiService();

  /// Which pipeline the picker chose. Kept across refreshes; one that has
  /// since gone falls back to the first rather than to an error.
  String? _selectedId;

  @override
  Future<LeadBoardData> build() => _fetch();

  Future<LeadBoardData> _fetch() async {
    final listResponse = await _apiService.get(ApiConfig.leadPipelines);
    if (!listResponse.success || listResponse.data == null) {
      throw Exception(listResponse.message ?? 'Could not load the pipelines.');
    }
    final rows = listResponse.data!['pipelines'];
    final pipelines = rows is List
        ? rows
              .whereType<Map<String, dynamic>>()
              .map(LeadPipelineSummary.fromJson)
              .toList()
        : <LeadPipelineSummary>[];
    if (pipelines.isEmpty) return const LeadBoardData();

    final active = pipelines.firstWhere(
      (p) => p.id == _selectedId,
      orElse: () => pipelines.first,
    );
    _selectedId = active.id;

    final boardResponse = await _apiService.get(
      ApiConfig.leadsKanban,
      queryParams: {'pipeline_id': active.id},
    );
    if (!boardResponse.success || boardResponse.data == null) {
      throw Exception(boardResponse.message ?? 'Could not load that pipeline.');
    }
    return LeadBoardData(
      pipelines: pipelines,
      active: active,
      lanes: LeadBoardLane.fromKanbanJson(boardResponse.data!),
    );
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Open another pipeline. A no-op for the one already open.
  Future<void> select(String pipelineId) async {
    if (pipelineId == _selectedId) return;
    _selectedId = pipelineId;
    await refresh();
  }

  /// Move a lead into a stage. It lands at the end of that stage.
  Future<ApiResponse<Map<String, dynamic>>> moveLead({
    required String leadId,
    required String stageId,
  }) async {
    final response = await _apiService.patch(ApiConfig.leadMove(leadId), {
      'stage_id': stageId,
    });
    if (response.success) await refresh();
    return response;
  }
}

final leadBoardProvider =
    AsyncNotifierProvider<LeadBoardNotifier, LeadBoardData>(
      LeadBoardNotifier.new,
    );
