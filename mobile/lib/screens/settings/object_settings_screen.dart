import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_object.dart';
import '../../providers/auth_provider.dart';
import '../../providers/custom_objects_provider.dart';
import '../../widgets/common/badge.dart';
import 'object_field_sheet.dart';
import 'objects_settings_screen.dart';

/// One custom object: its details, access and fields. Members read; an admin
/// edits with the server's say-so, and can always archive, restore or delete
/// for good (the server refuses a delete while records or links remain).
class ObjectSettingsScreen extends ConsumerWidget {
  const ObjectSettingsScreen({super.key, required this.objectKey});

  final String objectKey;

  void _say(BuildContext context, String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<bool> _confirm(
    BuildContext context, {
    required String title,
    required String body,
    required String action,
  }) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger600),
            child: Text(action),
          ),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _setActive(
    BuildContext context,
    WidgetRef ref,
    ObjectSummary summary,
    bool active,
  ) async {
    if (!active) {
      final ok = await _confirm(
        context,
        title: 'Archive ${summary.labelPlural}?',
        body:
            'Hides it from More and from Related sections; its records stay and come back when you restore it.',
        action: 'Archive',
      );
      if (!ok || !context.mounted) return;
    }
    final api = ref.read(customObjectsApiProvider);
    final response = active
        ? await api.updateObject(objectKey, {'is_active': true})
        : await api.deleteObject(objectKey);
    if (!context.mounted) return;
    if (response.success) refreshObjects(ref, objectKey);
    _say(
      context,
      response.success
          ? (active ? 'Restored' : 'Archived')
          : settingsWriteError(
              response,
              'Could not do that.',
              labels: objectFormLabels,
            ),
    );
  }

  Future<void> _destroy(
    BuildContext context,
    WidgetRef ref,
    ObjectSummary summary,
  ) async {
    final ok = await _confirm(
      context,
      title: 'Delete ${summary.labelPlural} for good?',
      body: 'Removes the object and its fields for good.',
      action: 'Delete for good',
    );
    if (!ok || !context.mounted) return;
    final response = await ref
        .read(customObjectsApiProvider)
        .deleteObject(objectKey);
    if (!context.mounted) return;
    if (response.success) {
      refreshObjects(ref, objectKey);
      context.pop();
    } else {
      _say(
        context,
        settingsWriteError(
          response,
          'Could not delete it.',
          labels: objectFormLabels,
        ),
      );
    }
  }

  Future<void> _editObject(
    BuildContext context,
    WidgetRef ref,
    ObjectSummary summary,
  ) {
    return showObjectFormSheet(
      context,
      existing: summary,
      onSubmit: (body) async {
        final response = await ref
            .read(customObjectsApiProvider)
            .updateObject(objectKey, body);
        if (!response.success) {
          return settingsWriteError(
            response,
            'Could not save the object.',
            labels: objectFormLabels,
          );
        }
        refreshObjects(ref, objectKey);
        return null;
      },
    );
  }

  Future<void> _chooseAccess(
    BuildContext context,
    WidgetRef ref,
    ObjectSummary summary,
  ) async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text('Who can see its records'),
            ),
            for (final o in const [
              ('everyone', 'Everyone in this organization'),
              ('own', 'Only the people involved'),
            ])
              ListTile(
                minTileHeight: 48,
                title: Text(o.$2),
                trailing:
                    o.$1 ==
                        (summary.recordAccess == 'everyone'
                            ? 'everyone'
                            : 'own')
                    ? Icon(LucideIcons.check, color: AppColors.primary600)
                    : null,
                onTap: () => Navigator.of(sheet).pop(o.$1),
              ),
          ],
        ),
      ),
    );
    if (picked == null || picked == summary.recordAccess || !context.mounted) {
      return;
    }
    final response = await ref.read(customObjectsApiProvider).updateObject(
      objectKey,
      {'record_access': picked},
    );
    if (!context.mounted) return;
    if (response.success) refreshObjects(ref, objectKey);
    _say(
      context,
      response.success
          ? 'Saved'
          : settingsWriteError(
              response,
              'Could not save who sees records.',
              labels: objectFormLabels,
            ),
    );
  }

  Future<void> _chooseBoard(
    BuildContext context,
    WidgetRef ref,
    ObjectSummary summary,
    List<ObjectField> fields,
  ) async {
    final choices = boardChoices(fields);
    final picked = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text('Group the board by'),
            ),
            for (final c in [null, ...choices])
              ListTile(
                minTileHeight: 48,
                title: Text(c?.label ?? 'No board'),
                trailing: (c?.key ?? '') == summary.boardField
                    ? Icon(LucideIcons.check, color: AppColors.primary600)
                    : null,
                onTap: () => Navigator.of(sheet).pop(c?.key ?? ''),
              ),
            if (choices.isEmpty)
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text(
                  'Add a dropdown field to group records on a board.',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
    if (picked == null || picked == summary.boardField || !context.mounted) {
      return;
    }
    final response = await ref
        .read(customObjectsApiProvider)
        .updateObject(objectKey, boardFieldBody(picked));
    if (!context.mounted) return;
    if (response.success) refreshObjects(ref, objectKey);
    _say(
      context,
      response.success
          ? (picked.isEmpty ? 'Board turned off' : 'Board field saved')
          : settingsWriteError(
              response,
              'Could not save the board field.',
              labels: objectFormLabels,
            ),
    );
  }

  Future<void> _editField(
    BuildContext context,
    WidgetRef ref,
    ObjectField? field,
    List<LinkTarget> targets,
  ) {
    return showObjectFieldSheet(
      context,
      existing: field,
      linkTargets: targets,
      onSubmit: (body) async {
        final api = ref.read(customObjectsApiProvider);
        final response = field == null
            ? await api.createField(objectKey, body)
            : await api.updateField(objectKey, field.key, body);
        if (!response.success) {
          return settingsWriteError(
            response,
            'Could not save the field.',
            labels: fieldFormLabels,
          );
        }
        refreshObjects(ref, objectKey);
        return null;
      },
    );
  }

  Future<void> _turn(
    BuildContext context,
    WidgetRef ref,
    ObjectField field,
    bool on,
  ) async {
    if (!on) {
      final ok = await _confirm(
        context,
        title: 'Turn off ${field.label}?',
        body: 'Stops being collected. Stored values stay.',
        action: 'Turn off',
      );
      if (!ok || !context.mounted) return;
    }
    final api = ref.read(customObjectsApiProvider);
    final response = on
        ? await api.updateField(objectKey, field.key, {'is_active': true})
        : await api.turnOffField(objectKey, field.key);
    if (!context.mounted) return;
    if (response.success) refreshObjects(ref, objectKey);
    _say(
      context,
      response.success
          ? (on ? 'Field turned on' : 'Field turned off')
          : settingsWriteError(
              response,
              'Could not do that.',
              labels: fieldFormLabels,
            ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(objectSettingsProvider(objectKey));
    final answerState = ref.watch(customObjectsProvider);
    final answer = shownCustomObjects(answerState);
    final loadFailed = customObjectsFailed(answerState);
    final isAdmin = ref.watch(isOrgAdminProvider);
    final targets = answer?.linkTargets ?? const <LinkTarget>[];

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: Text(async.value?.summary.labelPlural ?? 'Object'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('Could not load this object'),
                const SizedBox(height: 16),
                OutlinedButton(
                  onPressed: () =>
                      ref.invalidate(objectSettingsProvider(objectKey)),
                  child: const Text('Try again'),
                ),
              ],
            ),
          ),
        ),
        data: (data) {
          final summary = data.summary;
          final canWrite = isAdmin && canWriteObject(summary, answer);
          final canShape = isAdmin && canCustomizeObject(summary, answer);
          return RefreshIndicator(
            onRefresh: () =>
                ref.refresh(objectSettingsProvider(objectKey).future),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 32),
              children: [
                if (isAdmin && loadFailed)
                  _LoadFailed(
                    onRetry: () => ref.invalidate(customObjectsProvider),
                  )
                else if (isAdmin && !canShape && !answerState.isLoading)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: Text(
                      objectShapeLockedNotice,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ),
                _Details(
                  summary: summary,
                  boardLabel: boardLabelOf(summary, data.fields),
                  actions: [
                    if (canShape)
                      _action(
                        'Edit object',
                        () => _editObject(context, ref, summary),
                      )
                    else if (canWrite)
                      _action(
                        'Who sees records',
                        () => _chooseAccess(context, ref, summary),
                      ),
                    if (canWrite)
                      _action(
                        'Board field',
                        () => _chooseBoard(context, ref, summary, data.fields),
                      ),
                    if (isAdmin && summary.isActive)
                      _action(
                        'Archive',
                        () => _setActive(context, ref, summary, false),
                        danger: true,
                      ),
                    if (isAdmin && !summary.isActive) ...[
                      _action(
                        'Restore',
                        () => _setActive(context, ref, summary, true),
                      ),
                      _action(
                        'Delete for good',
                        () => _destroy(context, ref, summary),
                        danger: true,
                      ),
                    ],
                  ],
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 22, 16, 8),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          'FIELDS',
                          style: AppTypography.overline.copyWith(
                            color: AppColors.textSecondary,
                            letterSpacing: 1.2,
                          ),
                        ),
                      ),
                      if (canShape)
                        SizedBox(
                          height: 44,
                          child: TextButton.icon(
                            onPressed: () =>
                                _editField(context, ref, null, targets),
                            icon: const Icon(LucideIcons.plus, size: 16),
                            label: const Text('Add field'),
                          ),
                        ),
                    ],
                  ),
                ),
                if (data.fields.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      'No fields yet. Fields are what each record holds, such '
                      'as a price, a date or a link to a contact.',
                      style: AppTypography.body.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ),
                for (final f in data.fields)
                  _FieldRow(
                    field: f,
                    targets: targets,
                    canWrite: canWrite,
                    canShape: canShape,
                    onEdit: () => _editField(context, ref, f, targets),
                    onTurn: () => _turn(context, ref, f, !f.isActive),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }

  _Action _action(String label, VoidCallback onTap, {bool danger = false}) =>
      _Action(label, onTap, danger);
}

