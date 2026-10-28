import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../config/api_config.dart';
import '../../core/theme/theme.dart';
import '../../data/models/attachment.dart';
import '../../data/models/comment.dart';
import '../../data/models/custom_object.dart';
import '../../providers/auth_provider.dart';
import '../../providers/custom_objects_provider.dart';
import '../../routes/app_router.dart';
import '../../services/attachment_upload.dart';
import '../../widgets/common/common.dart';
import '../../widgets/interactions/record_timeline.dart';
import '../../widgets/objects/photo_gallery.dart';
import '../../widgets/objects/record_matches_card.dart';
import '../../widgets/objects/related_objects_section.dart';

const _missingRecord =
    'This record does not exist, or you do not have access to it.';

/// One custom object record, with its links, photos, files and people.
class ObjectRecordDetailScreen extends ConsumerStatefulWidget {
  const ObjectRecordDetailScreen({
    super.key,
    required this.objectKey,
    required this.recordId,
  });

  final String objectKey;
  final String recordId;

  @override
  ConsumerState<ObjectRecordDetailScreen> createState() =>
      _ObjectRecordDetailScreenState();
}

class _ObjectRecordDetailScreenState
    extends ConsumerState<ObjectRecordDetailScreen> {
  ObjectRecord? _record;
  bool _loading = true;
  bool _notFound = false;
  String? _error;
  bool _uploading = false;
  bool _addingNote = false;
  final _noteController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _noteController.dispose();
    super.dispose();
  }

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final response = await ref
          .read(customObjectsApiProvider)
          .fetchRecord(widget.objectKey, widget.recordId);
      if (!mounted) return;
      final data = response.data;
      setState(() {
        _loading = false;
        _notFound = response.statusCode == 404;
        if (response.success && data != null) {
          _record = ObjectRecord.fromJson(data);
          _error = null;
        } else if (!_notFound) {
          _error = response.message ?? 'Could not load this record';
        }
      });
    } on ArgumentError {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _notFound = true;
      });
    }
  }

  void _snack(String message, {bool error = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        behavior: SnackBarBehavior.floating,
        backgroundColor: error ? AppColors.danger600 : null,
      ),
    );
  }

  Future<bool> _confirm(String title, String body) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text('Delete', style: TextStyle(color: AppColors.danger600)),
          ),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _deleteRecord(ObjectDefinition def) async {
    final label = def.summary.label;
    if (!await _confirm(
      'Delete $label?',
      'This action cannot be undone. Its photos and files are deleted too.',
    )) {
      return;
    }
    final response = await ref
        .read(customObjectsApiProvider)
        .deleteRecord(widget.objectKey, widget.recordId);
    if (!mounted) return;
    if (!response.success) {
      _snack(response.message ?? 'Failed to delete $label', error: true);
      return;
    }
    // Refresh in place: the list keeps its filters and its search text.
    unawaited(
      ref.read(objectRecordsProvider(widget.objectKey).notifier).refresh(),
    );
    context.pop();
  }

  Future<void> _addNote() async {
    final text = _noteController.text.trim();
    if (text.isEmpty || _addingNote) return;
    setState(() => _addingNote = true);
    final response = await ref
        .read(customObjectsApiProvider)
        .addNote(widget.objectKey, widget.recordId, text);
    if (!mounted) return;
    setState(() => _addingNote = false);
    final data = response.data;
    if (!response.success || data == null) {
      _snack(noteError(response, 'Could not add that note.'), error: true);
      return;
    }
    _noteController.clear();
    setState(() => _record = ObjectRecord.fromJson(data));
  }

  Future<void> _editNote(Comment note) async {
    final text = await showDialog<String>(
      context: context,
      builder: (_) =>
          EditNoteDialog(initialText: note.comment, maxLength: noteMaxLength),
    );
    if (text == null || text.isEmpty || text == note.comment.trim()) return;
    final response = await ref
        .read(customObjectsApiProvider)
        .editNote(widget.objectKey, note.id, text);
    if (!mounted) return;
    if (!response.success) {
      _snack(noteError(response, 'Could not update that note.'), error: true);
      return;
    }
    await _load(quiet: true);
  }

  Future<void> _deleteNote(Comment note) async {
    if (!await _confirm('Delete note?', 'This action cannot be undone.')) {
      return;
    }
    final response = await ref
        .read(customObjectsApiProvider)
        .deleteNote(widget.objectKey, note.id);
    if (!mounted) return;
    if (!response.success) {
      _snack(noteError(response, 'Could not delete that note.'), error: true);
      return;
    }
    await _load(quiet: true);
  }

  Future<void> _upload() async {
    if (_uploading) return;
    setState(() => _uploading = true);
    final result = await pickAndUploadAttachmentTo(
      url: ApiConfig.objectRecordAttachments(widget.objectKey, widget.recordId),
      field: 'file',
    );
    if (!mounted) return;
    setState(() => _uploading = false);
    if (result.cancelled) return;
    if (!result.succeeded) {
      _snack(result.error!, error: true);
      return;
    }
    await _load(quiet: true);
  }

  Future<void> _deleteAttachment(RecordAttachment a) async {
    if (!await _confirm(
      a.isPhoto ? 'Delete photo?' : 'Delete file?',
      'Delete ${a.fileName}? This action cannot be undone.',
    )) {
      return;
    }
    final response = await ref
        .read(customObjectsApiProvider)
        .deleteAttachment(widget.objectKey, widget.recordId, a.id);
    if (!mounted) return;
    if (!response.success) {
      _snack(response.message ?? 'Could not delete that file', error: true);
      return;
    }
    await _load(quiet: true);
  }

  Future<void> _openFile(Attachment a) async {
    if (!a.hasFile) {
      _snack('That attachment has no file.');
      return;
    }
    final result = await downloadAttachment(
      url: ApiConfig.attachmentDownload(a.id),
      fileName: a.fileName,
    );
    if (!mounted || result.success) return;
    _snack(result.error ?? 'Could not download that file.');
  }

  @override
  Widget build(BuildContext context) {
    final definition = ref.watch(objectDefinitionProvider(widget.objectKey));

    return definition.when(
      loading: () => _shell(
        title: 'Record',
        body: const Center(child: CircularProgressIndicator()),
      ),
      error: (_, _) => _shell(
        title: 'Record',
        body: _Problem(
          message: 'Could not load this record',
          onRetry: () =>
              ref.invalidate(objectDefinitionProvider(widget.objectKey)),
        ),
      ),
      data: (def) {
        if (def == null) return _shell(title: 'Record', body: _missing());
        final record = _record;
        if (_loading && record == null) {
          return _shell(
            title: def.summary.label,
            body: const Center(child: CircularProgressIndicator()),
          );
        }
        if (_notFound) {
          return _shell(title: def.summary.label, body: _missing());
        }
        if (record == null) {
          return _shell(
            title: def.summary.label,
            body: _Problem(
              message: _error ?? 'Could not load this record',
              onRetry: _load,
            ),
          );
        }
        return _shell(
          title: def.summary.label,
          actions: [
            if (record.canEdit && _canWrite(def))
              IconButton(
                tooltip: 'Edit',
                icon: const Icon(LucideIcons.pencil),
                onPressed: () async {
                  await context.push(
                    AppRoutes.objectRecordEdit(widget.objectKey, record.id),
                  );
                  if (mounted) _load(quiet: true);
                },
              ),
            if (record.canDelete && _canWrite(def))
              IconButton(
                tooltip: 'Delete',
                icon: Icon(LucideIcons.trash2, color: AppColors.danger600),
                onPressed: () => _deleteRecord(def),
              ),
          ],
          body: _content(def, record),
        );
      },
    );
  }

  bool _canWrite(ObjectDefinition def) => canWriteObject(
    def.summary,
    shownCustomObjects(ref.watch(customObjectsProvider)),
  );

  Widget _missing() =>
      const EmptyState(icon: LucideIcons.boxes, title: _missingRecord);

  Widget _shell({
    required String title,
    required Widget body,
    List<Widget> actions = const [],
  }) {
    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(title),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: actions,
      ),
      body: body,
    );
  }

  Widget _content(ObjectDefinition def, ObjectRecord record) {
    final photos = record.attachments.where((a) => a.isPhoto).toList();
    final files = record.attachments.where((a) => !a.isPhoto).toList();
    return RefreshIndicator(
      onRefresh: () => _load(quiet: true),
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
            sliver: SliverList(
              delegate: SliverChildListDelegate([
                _card('Details', [
                  _field(def.summary.nameLabel, record.name),
                  for (final f in def.customFields)
                    _field(
                      f.label,
                      hasFieldValue(record.customFields[f.key])
                          ? displayValue(f, record.customFields[f.key])
                          : '',
                    ),
                ]),
                if (def.linkFields.isNotEmpty)
                  _card('Links', [
                    for (final f in def.linkFields)
                      _linkField(f.label, record.links[f.key]),
                  ]),
                if (def.summary.matches)
                  RecordMatchesCard(recordId: widget.recordId),
                if (photos.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(2, 0, 2, 10),
                    child: Text('Photos', style: AppTypography.h3),
                  ),
              ]),
            ),
          ),
          if (photos.isNotEmpty)
            SliverPadding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              sliver: PhotoGallery(photos: photos, onDelete: _deleteAttachment),
            ),
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 32),
            sliver: SliverList(
              delegate: SliverChildListDelegate([
                if (files.isNotEmpty || (record.canEdit && _canWrite(def)))
                  _card('Files', [
                    if (files.isEmpty) _none(),
                    for (final a in files) _fileRow(a),
                    if (record.canEdit && _canWrite(def)) _attachButton(),
                  ]),
                if (record.tags.isNotEmpty)
                  _card('Tags', [
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final t in record.tags) LabelPill(label: t.name),
                      ],
                    ),
                  ]),
                _timeline(def, record),
                _card('People', [
                  _field(
                    'Assigned to',
                    record.assignedTo.isEmpty
                        ? ''
                        : record.assignedTo.map(_personName).join(', '),
                  ),
                  _field(
                    'Created by',
                    record.createdBy == null
                        ? ''
                        : _personName(record.createdBy!),
                  ),
                ]),
                if (ApiConfig.isObjectKey(widget.objectKey))
                  RelatedObjectsSection(
                    to: 'obj:${widget.objectKey}',
                    recordId: widget.recordId,
                  ),
              ]),
            ),
          ),
        ],
      ),
    );
  }

  /// Notes and the record's creation, newest first. Posting and editing need
  /// writes on for the org; deleting your own note never does.
  Widget _timeline(ObjectDefinition def, ObjectRecord record) {
    final canWrite = _canWrite(def);
    final email = ref.watch(myEmailProvider);
    final isAdmin = ref.watch(isOrgAdminProvider);
    final creator = record.createdBy == null
        ? ''
        : _personName(record.createdBy!);
    final events = <TimelineEvent>[
      for (final n in record.comments)
        TimelineEvent(
          icon: LucideIcons.messageSquare,
          tint: AppColors.primary600,
          ts: n.commentedOn,
          actor: n.authorName,
          summary: 'added a note',
          body: n.comment,
          onEdit: canWrite && canEditNote(n, email) ? () => _editNote(n) : null,
          onDelete: canDeleteNote(n, email, isAdmin: isAdmin)
              ? () => _deleteNote(n)
              : null,
        ),
      if (record.createdAt != null)
        TimelineEvent(
          icon: LucideIcons.sparkles,
          tint: AppColors.success600,
          ts: record.createdAt!,
          actor: creator,
          summary: creator.isEmpty
              ? '${def.summary.label} created'
              : 'created this ${def.summary.label.toLowerCase()}',
        ),
    ]..sort((a, b) => b.ts.compareTo(a.ts));
    return _card('Timeline', [
      if (canWrite) _noteComposer(),
      for (var i = 0; i < events.length; i++)
        TimelineEventRow(event: events[i], isLast: i == events.length - 1),
    ]);
  }

  Widget _noteComposer() {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              controller: _noteController,
              enabled: !_addingNote,
              minLines: 1,
              maxLines: 5,
              inputFormatters: [
                LengthLimitingTextInputFormatter(noteMaxLength),
              ],
              textCapitalization: TextCapitalization.sentences,
              decoration: const InputDecoration(
                hintText: 'Add a note',
                isDense: true,
                border: OutlineInputBorder(),
              ),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 44,
            height: 44,
            child: IconButton.filled(
              tooltip: 'Add note',
              onPressed: _addingNote ? null : _addNote,
              icon: _addingNote
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(LucideIcons.send, size: 18),
            ),
          ),
        ],
      ),
    );
  }

  Widget _card(String title, List<Widget> children) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: AppTypography.h3),
          const SizedBox(height: 10),
          ...children,
        ],
      ),
    );
  }

  Widget _label(String text) => Text(
    text,
    style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
  );

  Widget _none() => Padding(
    padding: const EdgeInsets.only(bottom: 4),
    child: Text(
      'None',
      style: AppTypography.body.copyWith(color: AppColors.textTertiary),
    ),
  );

  Widget _field(String label, String value) {
    final empty = value.trim().isEmpty;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _label(label),
          const SizedBox(height: 2),
          Text(
            empty ? 'None' : value,
            style: AppTypography.body.copyWith(
              color: empty ? AppColors.textTertiary : AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _linkField(String label, LinkValue? value) {
    final records = value?.records ?? const <LinkedRecord>[];
    final hidden = value?.hidden ?? 0;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _label(label),
          if (records.isEmpty && hidden == 0) _none(),
          for (final r in records) _linkRow(r),
          if (hidden > 0)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                records.isEmpty
                    ? "$hidden you can't open"
                    : "and $hidden you can't open",
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _linkRow(LinkedRecord r) {
    final route = linkedRecordRoute(r.object, r.id);
    final name = r.name.isEmpty ? 'Untitled' : r.name;
    if (route == null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(name, style: AppTypography.body),
      );
    }
    return InkWell(
      onTap: () => context.push(route),
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 44),
        child: Align(
          alignment: Alignment.centerLeft,
          child: Text(
            name,
            style: AppTypography.body.copyWith(color: AppColors.primary600),
          ),
        ),
      ),
    );
  }

  Widget _attachButton() => Align(
    alignment: Alignment.centerLeft,
    child: ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 44),
      child: AttachFileButton(isUploading: _uploading, onPressed: _upload),
    ),
  );

  Widget _fileRow(RecordAttachment a) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Expanded(
            child: AttachmentTile(
              attachment: a.toAttachment(),
              onOpen: _openFile,
            ),
          ),
          if (a.canDelete)
            IconButton(
              tooltip: 'Delete file',
              icon: Icon(LucideIcons.trash2, color: AppColors.danger600),
              onPressed: () => _deleteAttachment(a),
            ),
        ],
      ),
    );
  }

  static String _personName(PersonRef p) =>
      p.name.isNotEmpty ? p.name : p.email;
}

class _Problem extends StatelessWidget {
  const _Problem({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, style: AppTypography.h3, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            TextButton(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      ),
    );
  }
}
