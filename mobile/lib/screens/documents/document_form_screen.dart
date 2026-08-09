import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/crm_document.dart';
import '../../providers/auth_provider.dart';
import '../../providers/documents_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../services/attachment_upload.dart';
import '../../widgets/forms/unsaved_changes.dart';

/// Upload a document, or change one that is already here.
///
/// The two modes differ in more than a button label, which is why they share a
/// screen but not a request:
///
/// * **Upload** is multipart and carries the file. Open to any org member.
/// * **Edit** is JSON and carries no file at all. Replacing the bytes behind a
///   title is a different act from renaming or re-sharing, and neither client
///   offers it. Gated to the uploader or an admin server-side, so the screen
///   refuses up front rather than drawing a form that would 403 on save.
///
/// `shared_to` and `teams` are sent on every edit, never omitted: the view
/// clears both and re-adds from the body, so a missing list is an emptied list.
class DocumentFormScreen extends ConsumerStatefulWidget {
  const DocumentFormScreen({super.key, this.documentId});

  /// Null when uploading.
  final String? documentId;

  bool get isEditing => documentId != null;

  @override
  ConsumerState<DocumentFormScreen> createState() => _DocumentFormScreenState();
}

class _DocumentFormScreenState extends ConsumerState<DocumentFormScreen> {
  final TextEditingController _title = TextEditingController();

  String _status = 'active';
  final Set<String> _sharedTo = {};
  final Set<String> _teams = {};
  PlatformFile? _file;

