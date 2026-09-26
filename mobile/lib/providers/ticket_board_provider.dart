import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/ticket_board.dart';
import '../services/api_service.dart';

export '../services/api_service.dart' show ApiResponse;

/// The ticket board as read in two calls: the pipeline list (for the picker)
/// and the lanes, by status or for the chosen pipeline.
class TicketBoardData {
  const TicketBoardData({
    this.pipelines = const [],
    this.active,
    this.lanes = const [],
  });

  final List<TicketPipelineSummary> pipelines;

  /// The pipeline on screen, or null for the board by status.
  final TicketPipelineSummary? active;
  final List<TicketBoardLane> lanes;

  /// Where a card in [from] can go: every other lane.
  List<TicketBoardLane> destinationsFrom(TicketBoardLane from) =>
      lanes.where((lane) => lane.id != from.id).toList();
}

/// The ticket board's state and its one write.
///
/// A move refreshes on success and leaves the board alone on failure, so a
/// refused move never shows a card in a lane the server did not put it in.
/// Who may move which ticket, and where, is the server's decision (WIP limits,
/// the close gate, write access); the screen shows its answer.
class TicketBoardNotifier extends AsyncNotifier<TicketBoardData> {
  final ApiService _apiService = ApiService();

  /// The pipeline the picker chose, or null for the board by status. One
  /// that has since gone falls back to the board by status.
  String? _selectedId;

  @override
  Future<TicketBoardData> build() => _fetch();

  Future<TicketBoardData> _fetch() async {
    final listResponse = await _apiService.get(ApiConfig.casePipelines);
    if (!listResponse.success || listResponse.data == null) {
      throw Exception(listResponse.message ?? 'Could not load the pipelines.');
    }
    final rows = listResponse.data!['pipelines'];
    final pipelines = rows is List
        ? rows
              .whereType<Map<String, dynamic>>()
              .map(TicketPipelineSummary.fromJson)
              .toList()
        : <TicketPipelineSummary>[];

    TicketPipelineSummary? active;
    for (final p in pipelines) {
      if (p.id == _selectedId) active = p;
    }
    _selectedId = active?.id;

    final boardResponse = await _apiService.get(
      ApiConfig.casesKanban,
      queryParams: active == null ? null : {'pipeline_id': active.id},
    );
    if (!boardResponse.success || boardResponse.data == null) {
      throw Exception(boardResponse.message ?? 'Could not load the board.');
    }
    return TicketBoardData(
      pipelines: pipelines,
      active: active,
      lanes: TicketBoardLane.fromKanbanJson(boardResponse.data!),
    );
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  /// Open a pipeline, or the board by status for null. A no-op for the one
  /// already open.
  Future<void> select(String? pipelineId) async {
    if (pipelineId == _selectedId) return;
    _selectedId = pipelineId;
    await refresh();
  }

  /// Move a ticket into [target], where it lands at the end of the lane.
  ///
  /// A refusal comes back with the server's own sentence. `ApiService` labels
  /// the two 400 shapes this endpoint uses: `{"error": "<sentence>"}` (a full
  /// stage) reads "Error: ..." and the close gate's `{"errors": {"status":
  /// [...]}}` reads "Status: ...". The label is dropped so the snackbar says
  /// only the sentence. A 404 is replaced, since its own text is "Not found.".
  Future<ApiResponse<Map<String, dynamic>>> moveTicket({
    required String ticketId,
    required TicketBoardLane target,
  }) async {
    final response = await _apiService.patch(
      ApiConfig.ticketMove(ticketId),
      target.moveBody,
    );
    if (response.success) {
      await refresh();
      return response;
    }
    if (response.statusCode == 404) {
      return const ApiResponse(
        success: false,
        statusCode: 404,
        message: ticketMoveNotAllowedMessage,
      );
    }
    final message = response.message;
    if (message == null) return response;
    return ApiResponse(
      success: false,
      statusCode: response.statusCode,
      message: message.replaceFirst(RegExp(r'^(Error|Status): '), ''),
    );
  }
}

/// Shown when a move is answered 404.
const ticketMoveNotAllowedMessage =
    'You cannot move that ticket. You may not have access to it, or the '
    'ticket or stage no longer exists.';

final ticketBoardProvider =
    AsyncNotifierProvider<TicketBoardNotifier, TicketBoardData>(
      TicketBoardNotifier.new,
    );
