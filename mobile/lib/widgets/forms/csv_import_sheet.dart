import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../providers/csv_import_provider.dart';
import '../../services/attachment_upload.dart' show clearAttachmentPickerCache;

/// Open the CSV import sheet for [target].
///
/// On a successful import the sheet closes, a snackbar reports how many
/// records were created, and [onImported] runs so the list behind it
/// refreshes. [pickFile] and [saveTemplate] replace the platform picker in
/// tests; nothing else passes them.
Future<void> showCsvImportSheet(
  BuildContext context,
  CsvImportTarget target, {
  required VoidCallback onImported,
  Future<PlatformFile?> Function()? pickFile,
  Future<String?> Function(String fileName, List<int> bytes)? saveTemplate,
}) async {
  final created = await showModalBottomSheet<int>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (_) => CsvImportSheet(
      target: target,
      pickFile: pickFile,
      saveTemplate: saveTemplate,
    ),
  );
  if (created == null || !context.mounted) return;
  onImported();
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text('Imported $created ${target.noun(created)}')),
  );
}

class CsvImportSheet extends ConsumerStatefulWidget {
  const CsvImportSheet({
    super.key,
    required this.target,
    this.pickFile,
    this.saveTemplate,
  });

  final CsvImportTarget target;
  final Future<PlatformFile?> Function()? pickFile;
  final Future<String?> Function(String fileName, List<int> bytes)?
  saveTemplate;

  @override
  ConsumerState<CsvImportSheet> createState() => _CsvImportSheetState();
}

class _CsvImportSheetState extends ConsumerState<CsvImportSheet> {
  String? _templateNote;

  CsvImportTarget get _target => widget.target;
  CsvImportNotifier get _notifier =>
      ref.read(csvImportProvider(_target).notifier);

  Future<void> _choose() async {
    final injected = widget.pickFile;
    final picked = await (injected ?? _pickOne)();
    if (mounted) await _notifier.choose(picked);
    // The bytes are held in memory from here on, so the picker's cached copy
    // is not needed for the commit.
    if (injected == null && picked != null) await clearAttachmentPickerCache();
  }

  static Future<PlatformFile?> _pickOne() async {
    // Any type, then refused by name: a CSV is labelled text/plain by some
    // Android file managers, and filtering on MIME type would hide it.
    final result = await FilePicker.pickFiles();
    if (result == null || result.files.isEmpty) return null;
    return result.files.first;
  }

  Future<void> _saveTemplate() async {
    final bytes = utf8.encode(_target.templateCsv);
    String? note;
    try {
      final saved = await (widget.saveTemplate ?? _saveWithPicker)(
        _target.templateFileName,
        bytes,
      );
      note = saved == null ? null : 'Template saved.';
    } catch (_) {
      note = 'Could not save the template.';
    }
    if (mounted) setState(() => _templateNote = note);
  }

  static Future<String?> _saveWithPicker(String fileName, List<int> bytes) =>
      FilePicker.saveFile(
        dialogTitle: 'Save CSV template',
        fileName: fileName,
        bytes: Uint8List.fromList(bytes),
      );

  Future<void> _commit() async {
    final ok = await _notifier.commit();
    if (!ok || !mounted) return;
    Navigator.of(
      context,
    ).pop(ref.read(csvImportProvider(_target)).created ?? 0);
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(csvImportProvider(_target));
    final preview = state.preview;

    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.9,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 4, 0),
            child: Row(
              children: [
                const Icon(
                  LucideIcons.upload,
                  size: 18,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Import ${_target.plural} from CSV',
                    style: AppTypography.h3.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Close',
                  icon: const Icon(LucideIcons.x, size: 20),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
          ),
          Flexible(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    _target.description,
                    style: AppTypography.bodySmall.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                  const SizedBox(height: 12),
                  if (state.file != null) ...[
                    _FileLine(file: state.file!),
                    const SizedBox(height: 12),
                  ],
                  if (state.busy) ...[
                    const LinearProgressIndicator(minHeight: 2),
                    const SizedBox(height: 12),
                  ],
                  if (preview == null)
                    _FormatHelp(
                      target: _target,
                      onSaveTemplate: _saveTemplate,
                      note: _templateNote,
                    )
                  else
                    _PreviewSummary(target: _target, preview: preview),
                  if (state.commitErrors.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    _RowErrors(
                      heading:
                          'Server rejected ${state.commitErrors.length} '
                          'row${state.commitErrors.length == 1 ? '' : 's'} '
                          'during import. The file may have changed since '
                          'preview',
                      errors: state.commitErrors,
                    ),
                  ],
                  if (state.error != null) ...[
                    const SizedBox(height: 12),
                    _Alert(message: state.error!),
                  ],
                ],
              ),
            ),
          ),
          Container(
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: AppColors.border)),
            ),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: state.busy
                        ? null
                        : preview == null
                        ? () => Navigator.of(context).pop()
                        : _notifier.reset,
                    child: Text(preview == null ? 'Cancel' : 'Back'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: preview == null
                      ? FilledButton.icon(
                          style: _filled,
                          onPressed: state.busy ? null : _choose,
                          icon: const Icon(LucideIcons.fileText, size: 18),
                          label: const Text('Choose CSV file'),
                        )
                      : FilledButton(
                          style: _filled,
                          onPressed: state.canCommit ? _commit : null,
                          child: Text(
                            'Import ${preview.valid} '
                            '${_target.noun(preview.valid)}',
                            textAlign: TextAlign.center,
                          ),
                        ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static final ButtonStyle _filled = FilledButton.styleFrom(
    minimumSize: const Size.fromHeight(AppLayout.buttonHeightLarge),
    shape: RoundedRectangleBorder(borderRadius: AppLayout.borderRadiusMd),
  );
}

class _FileLine extends StatelessWidget {
  const _FileLine({required this.file});

  final CsvImportFile file;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Icon(
          LucideIcons.fileText,
          size: 18,
          color: AppColors.textSecondary,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            file.name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
          ),
        ),
        const SizedBox(width: 8),
        Text(
          '${(file.bytes.length / 1024).toStringAsFixed(1)} KB',
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      ],
    );
  }
}