class _LoadFailed extends StatelessWidget {
  const _LoadFailed({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
      child: Row(
        children: [
          Expanded(
            child: Text(
              objectsLoadFailedNotice,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
          TextButton(
            onPressed: onRetry,
            style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
            child: const Text('Retry'),
          ),
        ],
      ),
    );
  }
}

class _Action {
  const _Action(this.label, this.onTap, this.danger);

  final String label;
  final VoidCallback onTap;
  final bool danger;
}

class _Details extends StatelessWidget {
  const _Details({
    required this.summary,
    required this.actions,
    required this.boardLabel,
  });

  final ObjectSummary summary;
  final String boardLabel;
  final List<_Action> actions;

  Widget _line(String label, String value) => Padding(
    padding: const EdgeInsets.only(top: 10),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
        Text(value, style: AppTypography.body),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(top: 16),
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _line('Name', '${summary.label} / ${summary.labelPlural}'),
          _line('Key', summary.key),
          _line("Each record's name is called", summary.nameLabel),
          _line(
            'Who can see its records',
            summary.recordAccess == 'everyone'
                ? 'Everyone in this organization'
                : 'Only the people involved',
          ),
          _line('Board field', boardLabel),
          if (summary.description.isNotEmpty)
            _line('Description', summary.description),
          if (!summary.isActive) ...[
            const SizedBox(height: 10),
            StatusBadge(label: 'Archived', color: AppColors.gray500),
          ],
          if (actions.isNotEmpty) ...[
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final a in actions)
                  SizedBox(
                    height: 44,
                    child: OutlinedButton(
                      onPressed: a.onTap,
                      style: a.danger
                          ? OutlinedButton.styleFrom(
                              foregroundColor: AppColors.danger600,
                            )
                          : null,
                      child: Text(a.label),
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _FieldRow extends StatelessWidget {
  const _FieldRow({
    required this.field,
    required this.targets,
    required this.canWrite,
    required this.canShape,
    required this.onEdit,
    required this.onTurn,
  });

  final ObjectField field;
  final List<LinkTarget> targets;
  final bool canWrite;
  final bool canShape;
  final VoidCallback onEdit;
  final VoidCallback onTurn;

  @override
  Widget build(BuildContext context) {
    final type = objectFieldTypeLabels[field.fieldType] ?? field.fieldType;
    final detail = [
      type,
      if (field.isLink)
        '${linkTargetLabel(targets, field.target)}, '
            '${field.many == true ? 'several' : 'one'}',
      if (field.options.isNotEmpty)
        field.options.map((o) => o.label).join(', '),
    ].join(' · ');
    return Container(
      color: AppColors.surface,
      margin: const EdgeInsets.only(bottom: 1),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  field.label,
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w600,
                    color: field.isActive
                        ? AppColors.textPrimary
                        : AppColors.textSecondary,
                  ),
                ),
              ),
              if (!field.isActive)
                StatusBadge(label: 'Turned off', color: AppColors.gray500)
              else if (field.isRequired)
                StatusBadge(label: 'Required', color: AppColors.primary600),
            ],
          ),
          Text(
            field.key,
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
              fontFamily: 'monospace',
            ),
          ),
          const SizedBox(height: 4),
          Text(
            detail,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          if (canWrite) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (canShape)
                  SizedBox(
                    height: 44,
                    child: OutlinedButton(
                      onPressed: onEdit,
                      child: const Text('Edit'),
                    ),
                  ),
                SizedBox(
                  height: 44,
                  child: OutlinedButton(
                    onPressed: onTurn,
                    style: field.isActive
                        ? OutlinedButton.styleFrom(
                            foregroundColor: AppColors.danger600,
                          )
                        : null,
                    child: Text(field.isActive ? 'Turn off' : 'Turn on'),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
