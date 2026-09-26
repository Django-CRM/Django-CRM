import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../services/api_service.dart';
import '../../services/record_export.dart';

/// "Export CSV", in the app bar of every record list.
///
/// [query] is read at the moment of the tap, from the same state the list was
/// fetched with, so the file holds the rows the screen is showing with every
/// filter applied, all pages of them. Offered to every member, as on the web:
/// the API decides what each caller's file contains.
///
/// Disabled while a download is in flight, so a second tap cannot start a
/// second one.
class ExportCsvButton extends StatefulWidget {
  const ExportCsvButton({
    super.key,
    required this.endpoint,
    required this.filePrefix,
    required this.query,
    this.api,
    this.saveFile,
  });

  final String endpoint;
  final String filePrefix;
  final Future<Map<String, Object?>> Function() query;

  /// Injectable for tests; the real ones are used otherwise.
  final ApiService? api;
  final Future<String?> Function(Uint8List bytes, String fileName)? saveFile;

  @override
  State<ExportCsvButton> createState() => _ExportCsvButtonState();
}

class _ExportCsvButtonState extends State<ExportCsvButton> {
  bool _busy = false;

  Future<void> _export() async {
    setState(() => _busy = true);
    RecordExportResult result;
    try {
      result = await exportRecordsCsv(
        endpoint: widget.endpoint,
        query: await widget.query(),
        filePrefix: widget.filePrefix,
        api: widget.api,
        saveFile: widget.saveFile,
      );
    } catch (_) {
      result = const RecordExportResult(
        RecordExportOutcome.failed,
        error: 'Could not export.',
      );
    }
    if (!mounted) return;
    setState(() => _busy = false);
    final message = switch (result.outcome) {
      RecordExportOutcome.saved => 'Saved ${result.fileName}',
      RecordExportOutcome.failed => result.error ?? 'Could not export.',
      RecordExportOutcome.cancelled => null,
    };
    if (message != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: 'Export CSV',
      onPressed: _busy ? null : _export,
      icon: _busy
          ? const SizedBox(
              height: 18,
              width: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(LucideIcons.download),
    );
  }
}
