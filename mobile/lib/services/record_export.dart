import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

import 'api_service.dart';

/// How an export ended. [cancelled] is not a failure: the person closed the
/// save dialog, and nothing should be said about it.
enum RecordExportOutcome { saved, cancelled, failed }

class RecordExportResult {
  const RecordExportResult(this.outcome, {this.fileName, this.error});

  final RecordExportOutcome outcome;
  final String? fileName;
  final String? error;
}

/// `<prefix>-<YYYY-MM-DD>.csv`, the name the API gives the web download.
///
/// The day is the device's. The API names its file by the org's day, and a
/// phone in the org's timezone agrees with it; `getBytes` does not surface the
/// response headers, so the name cannot be read off the reply.
String exportFileName(String prefix, DateTime day) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '$prefix-${day.year}-${two(day.month)}-${two(day.day)}.csv';
}

/// [endpoint] with [query] as its query string. A `List` value becomes a
/// repeated parameter (`status=New&status=Pending`), which is how the list
/// endpoints take more than one of a thing. Empty values are left out.
String exportUrl(String endpoint, Map<String, Object?> query) {
  final params = <String, dynamic>{};
  query.forEach((key, value) {
    if (value is Iterable) {
      final values = value.map((v) => '$v').where((v) => v.isNotEmpty).toList();
      if (values.isNotEmpty) params[key] = values;
    } else if (value != null && '$value'.isNotEmpty) {
      params[key] = '$value';
    }
  });
  final uri = Uri.parse(endpoint);
  return (params.isEmpty ? uri : uri.replace(queryParameters: params))
      .toString();
}

/// Download a list as CSV with the list's own filters, then ask the operating
/// system where to keep it.
///
/// The API decides which rows the caller may have, exactly as it does for the
/// list: this sends the same query and trusts nothing it holds locally.
/// [api], [saveFile] and [today] are injectable for tests.
Future<RecordExportResult> exportRecordsCsv({
  required String endpoint,
  required Map<String, Object?> query,
  required String filePrefix,
  ApiService? api,
  Future<String?> Function(Uint8List bytes, String fileName)? saveFile,
  DateTime? today,
}) async {
  final response = await (api ?? ApiService()).getBytes(
    exportUrl(endpoint, query),
  );
  if (!response.success || response.data == null) {
    return RecordExportResult(
      RecordExportOutcome.failed,
      error: response.message ?? 'Could not export.',
    );
  }
  final name = exportFileName(filePrefix, today ?? DateTime.now());
  try {
    final saved = await (saveFile ?? _saveWithPicker)(response.data!, name);
    return saved == null
        ? const RecordExportResult(RecordExportOutcome.cancelled)
        : RecordExportResult(RecordExportOutcome.saved, fileName: name);
  } catch (_) {
    return const RecordExportResult(
      RecordExportOutcome.failed,
      error: 'Could not save the file.',
    );
  }
}

Future<String?> _saveWithPicker(Uint8List bytes, String fileName) =>
    FilePicker.saveFile(
      dialogTitle: 'Save CSV export',
      fileName: fileName,
      bytes: bytes,
    );
