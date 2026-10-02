import 'package:file_picker/file_picker.dart';
import 'dart:typed_data';

import '../config/api_config.dart';
import 'api_service.dart';

/// The one place the app turns a file on the phone into an attachment.
///
/// Four detail screens carry a Files section and every one of them could
/// already delete an attachment it had no way to create. The endpoints are the
/// same detail POST that takes a comment, each with its own field name, so the
/// only thing that varies between them is that name and the URL.
enum AttachmentTarget {
  task('task_attachment'),
  lead('lead_attachment'),
  deal('opportunity_attachment'),
  ticket('case_attachment');

  const AttachmentTarget(this.field);

  /// The multipart field the matching Django view reads. Getting this wrong is
  /// silent: `request.FILES.get()` returns None, the view answers 200, and
  /// nothing is stored.
  final String field;

  String urlFor(String recordId) {
    switch (this) {
      case AttachmentTarget.task:
        return '${ApiConfig.tasks}$recordId/';
      case AttachmentTarget.lead:
        return '${ApiConfig.leads}$recordId/';
      case AttachmentTarget.deal:
        return '${ApiConfig.opportunities}$recordId/';
      case AttachmentTarget.ticket:
        return '${ApiConfig.tickets}$recordId/';
    }
  }
}

/// Mirrors `common.utils.ATTACHMENT_MAX_BYTES`.
///
/// A UX hint, not a rule. The server enforces it, which is the only place that
/// counts, and this exists so somebody who picked a 300 MB video is told
/// immediately instead of after three minutes of uploading.
const int attachmentMaxBytes = 25 * 1024 * 1024;

class AttachmentSelectionResult {
  const AttachmentSelectionResult._({
    this.file,
    this.error,
    this.cancelled = false,
  });

  const AttachmentSelectionResult.selected(PlatformFile file)
    : this._(file: file);

  const AttachmentSelectionResult.failed(String error) : this._(error: error);

  const AttachmentSelectionResult.cancelled() : this._(cancelled: true);

  final PlatformFile? file;
  final String? error;
  final bool cancelled;
}

/// Pick and validate one attachment for a form that uploads it later.
Future<AttachmentSelectionResult> selectAttachment({
  Future<PlatformFile?> Function()? pickFile,
}) async {
  final picked = await (pickFile ?? _pickOneFile)();
  if (picked == null) return const AttachmentSelectionResult.cancelled();
  if ((picked.path ?? '').isEmpty) {
    return const AttachmentSelectionResult.failed(
      'That file could not be read. Try picking it again.',
    );
  }
  final size = await picked.length();
  if (size == null) {
    return const AttachmentSelectionResult.failed(
      'That file could not be read. Try picking it again.',
    );
  }
  if (size > attachmentMaxBytes) {
    final limit = attachmentMaxBytes ~/ (1024 * 1024);
    return AttachmentSelectionResult.failed(
      '${picked.name} is ${_megabytes(size)} MB. '
      'Files must be $limit MB or smaller.',
    );
  }
  return AttachmentSelectionResult.selected(picked);
}

class AttachmentDownloadResult {
  const AttachmentDownloadResult({required this.success, this.error});

  final bool success;
  final String? error;
}

/// Download a private attachment with the API authorization header, then ask
/// the operating system where to save it. No token is placed in the URL.
Future<AttachmentDownloadResult> downloadAttachment({
  required String url,
  required String fileName,
  ApiService? api,
  Future<String?> Function(Uint8List bytes, String fileName)? saveFile,
}) async {
  final response = await (api ?? ApiService()).getBytes(url);
  if (!response.success || response.data == null) {
    return AttachmentDownloadResult(
      success: false,
      error: response.message ?? 'Could not download that file.',
    );
  }
  try {
    await (saveFile ?? _saveFile)(response.data!, fileName);
    return const AttachmentDownloadResult(success: true);
  } catch (_) {
    return const AttachmentDownloadResult(
      success: false,
      error: 'Could not save that file.',
    );
  }
}

Future<String?> _saveFile(Uint8List bytes, String fileName) async =>
    (await FilePicker.saveFile(
      dialogTitle: 'Save attachment',
      fileName: fileName,
      bytes: bytes,
    ))?.toString();

/// Remove the temporary copy made by the native picker after a form uploads it.
Future<void> clearAttachmentPickerCache() => _clearPickerCache();

/// What an upload attempt produced. `cancelled` is not a failure and should not
/// raise a message: the user closed the picker.
class AttachmentUploadResult {
  const AttachmentUploadResult._({
    required this.cancelled,
    this.error,
    this.data,
  });

  const AttachmentUploadResult.cancelled()
    : this._(cancelled: true, error: null, data: null);

  const AttachmentUploadResult.failed(String message)
    : this._(cancelled: false, error: message, data: null);

  const AttachmentUploadResult.uploaded(Map<String, dynamic>? body)
    : this._(cancelled: false, error: null, data: body);

  final bool cancelled;
  final String? error;

  /// The detail POST answers with the refreshed `attachments` and `comments`
  /// arrays, so a caller can update in place instead of refetching the page.
  final Map<String, dynamic>? data;

  bool get succeeded => !cancelled && error == null;
}

/// Pick a file and attach it to [recordId].
///
/// [pickFile] and [api] are injectable so the size rule and the error paths can
/// be tested without a file system or a server. Nothing else passes them.
Future<AttachmentUploadResult> pickAndUploadAttachment({
  required AttachmentTarget target,
  required String recordId,
  Future<PlatformFile?> Function()? pickFile,
  ApiService? api,
}) async {
  final picked = await (pickFile ?? _pickOneFile)();
  if (picked == null) return const AttachmentUploadResult.cancelled();

  final path = picked.path;
  if (path == null || path.isEmpty) {
    return const AttachmentUploadResult.failed(
      'That file could not be read. Try picking it again.',
    );
  }

  final size = await picked.length();
  if (size == null) {
    if (pickFile == null) await _clearPickerCache();
    return const AttachmentUploadResult.failed(
      'That file could not be read. Try picking it again.',
    );
  }

  if (size > attachmentMaxBytes) {
    // Clear it here too. This is the case where the copy matters most: the
    // file that was too big to send is the one it hurts to keep.
    if (pickFile == null) await _clearPickerCache();
    final limit = attachmentMaxBytes ~/ (1024 * 1024);
    return AttachmentUploadResult.failed(
      '${picked.name} is ${_megabytes(size)} MB. '
      'Files must be $limit MB or smaller.',
    );
  }

  final response = await (api ?? ApiService()).postMultipart(
    target.urlFor(recordId),
    fileField: target.field,
    filePath: path,
    fileName: picked.name,
  );

  if (pickFile == null) await _clearPickerCache();

  if (!response.success) {
    return AttachmentUploadResult.failed(
      response.message ?? 'Could not upload that file.',
    );
  }
  return AttachmentUploadResult.uploaded(response.data);
}

Future<PlatformFile?> _pickOneFile() => FilePicker.pickFile();

/// Drop the picker's cached copy of what was just uploaded.
///
/// `pickFile` copies the chosen file into the app's cache directory, so
/// without this every attachment a person sends stays on their phone a second
/// time, in a directory they never see. Only when the real picker ran: an
/// injected one has nothing to clear, and the platform channel is not there in
/// a test. Failure is ignored, since the OS clears that cache eventually and
/// nothing here depends on it.
Future<void> _clearPickerCache() async {
  try {
    await FilePicker.clearTemporaryFiles();
  } catch (_) {
    // Best effort.
  }
}

String _megabytes(int bytes) => (bytes / (1024 * 1024)).toStringAsFixed(1);
