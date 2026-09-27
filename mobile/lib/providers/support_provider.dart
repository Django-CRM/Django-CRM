import 'package:file_picker/file_picker.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/support_ticket.dart';
import '../services/api_service.dart';
import '../services/attachment_upload.dart';

class SupportTicketListData {
  const SupportTicketListData({this.tickets = const [], this.count = 0});

  final List<SupportTicket> tickets;
  final int count;
}

/// Thrown when this deployment serves no BottleCRM support queue.
///
/// `/api/support/` comes from the enterprise `platform_support` app, so a
/// community backend answers 404. That is a deployment fact, not a failure,
/// and the list screen renders the self-serve half of Help instead of an
/// error. Every other status stays an ordinary error, so a real outage on a
/// deployment that does have support is not disguised as this.
class SupportUnavailable implements Exception {
  const SupportUnavailable();

  @override
  String toString() => 'SupportUnavailable';
}

class SupportMutationResult {
  const SupportMutationResult({this.ticket, this.error});

  final SupportTicket? ticket;
  final String? error;
  bool get success => ticket != null;
}

class SupportRepository {
  SupportRepository({ApiService? api}) : _api = api ?? ApiService();

  final ApiService _api;

  Future<ApiResponse<SupportTicketListData>> list() async {
    final response = await _api.get('${ApiConfig.supportTickets}?limit=100');
    if (!response.success || response.data == null) {
      return ApiResponse(
        success: false,
        message: response.message ?? 'Could not load support tickets.',
        statusCode: response.statusCode,
      );
    }
    final body = response.data!;
    final rows = (body['tickets'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(SupportTicket.fromJson)
        .toList();
    return ApiResponse(
      success: true,
      data: SupportTicketListData(
        tickets: rows,
        count: body['count'] as int? ?? rows.length,
      ),
      statusCode: response.statusCode,
    );
  }

  Future<SupportMutationResult> detail(String id) async {
    final response = await _api.get(ApiConfig.supportTicket(id));
    return _ticketResult(response);
  }

  Future<SupportMutationResult> create({
    required String subject,
    required SupportCategory category,
    required String body,
    PlatformFile? attachment,
  }) async {
    final invalid = await _attachmentError(attachment);
    if (invalid != null) return SupportMutationResult(error: invalid);
    final response = attachment == null
        ? await _api.post(ApiConfig.supportTickets, {
            'subject': subject.trim(),
            'category': category.value,
            'body': body.trim(),
          })
        : await _api.postMultipart(
            ApiConfig.supportTickets,
            fileField: 'attachment',
            filePath: attachment.path!,
            fileName: attachment.name,
            fields: {
              'subject': subject.trim(),
              'category': category.value,
              'body': body.trim(),
            },
          );
    return _ticketResult(response);
  }

  Future<SupportMutationResult> reply({
    required String ticketId,
    required String body,
    PlatformFile? attachment,
  }) async {
    final invalid = await _attachmentError(attachment);
    if (invalid != null) return SupportMutationResult(error: invalid);
    final response = attachment == null
        ? await _api.post(ApiConfig.supportTicketReplies(ticketId), {
            'body': body.trim(),
          })
        : await _api.postMultipart(
            ApiConfig.supportTicketReplies(ticketId),
            fileField: 'attachment',
            filePath: attachment.path!,
            fileName: attachment.name,
            fields: {'body': body.trim()},
          );
    return _ticketResult(response);
  }

  Future<String?> _attachmentError(PlatformFile? file) async {
    if (file == null) return null;
    final size = await file.length();
    if ((file.path ?? '').isEmpty || size == null) {
      return 'That file could not be read. Try picking it again.';
    }
    if (size > attachmentMaxBytes) {
      return 'Files must be 25 MB or smaller.';
    }
    return null;
  }

  SupportMutationResult _ticketResult(
    ApiResponse<Map<String, dynamic>> response,
  ) {
    if (!response.success || response.data == null) {
      return SupportMutationResult(
        error: response.message ?? 'The support request failed.',
      );
    }
    return SupportMutationResult(
      ticket: SupportTicket.fromJson(response.data!),
    );
  }
}

class SupportTicketsNotifier extends AsyncNotifier<SupportTicketListData> {
  final SupportRepository _repository = SupportRepository();

  @override
  Future<SupportTicketListData> build() => _fetch();

  Future<SupportTicketListData> _fetch() async {
    final response = await _repository.list();
    if (!response.success || response.data == null) {
      if (response.statusCode == 404) throw const SupportUnavailable();
      throw Exception(response.message ?? 'Could not load support tickets.');
    }
    return response.data!;
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(_fetch);
  }

  Future<SupportMutationResult> getTicket(String id) => _repository.detail(id);

  Future<SupportMutationResult> create({
    required String subject,
    required SupportCategory category,
    required String body,
    PlatformFile? attachment,
  }) async {
    final result = await _repository.create(
      subject: subject,
      category: category,
      body: body,
      attachment: attachment,
    );
    if (result.success) await refresh();
    return result;
  }

  Future<SupportMutationResult> reply({
    required String ticketId,
    required String body,
    PlatformFile? attachment,
  }) async {
    final result = await _repository.reply(
      ticketId: ticketId,
      body: body,
      attachment: attachment,
    );
    if (result.success) {
      final current = state.value;
      if (current != null) {
        state = AsyncValue.data(
          SupportTicketListData(
            count: current.count,
            tickets: current.tickets
                .map(
                  (ticket) => ticket.id == ticketId ? result.ticket! : ticket,
                )
                .toList(),
          ),
        );
      }
    }
    return result;
  }
}

final supportTicketsProvider =
    AsyncNotifierProvider<SupportTicketsNotifier, SupportTicketListData>(
      SupportTicketsNotifier.new,
    );
