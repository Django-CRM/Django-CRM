import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/custom_object.dart';
import '../../providers/custom_objects_provider.dart';
import '../../routes/app_router.dart';
import 'link_field_picker.dart';

/// The custom records that link to a built-in record, on its detail screen.
/// Hidden unless the org has custom objects; the server decides what each
/// person may read and change, and the app offers writes only as it says.
class RelatedObjectsSection extends ConsumerWidget {
  const RelatedObjectsSection({
    super.key,
    required this.to,
    required this.recordId,
  });

  final String to;
  final String recordId;

  void _refusal(BuildContext context, String? message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message ?? 'Could not change this link'),
        backgroundColor: AppColors.danger600,
      ),
    );
  }

  Future<void> _add(
    BuildContext context,
    WidgetRef ref,
    List<RelatedLinkField> linkFields,
  ) async {
    final field = await showModalBottomSheet<RelatedLinkField>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (sheet) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          padding: const EdgeInsets.symmetric(vertical: 12),
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
              child: Text('Add to', style: AppTypography.h3),
            ),
            for (final f in linkFields)
              ListTile(
                minTileHeight: 48,
                title: Text('${f.objectLabel}: ${f.fieldLabel}'),
                onTap: () => Navigator.pop(sheet, f),
              ),
          ],
        ),
      ),
    );
    if (field == null || !context.mounted) return;
    final picked = await showLinkSearchSheet(
      context,
      title: '${field.objectLabel}: ${field.fieldLabel}',
      target: 'obj:${field.objectKey}',
    );
    if (picked == null || !context.mounted) return;
    final response = await ref
        .read(customObjectsApiProvider)
        .addRelated(to, recordId, picked.id, field.fieldKey);
    if (!context.mounted) return;
    if (response.success) {
      ref.invalidate(relatedProvider((to: to, id: recordId)));
    } else {
      _refusal(context, response.message);
    }
  }

  Future<void> _remove(
    BuildContext context,
    WidgetRef ref,
    RelatedGroup group,
    RelatedRecordRef record,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text('Remove from ${group.objectLabel}: ${group.fieldLabel}?'),
        content: Text(
          '${record.name.isEmpty ? 'This record' : record.name} will no '
          'longer link here.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final response = await ref
        .read(customObjectsApiProvider)
        .removeRelated(to, recordId, record.id, group.fieldKey);
    if (!context.mounted) return;
    if (response.success) {
      ref.invalidate(relatedProvider((to: to, id: recordId)));
    } else {
      _refusal(context, response.message);
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final answer = ref.watch(relatedProvider((to: to, id: recordId))).value;
    if (answer == null || answer.showsNothing) return const SizedBox.shrink();
    final objects =
        shownCustomObjects(ref.watch(customObjectsProvider))?.objects ??
        const <ObjectSummary>[];
    bool writable(String key) =>
        objects.where((o) => o.key == key).every((o) => o.writable != false);
    final linkFields = [
      for (final f in answer.linkFields)
        if (writable(f.objectKey)) f,
    ];

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.gray200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Related', style: AppTypography.h3),
          for (final group in answer.groups) ...[
            const SizedBox(height: 12),
            Text(
              '${group.objectLabel}: ${group.fieldLabel}',
              style: AppTypography.label.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            for (final record in group.records)
              _row(
                context,
                ref,
                answer,
                group,
                record,
                writable(group.objectKey),
              ),
          ],
          if (linkFields.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: TextButton.icon(
                style: TextButton.styleFrom(
                  minimumSize: const Size(44, 44),
                  alignment: Alignment.centerLeft,
                ),
                onPressed: () => _add(context, ref, linkFields),
                icon: const Icon(LucideIcons.plus, size: 16),
                label: const Text('Add to'),
              ),
            ),
        ],
      ),
    );
  }

  Widget _row(
    BuildContext context,
    WidgetRef ref,
    RelatedAnswer answer,
    RelatedGroup group,
    RelatedRecordRef record,
    bool writable,
  ) {
    final name = record.name.isEmpty ? 'Untitled' : record.name;
    return Row(
      children: [
        Expanded(
          child: InkWell(
            onTap: () => context.push(
              AppRoutes.objectRecord(group.objectKey, record.id),
            ),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 44),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  name,
                  style: AppTypography.body.copyWith(
                    color: AppColors.primary600,
                  ),
                ),
              ),
            ),
          ),
        ),
        if (writable && answer.removable(group))
          IconButton(
            tooltip: 'Remove ${record.name}',
            constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
            icon: const Icon(LucideIcons.x, size: 18),
            onPressed: () => _remove(context, ref, group, record),
          ),
      ],
    );
  }
}