/// The web drawer's "CSV format" box: what the columns are, and the template.
class _FormatHelp extends StatelessWidget {
  const _FormatHelp({
    required this.target,
    required this.onSaveTemplate,
    required this.note,
  });

  final CsvImportTarget target;
  final VoidCallback onSaveTemplate;
  final String? note;

  @override
  Widget build(BuildContext context) {
    final secondary = AppTypography.bodySmall.copyWith(
      color: AppColors.textSecondary,
    );
    final required = target.requiredHeaders.join(', ');
    final requiredNote = target.requiredNote;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surfaceDim,
        border: Border.all(color: AppColors.border),
        borderRadius: AppLayout.borderRadiusMd,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'CSV format',
            style: AppTypography.label.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            'Required headers: $required'
            '${requiredNote == null ? '.' : '; $requiredNote'} '
            'Optional: ${target.optionalHelp}',
            style: secondary,
          ),
          Text('.csv only, up to 5 MB.', style: secondary),
          if (target.extraHelp != null) ...[
            const SizedBox(height: 4),
            Text(target.extraHelp!, style: secondary),
          ],
          const SizedBox(height: 4),
          TextButton.icon(
            style: TextButton.styleFrom(
              minimumSize: const Size(0, AppLayout.buttonHeightLarge),
              padding: EdgeInsets.zero,
            ),
            onPressed: onSaveTemplate,
            icon: const Icon(LucideIcons.download, size: 16),
            label: const Text('Save CSV template'),
          ),
          if (note != null) Text(note!, style: secondary),
        ],
      ),
    );
  }
}

class _PreviewSummary extends StatelessWidget {
  const _PreviewSummary({required this.target, required this.preview});

  final CsvImportTarget target;
  final CsvImportPreview preview;

  @override
  Widget build(BuildContext context) {
    final headerError = preview.headerError;
    final errors = preview.errors;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (headerError != null)
          _Alert(message: headerError)
        else ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _Count(label: 'Total: ${preview.total}'),
              _Count(
                label: 'Valid: ${preview.valid}',
                color: AppColors.success700,
                background: AppColors.success50,
                icon: LucideIcons.circleCheck,
              ),
              if (preview.invalid > 0)
                _Count(
                  label: 'Invalid: ${preview.invalid}',
                  color: AppColors.danger700,
                  background: AppColors.danger50,
                  icon: LucideIcons.circleAlert,
                ),
            ],
          ),
          if (errors.isNotEmpty) ...[
            const SizedBox(height: 12),
            _RowErrors(
              heading:
                  '${errors.length} error${errors.length == 1 ? '' : 's'}, '
                  'fix the CSV before importing',
              errors: errors,
            ),
          ] else if (preview.valid == 0) ...[
            const SizedBox(height: 12),
            Text(
              'No ${target.plural} to import in this file.',
              style: AppTypography.bodySmall.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ],
      ],
    );
  }
}

class _Count extends StatelessWidget {
  const _Count({
    required this.label,
    this.color = AppColors.textPrimary,
    this.background = AppColors.gray100,
    this.icon,
  });

  final String label;
  final Color color;
  final Color background;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: background,
        borderRadius: AppLayout.borderRadiusFull,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 4),
          ],
          Text(label, style: AppTypography.labelSmall.copyWith(color: color)),
        ],
      ),
    );
  }
}

/// Row number, column and problem, in a box that scrolls on its own so a file
/// with hundreds of bad rows does not push the buttons off the screen.
class _RowErrors extends StatelessWidget {
  const _RowErrors({required this.heading, required this.errors});

  final String heading;
  final List<CsvRowError> errors;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          heading,
          style: AppTypography.bodySmall.copyWith(
            color: AppColors.danger800,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 8),
        Container(
          constraints: const BoxConstraints(maxHeight: 240),
          decoration: BoxDecoration(
            color: AppColors.danger50,
            border: Border.all(color: AppColors.danger200),
            borderRadius: AppLayout.borderRadiusMd,
          ),
          child: ListView.separated(
            shrinkWrap: true,
            padding: EdgeInsets.zero,
            itemCount: errors.length,
            separatorBuilder: (_, _) =>
                const Divider(height: 1, color: AppColors.danger200),
            itemBuilder: (_, i) {
              final e = errors[i];
              return Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 8,
                ),
                child: Text.rich(
                  TextSpan(
                    children: [
                      TextSpan(
                        text: 'Row ${e.row}',
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      if (e.field.isNotEmpty) TextSpan(text: ' · ${e.field}'),
                      TextSpan(text: '\n${e.message}'),
                    ],
                  ),
                  style: AppTypography.bodySmall.copyWith(
                    color: AppColors.danger900,
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _Alert extends StatelessWidget {
  const _Alert({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.danger50,
        border: Border.all(color: AppColors.danger200),
        borderRadius: AppLayout.borderRadiusMd,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            LucideIcons.circleAlert,
            size: 16,
            color: AppColors.danger700,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: AppTypography.bodySmall.copyWith(
                color: AppColors.danger800,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
