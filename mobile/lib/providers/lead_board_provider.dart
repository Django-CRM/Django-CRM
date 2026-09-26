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

  /// The stages of the pipeline, without "No stage".
  List<LeadBoardLane> get stages =>
      lanes.where((lane) => !lane.isUnstaged).toList();

  /// Where a card in [from] can go: every other lane. That includes "No
  /// stage" for a card in a stage, and never the lane the card is already in.
  List<LeadBoardLane> destinationsFrom(LeadBoardLane from) =>
      lanes.where((lane) => lane.id != from.id).toList();
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

  /// Move a lead into a stage, where it lands at the end. A null [stageId]
  /// takes it out of its pipeline, back to "No stage"; the key is sent with
  /// an explicit null because a body without it is refused.
  ///
  /// A 404 is the server saying the caller may not edit this lead, worded as
  /// if it did not exist (it also answers 404 for a lead or stage that is
  /// gone). Its own text is "Not found.", so it is replaced with a sentence
  /// that says what happened.
  Future<ApiResponse<Map<String, dynamic>>> moveLead({
    required String leadId,
    required String? stageId,
  }) async {
    final response = await _apiService.patch(ApiConfig.leadMove(leadId), {
      'stage_id': stageId,
    });
    if (response.success) {
      await refresh();
    } else if (response.statusCode == 404) {
      return const ApiResponse(
        success: false,
        statusCode: 404,
        message: leadMoveNotAllowedMessage,
      );
    }
    return response;
  }
}

/// Shown when a move is answered 404.
const leadMoveNotAllowedMessage =
    'You cannot move that lead. You may not have access to it, or the lead or '
    'stage no longer exists.';

final leadBoardProvider =
    AsyncNotifierProvider<LeadBoardNotifier, LeadBoardData>(
      LeadBoardNotifier.new,
    );
