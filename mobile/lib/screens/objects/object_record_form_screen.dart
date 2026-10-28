import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/api/enterprise_schema.g.dart' as enterprise;
import '../../data/models/custom_object.dart';
import '../../data/models/lookup_models.dart';
import '../../providers/custom_objects_provider.dart';
import '../../providers/lookup_provider.dart';
import '../../routes/app_router.dart';
import '../../widgets/common/common.dart';
import '../../widgets/forms/custom_fields_form.dart';
import '../../widgets/forms/multi_select_sheet.dart';
import '../../widgets/forms/unsaved_changes.dart';
import '../../widgets/objects/link_field_picker.dart';

const _cannotEdit = 'You cannot edit this record.';
const _missingRecord =
    'This record does not exist, or you do not have access to it.';

/// Creates a custom object record, or edits one when [recordId] is given.
class ObjectRecordFormScreen extends ConsumerStatefulWidget {
  const ObjectRecordFormScreen({
    super.key,
    required this.objectKey,
    this.recordId,
  });

  final String objectKey;
  final String? recordId;

  @override
  ConsumerState<ObjectRecordFormScreen> createState() =>
      _ObjectRecordFormScreenState();
}

class _ObjectRecordFormScreenState
    extends ConsumerState<ObjectRecordFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();

  ObjectRecord? _original;
  bool _loadingRecord = false;
  bool _notFound = false;
  String? _loadError;

  bool _seeded = false;
  Map<String, dynamic> _custom = {};
  Map<String, List<LinkedRecord>> _links = {};
  List<String> _assignees = [];
  List<String> _tagIds = [];
  final Map<String, String> _storedNames = {};
  final Map<String, String> _tagNames = {};

  bool _saving = false;
  bool _dirty = false;
  String? _saveError;
  final _errorKey = GlobalKey();

  bool get _editing => widget.recordId != null;

  @override
  void initState() {
    super.initState();
    if (_editing) _loadRecord();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _loadRecord() async {
    setState(() {
      _loadingRecord = true;
      _loadError = null;
    });
    try {
      final response = await ref
          .read(customObjectsApiProvider)
          .fetchRecord(widget.objectKey, widget.recordId!);
      if (!mounted) return;
      final data = response.data;
      setState(() {
        _loadingRecord = false;
        _notFound = response.statusCode == 404;
        if (response.success && data != null) {
          _original = ObjectRecord.fromJson(data);
        } else if (!_notFound) {
          _loadError = response.message ?? 'Could not load this record';
        }
      });
    } on ArgumentError {
      if (!mounted) return;
      setState(() {
        _loadingRecord = false;
        _notFound = true;
      });
    }
  }

  void _seed(ObjectDefinition def) {
    if (_seeded) return;
    _seeded = true;
    final original = _original;
    if (original == null) return;
    _name.text = original.name;
    _custom = Map<String, dynamic>.from(original.customFields);
    _links = {
      for (final f in def.linkFields)
        f.key: [...?original.links[f.key]?.records],
    };
    _assignees = [for (final p in original.assignedTo) p.id];
    for (final p in original.assignedTo) {
      _storedNames[p.id] = p.name.isNotEmpty ? p.name : p.email;
    }
    _tagIds = [for (final t in original.tags) t.id];
    for (final t in original.tags) {
      _tagNames[t.id] = t.name;
    }
  }

  Future<void> _pickTags(List<TagLookup> tags) async {
    final picked = await MultiSelectSheet.show<TagLookup>(
      context: context,
      title: 'Tags',
      items: tags,
      initialSelection: [
        for (final t in tags)
          if (_tagIds.contains(t.id)) t,
      ],
      labelOf: (t) => t.name,
      searchText: (t) => t.name,
      emptyMessage: 'No tags',
    );
    if (picked == null) return;
    final listed = {for (final t in tags) t.id};
    setState(() {
      // A tag the record carries that is turned off is not offered, and stays.
      _tagIds = [
        for (final id in _tagIds)
          if (!listed.contains(id)) id,
        for (final t in picked) t.id,
      ];
      for (final t in picked) {
        _tagNames[t.id] = t.name;
      }
      _dirty = true;
    });
  }

  List<UserLookup> _people(List<UserLookup> active) {
    final known = {for (final u in active) u.id};
    return [
      ...active,
      for (final e in _storedNames.entries)
        if (!known.contains(e.key))
          UserLookup(
            id: e.key,
            email: '',
            name: '${e.value} (inactive)',
            role: 'USER',
            isActive: false,
          ),
    ];
  }

  Future<void> _pickAssignees(List<UserLookup> people) async {
    final picked = await MultiSelectSheet.show<UserLookup>(
      context: context,
      title: 'Assigned to',
      items: people,
      initialSelection: [
        for (final u in people)
          if (_assignees.contains(u.id)) u,
      ],
      labelOf: (u) => u.displayName,
      searchText: (u) => '${u.displayName} ${u.email}',
      emptyMessage: 'No members',
    );
    if (picked == null) return;
    setState(() {
      _assignees = [for (final u in picked) u.id];
      _dirty = true;
    });
  }

  Future<void> _save(ObjectDefinition def) async {
    if (_saving) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;
    setState(() {
      _saving = true;
      _saveError = null;
    });
    final body = recordWriteBody(
      definition: def,
      name: _name.text.trim(),
      customFields: _custom,
      links: {
        for (final e in _links.entries) e.key: [for (final r in e.value) r.id],
      },
      assignees: _assignees,
      tags: _tagIds,
      original: _original,
    );
    final api = ref.read(customObjectsApiProvider);
    final response = _editing
        ? await api.updateRecord(widget.objectKey, widget.recordId!, body)
        : await api.createRecord(widget.objectKey, body);
    if (!mounted) return;
    if (!response.success) {
      setState(() {
        _saving = false;
        _saveError = recordWriteError(response, def);
      });
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(
          SnackBar(
            content: Text(_saveError!),
            backgroundColor: AppColors.danger600,
          ),
        );
      WidgetsBinding.instance.addPostFrameCallback((_) {
        final target = _errorKey.currentContext;
        if (target != null) {
          Scrollable.ensureVisible(
            target,
            duration: const Duration(milliseconds: 200),
          );
        }
      });
      return;
    }
    setState(() => _saving = false);
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    // Refresh in place: the list keeps its filters and its search text.
    unawaited(
      ref.read(objectRecordsProvider(widget.objectKey).notifier).refresh(),
    );
    if (_editing) {
      context.pop();
      return;
    }
    final id = enterprise.CustomRecordDetail.fromJson(response.data ?? {}).id;
    if (id == null || id.isEmpty) {
      context.pop();
    } else {
      context.pushReplacement(AppRoutes.objectRecord(widget.objectKey, id));
    }
  }

  Widget _shell(String title, Widget body, {Widget? bottom}) => Scaffold(
    backgroundColor: AppColors.surface,
    appBar: AppBar(
      title: Text(title),
      backgroundColor: AppColors.surface,
      elevation: 0,
      scrolledUnderElevation: 1,
    ),
    body: body,
    bottomNavigationBar: bottom,
  );

  Widget _message(String text, {VoidCallback? onRetry}) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            text,
            textAlign: TextAlign.center,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
          if (onRetry != null) ...[
            const SizedBox(height: 16),
            TextButton(
              onPressed: onRetry,
              style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
              child: const Text('Retry'),
            ),
          ],
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final definition = ref.watch(objectDefinitionProvider(widget.objectKey));
    final answerState = ref.watch(customObjectsProvider);
    final answer = shownCustomObjects(answerState);
    const loading = Center(child: CircularProgressIndicator());

    return definition.when(
      loading: () => _shell('Record', loading),
      error: (_, _) => _shell(
        'Record',
        _message(
          'Could not load this record',
          onRetry: () =>
              ref.invalidate(objectDefinitionProvider(widget.objectKey)),
        ),
      ),
      data: (def) {
        if (def == null) return _shell('Record', _message(_missingRecord));
        final title = _editing
            ? 'Edit ${def.summary.label}'
            : 'New ${def.summary.label}';
        final decided = def.summary.writable != null;
        if (!decided && answerState.isLoading) return _shell(title, loading);
        if (!decided && customObjectsFailed(answerState)) {
          return _shell(
            title,
            _message(
              objectsLoadFailedNotice,
              onRetry: () => ref.invalidate(customObjectsProvider),
            ),
          );
        }
        if (!canWriteObject(def.summary, answer)) {
          return _shell(title, _message(objectsTurnedOffNotice));
        }
        if (_editing) {
          if (_loadingRecord) return _shell(title, loading);
          if (_notFound) return _shell(title, _message(_missingRecord));
          final original = _original;
          if (original == null) {
            return _shell(
              title,
              _message(
                _loadError ?? 'Could not load this record',
                onRetry: _loadRecord,
              ),
            );
          }
          if (!original.canEdit) return _shell(title, _message(_cannotEdit));
        }
        _seed(def);
        return _form(def, title);
      },
    );
  }

  Widget _form(ObjectDefinition def, String title) {
    final people = _people(ref.watch(usersProvider));
    final byId = {for (final u in people) u.id: u};
    final tags = ref.watch(tagsProvider);
    // "No longer active" is only known once the list has loaded; while it
    // loads, or after it fails, a carried tag shows plainly.
    final tagsLoaded = ref.watch(tagsLookupProvider).hasValue;
    for (final t in tags) {
      _tagNames[t.id] = t.name;
    }
    final original = _original;
    return UnsavedChangesGuard(
      hasUnsavedChanges: () => _dirty,
      isSaving: _saving,
      child: Scaffold(
        backgroundColor: AppColors.surface,
        appBar: AppBar(
          title: Text(title),
          backgroundColor: AppColors.surface,
          elevation: 0,
          scrolledUnderElevation: 1,
          leading: IconButton(
            icon: const Icon(LucideIcons.chevronLeft),
            tooltip: 'Back',
            onPressed: _saving
                ? null
                : () => leaveForm(context, hasUnsavedChanges: _dirty),
          ),
        ),
        body: Form(
          key: _formKey,
          onChanged: () => _dirty = true,
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 24, 24, 32),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                FloatingLabelInput(
                  label: '${def.summary.nameLabel} *',
                  controller: _name,
                  maxLength: 255,
                  textCapitalization: TextCapitalization.sentences,
                  validator: (v) => (v == null || v.trim().isEmpty)
                      ? '${def.summary.nameLabel} is required'
                      : null,
                ),
                const SizedBox(height: 16),
                CustomFieldsForm(
                  definitions: [
                    for (final f in def.customFields)
                      f.toDefinition(widget.objectKey),
                  ],
                  values: _custom,
                  onChanged: (next) => setState(() {
                    _custom = next;
                    _dirty = true;
                  }),
                ),
                for (final f in def.linkFields) ...[
                  LinkFieldPicker(
                    field: f,
                    value:
                        original?.links[f.key] ??
                        LinkValue(many: f.many ?? true),
                    selected: _links[f.key] ?? const [],
                    onChanged: (next) => setState(() {
                      _links = {..._links, f.key: next};
                      _dirty = true;
                    }),
                  ),
                  const SizedBox(height: 16),
                ],
                Text(
                  'Assigned to',
                  style: AppTypography.label.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 6),
                if (_assignees.isNotEmpty)
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      for (final id in _assignees)
                        Chip(label: Text(byId[id]?.displayName ?? id)),
                    ],
                  ),
                TextButton.icon(
                  style: TextButton.styleFrom(
                    minimumSize: const Size(44, 44),
                    alignment: Alignment.centerLeft,
                  ),
                  onPressed: () => _pickAssignees(people),
                  icon: const Icon(LucideIcons.userPlus, size: 16),
                  label: const Text('Choose'),
                ),
                const SizedBox(height: 12),
                Text(
                  'Tags',
                  style: AppTypography.label.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 6),
                if (_tagIds.isNotEmpty)
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      for (final id in _tagIds)
                        if (!tagsLoaded || tags.any((t) => t.id == id))
                          Chip(label: Text(_tagNames[id] ?? 'Tag'))
                        else
                          // Turned off since it was applied: not offered in the
                          // picker, so this is the only way to drop it.
                          InputChip(
                            label: Text(
                              '${_tagNames[id] ?? 'Tag'} (no longer active)',
                            ),
                            onDeleted: () => setState(() {
                              _tagIds = [
                                for (final t in _tagIds)
                                  if (t != id) t,
                              ];
                              _dirty = true;
                            }),
                            deleteButtonTooltipMessage: 'Remove tag',
                          ),
                    ],
                  ),
                TextButton.icon(
                  style: TextButton.styleFrom(
                    minimumSize: const Size(44, 44),
                    alignment: Alignment.centerLeft,
                  ),
                  onPressed: () => _pickTags(tags),
                  icon: const Icon(LucideIcons.tag, size: 16),
                  label: const Text('Choose tags'),
                ),
                if (_saveError != null) ...[
                  const SizedBox(height: 16),
                  Container(
                    key: _errorKey,
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.danger50,
                      border: Border.all(color: AppColors.danger500),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      _saveError!,
                      style: AppTypography.body.copyWith(
                        color: AppColors.danger600,
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
        bottomNavigationBar: Container(
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: Border(top: BorderSide(color: AppColors.border)),
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
              child: PrimaryButton(
                label: 'Save',
                onPressed: _saving ? null : () => _save(def),
                isLoading: _saving,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