  bool _saving = false;
  bool _dirty = false;
  bool _loaded = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (!widget.isEditing) _loaded = true;
    _title.addListener(() {
      if (!_dirty && _loaded) _dirty = true;
    });
  }

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  void _fill(CrmDocument doc) {
    _title.text = doc.title;
    _status = doc.status;
    _sharedTo
      ..clear()
      ..addAll(doc.sharedTo.map((p) => p.id));
    _teams
      ..clear()
      ..addAll(doc.teams.map((t) => t.id));
    // Set last: assigning to the controller fires the listener above, and a
    // form that opens already dirty prompts on the way out of a page nobody
    // edited.
    _loaded = true;
    _dirty = false;
  }

  Future<void> _pickFile() async {
    final result = await selectAttachment();
    if (result.cancelled || !mounted) return;
    if (result.error != null) {
      setState(() => _error = result.error);
      return;
    }
    setState(() {
      _file = result.file;
      _dirty = true;
      _error = null;
      // A file the user picked before naming the document is almost always the
      // name they want. Only ever fills a blank field.
      if (_title.text.trim().isEmpty) {
        final name = result.file!.name;
        final dot = name.lastIndexOf('.');
        _title.text = dot > 0 ? name.substring(0, dot) : name;
      }
    });
  }

  Future<void> _save() async {
    final problem = validateDocumentTitle(_title.text);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    if (!widget.isEditing && _file == null) {
      setState(() => _error = 'Pick a file to upload.');
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    final notifier = ref.read(documentsProvider.notifier);
    final response = widget.isEditing
        ? await notifier.updateDocument(
            widget.documentId!,
            title: _title.text.trim(),
            status: _status,
            sharedTo: _sharedTo.toList(),
            teams: _teams.toList(),
            // Null means keep the stored file. Picking one replaces it and
            // keeps the record, and so keeps its shares.
            filePath: _file?.path,
            fileName: _file?.name,
          )
        : await notifier.uploadDocument(
            title: _title.text.trim(),
            status: _status,
            filePath: _file!.path!,
            fileName: _file!.name,
            sharedTo: _sharedTo.toList(),
            teams: _teams.toList(),
          );

    // The picker copied the chosen file into the app's cache. Without this
    // every document somebody sends stays on their phone a second time, in a
    // directory they never see. An edit that replaced the file made a copy
    // too, so this can no longer be skipped on the edit path.
    if (_file != null) await clearAttachmentPickerCache();

    if (!mounted) return;
    if (!response.success) {
      setState(() {
        _saving = false;
        _error = response.message ?? 'Could not save this document.';
      });
      return;
    }

    _dirty = false;
    context.pop();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          widget.isEditing ? 'Document updated' : 'Document uploaded',
        ),
      ),
    );
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this document?'),
        content: const Text(
          'The file and the record of it go for good, for everybody it was '
          'shared with. To take it out of the way without destroying it, '
          'archive it instead.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _saving = true);
    final response = await ref
        .read(documentsProvider.notifier)
        .deleteDocument(widget.documentId!);
    if (!mounted) return;
    if (!response.success) {
      setState(() {
        _saving = false;
        _error = response.message ?? 'Could not delete this document.';
      });
      return;
    }
    _dirty = false;
    context.pop();
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('Document deleted')));
  }

  @override
  Widget build(BuildContext context) {
    if (widget.isEditing && !_loaded) {
      final async = ref.watch(documentsProvider);
      return async.when(
        loading: () =>
            const Scaffold(body: Center(child: CircularProgressIndicator())),
        error: (e, _) => _Refused(message: e.toString()),
        data: (data) {
          CrmDocument? doc;
          for (final candidate in data.documents) {
            if (candidate.id == widget.documentId) doc = candidate;
          }
          if (doc == null) {
            return const _Refused(
              message:
                  'That document is not in your list any more. It may have '
                  'been deleted, archived, or un-shared with you.',
            );
          }
          final canWrite = canWriteDocument(
            isAdmin: ref.watch(isOrgAdminProvider),
            myEmail: ref.watch(myEmailProvider),
            uploaderEmail: doc.uploaderEmail,
          );
          if (!canWrite) {
            return const _Refused(
              message:
                  'Only the person who uploaded a document, or an '
                  'administrator, can change it. Being able to open a document '
                  'is not the same as being able to rewrite it for everybody '
                  'else.',
            );
          }
          _fill(doc);
          return _form(context);
        },
      );
    }
    return _form(context);
  }

  Widget _form(BuildContext context) {
    final people = ref.watch(usersProvider);
    final teams = ref.watch(teamsProvider);

    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _dirty,
      isSaving: _saving,
      child: Scaffold(
        backgroundColor: AppColors.surfaceDim,
        appBar: AppBar(
          title: Text(widget.isEditing ? 'Edit document' : 'Upload document'),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.arrowLeft),
            onPressed: _saving
                ? null
                : () => leaveForm(context, hasUnsavedChanges: _dirty),
          ),
        ),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: [
            if (_error != null) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.danger50,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AppColors.danger200),
                ),
                child: Text(
                  _error!,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.danger600,
                  ),
                ),
              ),
              const SizedBox(height: 16),
            ],

            // Optional on an edit, required on an upload. Replacing the file
            // here keeps the record and its shares; the alternative was
            // deleting and re-uploading, which lost every share it had.
            OutlinedButton.icon(
              onPressed: _saving ? null : _pickFile,
              icon: const Icon(LucideIcons.paperclip, size: 18),
              label: Text(
                _file != null
                    ? _file!.name
                    : widget.isEditing
                    ? 'Replace the file'
                    : 'Choose a file',
              ),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size(double.infinity, 48),
                alignment: Alignment.centerLeft,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              widget.isEditing && _file == null
                  ? 'Leave this alone to keep the current file. Files must be 25 MB or smaller.'
                  : 'Files must be 25 MB or smaller.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),

            TextField(
              controller: _title,
              textCapitalization: TextCapitalization.sentences,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'Title',
                border: OutlineInputBorder(),
                counterText: '',
                helperText: 'Must be unique within the organisation',
              ),
            ),
            const SizedBox(height: 16),

            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _status == 'active',
              onChanged: _saving
                  ? null
                  : (value) => setState(() {
                      _status = value ? 'active' : 'inactive';
                      _dirty = true;
                    }),
              title: const Text('Active'),
              subtitle: Text(
                'Archiving hides a document from the default list. The file '
                'and everyone it is shared with stay exactly as they are.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            const SizedBox(height: 8),

            Text(
              'SHARE WITH',
              style: AppTypography.overline.copyWith(
                color: AppColors.textSecondary,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              // Both halves are true and neither is obvious: sharing with
              // nobody is legal and useful, and admins are not a share.
              'Leave everything unticked to keep it to yourself. Administrators '
              'can open every document either way.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 4),

            for (final person in people)
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                dense: false,
                value: _sharedTo.contains(person.id),
                title: Text(person.displayName),
                subtitle: Text(
                  person.email,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                onChanged: _saving
                    ? null
                    : (checked) => setState(() {
                        _dirty = true;
                        if (checked == true) {
                          _sharedTo.add(person.id);
                        } else {
                          _sharedTo.remove(person.id);
                        }
                      }),
              ),

            if (teams.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                'TEAMS',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                // A team share follows the team, which is the reason to prefer
                // it over ticking its members one by one.
                'A team share reaches whoever is in the team at the time, not '
                'whoever is in it today.',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              for (final team in teams)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _teams.contains(team.id),
                  title: Text(team.name),
                  onChanged: _saving
                      ? null
                      : (checked) => setState(() {
                          _dirty = true;
                          if (checked == true) {
                            _teams.add(team.id);
                          } else {
                            _teams.remove(team.id);
                          }
                        }),
                ),
            ],

            const SizedBox(height: 20),
            SizedBox(
              height: 48,
              child: FilledButton(
                onPressed: _saving ? null : _save,
                child: _saving
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Text(widget.isEditing ? 'Save changes' : 'Upload'),
              ),
            ),

            if (widget.isEditing) ...[
              const SizedBox(height: 12),
              SizedBox(
                height: 48,
                child: OutlinedButton(
                  onPressed: _saving ? null : _delete,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.danger600,
                    side: BorderSide(color: AppColors.danger200),
                  ),
                  child: const Text('Delete document'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Refused extends StatelessWidget {
  const _Refused({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Document')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            message,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}
