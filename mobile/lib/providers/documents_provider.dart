import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/api_config.dart';
import '../data/models/crm_document.dart';
import '../services/api_service.dart';

export '../services/api_service.dart' show ApiResponse;

/// The documents screen's data.
class DocumentsData {
  const DocumentsData({
    this.documents = const [],
    this.totals = const DocumentTotals(),
    this.includeArchived = false,
  });

  final List<CrmDocument> documents;
  final DocumentTotals totals;
  final bool includeArchived;
}

/// Whether the list is also showing archived documents. Off by default:
/// archiving is how somebody gets a document out of the way, so a list that
/// showed them back alongside the live ones would undo the only thing archiving
/// does. Matches `?archived=1` on the web.
class DocumentsArchivedNotifier extends Notifier<bool> {
  @override
  bool build() => false;

  void toggle() => state = !state;
}

final documentsArchivedProvider =
    NotifierProvider<DocumentsArchivedNotifier, bool>(
      DocumentsArchivedNotifier.new,
    );

/// Shared files.
///
/// `GET /api/documents/` answers two separately paginated envelopes,
/// `documents_active` and `documents_inactive`, a v1 artefact. Both are read
/// and merged into the one list this screen shows. A `status` filter narrows
/// the base queryset before the split, so asking for `active` leaves the
/// inactive envelope empty and the merge still returns the right rows.
class DocumentsNotifier extends AsyncNotifier<DocumentsData> {
  final ApiService _apiService = ApiService();

  @override
  Future<DocumentsData> build() {
    // Watched, not read: flipping the archived switch is what refetches.
    final includeArchived = ref.watch(documentsArchivedProvider);
    return _fetch(includeArchived);
  }

  Future<DocumentsData> _fetch(bool includeArchived) async {
    final query = includeArchived ? 'limit=1000' : 'limit=1000&status=active';
    final response = await _apiService.get('${ApiConfig.documents}?$query');
    if (!response.success || response.data == null) {
      throw Exception(response.message ?? 'Could not load the documents.');
    }

    final data = response.data!;
    final documents = [
      ..._envelope(data, 'documents_active'),
      ..._envelope(data, 'documents_inactive'),
    ].map(CrmDocument.fromJson).toList();

    documents.sort((a, b) {
      final at = a.createdAt, bt = b.createdAt;
      if (at == null && bt == null) return 0;
      if (at == null) return 1;
      if (bt == null) return -1;
      return bt.compareTo(at);
    });

    return DocumentsData(
      documents: documents,
      totals: documentTotals(documents),
      includeArchived: includeArchived,
    );
  }

  /// The rows sit at `documents_active.documents_active`, nested under a key
  /// with the same name as its own envelope.
  List<Map<String, dynamic>> _envelope(Map<String, dynamic> data, String key) {
    final block = data[key];
    if (block is! Map<String, dynamic>) return const [];
    final rows = block[key];
    if (rows is! List) return const [];
    return rows.whereType<Map<String, dynamic>>().toList();
  }

  Future<void> refresh() async {
    final includeArchived = ref.read(documentsArchivedProvider);
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(() => _fetch(includeArchived));
  }

  /// `POST /api/documents/` as multipart, because it carries a file.
  ///
  /// Open to any org member: `DocumentListView.post` checks authentication and
  /// org context and nothing more. A UI that claimed "admins only" would be
  /// inventing a rule the server does not enforce.
  ///
  /// `shared_to` and `teams` go as JSON-encoded arrays of PROFILE and TEAM ids.
  /// The view passes them through `payload_id_list`, which takes a JSON array,
  /// and re-resolves every id inside the caller's own org, so a share can never
  /// point at another tenant.
  Future<ApiResponse<Map<String, dynamic>>> uploadDocument({
    required String title,
    required String status,
    required String filePath,
    required String fileName,
    List<String> sharedTo = const [],
    List<String> teams = const [],
  }) async {
    try {
      final response = await _apiService.postMultipart(
        ApiConfig.documents,
        fileField: 'document_file',
        filePath: filePath,
        fileName: fileName,
        fields: {
          'title': title,
          'status': status,
          'shared_to': jsonEncode(sharedTo),
          'teams': jsonEncode(teams),
        },
      );
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }

  /// Save an edit. Multipart when a replacement file was picked, JSON
  /// otherwise, so a rename does not resend the bytes. Gated by `_may_write`
  /// (the uploader or an admin) server-side on both verbs.
  ///
  /// The verb follows whether sharing changed, because the two verbs disagree
  /// about it. `PUT` replaces `shared_to` and `teams` with the body's lists; a
  /// person must be active unless the document is already shared with them,
  /// so a share to somebody deactivated since survives only when [sharedTo]
  /// names it again. `PATCH` writes title, status and file and never touches
  /// either list. So pass [sharedTo] and [teams] (both, or neither, and in
  /// full) only when the person changed who can open the document; otherwise
  /// this PATCHes and every share survives.
  ///
  /// Replacing the file keeps the record and therefore keeps its shares.
  /// Deleting and re-uploading used to be the only way to correct a wrong
  /// file, and it silently dropped everyone it was shared with.
  Future<ApiResponse<Map<String, dynamic>>> updateDocument(
    String id, {
    required String title,
    required String status,
    List<String>? sharedTo,
    List<String>? teams,
    String? filePath,
    String? fileName,
  }) => _write(() {
    final sharing = sharedTo != null && teams != null;
    final method = sharing ? 'PUT' : 'PATCH';
    if (filePath == null) {
      final body = <String, dynamic>{
        'title': title,
        'status': status,
        if (sharing) 'shared_to': sharedTo,
        if (sharing) 'teams': teams,
      };
      return sharing
          ? _apiService.put(ApiConfig.document(id), body)
          : _apiService.patch(ApiConfig.document(id), body);
    }
    // Multipart carries every field as a string, so the two lists go as JSON
    // exactly the way the upload sends them. `payload_id_list` reads both
    // spellings and re-resolves each id inside the caller's own org.
    return _apiService.postMultipart(
      ApiConfig.document(id),
      method: method,
      fileField: 'document_file',
      filePath: filePath,
      fileName: fileName,
      fields: {
        'title': title,
        'status': status,
        if (sharing) 'shared_to': jsonEncode(sharedTo),
        if (sharing) 'teams': jsonEncode(teams),
      },
    );
  });

  /// `DELETE /api/documents/<id>/`. A hard delete, gated by `_may_delete`
  /// (the uploader or an admin). Archiving is the reversible option and lives
  /// on the edit form; this one destroys the row and the file.
  Future<ApiResponse<Map<String, dynamic>>> deleteDocument(String id) =>
      _write(() => _apiService.delete(ApiConfig.document(id)));

  Future<ApiResponse<Map<String, dynamic>>> _write(
    Future<ApiResponse<Map<String, dynamic>>> Function() send,
  ) async {
    try {
      final response = await send();
      if (response.success) await refresh();
      return response;
    } catch (e) {
      return ApiResponse(success: false, message: e.toString(), statusCode: 0);
    }
  }
}

final documentsProvider =
    AsyncNotifierProvider<DocumentsNotifier, DocumentsData>(
      DocumentsNotifier.new,
    );
