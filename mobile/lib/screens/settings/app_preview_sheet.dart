import 'package:flutter/material.dart';

import '../../core/theme/theme.dart';
import '../../data/models/org_app.dart';
import '../../widgets/common/badge.dart';

/// The admin's go-ahead from a preview, with or without sample records.
typedef AppPreviewChoice = ({bool sampleData});

/// Lists what an install, update or removal would do, grouped by type, and
/// asks before doing it. Resolves to null when the admin backs out.
Future<AppPreviewChoice?> showAppPreviewSheet(
  BuildContext context, {
  required OrgApp app,
  required AppPreview preview,
}) {
  return showModalBottomSheet<AppPreviewChoice>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (_) => _AppPreviewSheet(app: app, preview: preview),
  );
}

class _AppPreviewSheet extends StatefulWidget {
  const _AppPreviewSheet({required this.app, required this.preview});

  final OrgApp app;
  final AppPreview preview;

  @override
  State<_AppPreviewSheet> createState() => _AppPreviewSheetState();
}

class _AppPreviewSheetState extends State<_AppPreviewSheet> {
  late bool _sampleData = widget.preview.sampleDefaultOn;

  AppAction get _action => widget.preview.action;

  bool get _offersSamples =>
      _action == AppAction.install && widget.preview.sampleOffered;

  String get _title => switch (_action) {
    AppAction.install => 'Install ${widget.app.name}?',
    AppAction.upgrade => 'Update ${widget.app.name}?',
    AppAction.remove => 'Remove ${widget.app.name}?',
  };

  String get _lead => switch (_action) {
    AppAction.install =>
      'This is what it adds. Anything this organization already has is '
          'kept as it is, and no record changes.',
    AppAction.upgrade =>
      'This adds what is listed. Nothing you already have changes, and '
          'anything you deleted stays deleted.',
    AppAction.remove =>
      'This turns off what is listed. Every record stays, pipelines and '
          'tags included, and installing again brings it all back.',
  };

  String get _empty => switch (_action) {
    AppAction.install =>
      'This organization already has everything this app sets up.',
    AppAction.upgrade => 'There is nothing new to add.',
    AppAction.remove => 'There is nothing to turn off.',
  };

  String get _confirm => switch (_action) {
    AppAction.install => 'Install',
    AppAction.upgrade => 'Update',
    AppAction.remove => 'Remove',
  };

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final groups = widget.preview.groups;
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: media.size.height * 0.9),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Flexible(
              child: ListView(
                shrinkWrap: true,
                padding: const EdgeInsets.fromLTRB(16, 20, 16, 8),
                children: [
                  Text(_title, style: AppTypography.h3),
                  const SizedBox(height: 8),
                  Text(
                    _lead,
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                  if (widget.preview.hasHandWork) ...[
                    const SizedBox(height: 12),
                    Text(
                      'Stages marked Add by hand are new in this version, but '
                      'their pipeline is already here, so the update leaves '
                      'them out. Add them to that pipeline yourself if you '
                      'want them.',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.warning700,
                      ),
                    ),
                  ],
                  if (groups.isEmpty) ...[
                    const SizedBox(height: 16),
                    Text(_empty, style: AppTypography.body),
                  ],
                  for (final group in groups) ...[
                    const SizedBox(height: 20),
                    Text(
                      group.title.toUpperCase(),
                      style: AppTypography.overline.copyWith(
                        color: AppColors.textSecondary,
                        letterSpacing: 1.2,
                      ),
                    ),
                    for (final item in group.items) _ItemRow(item: item),
                  ],
                  if (_offersSamples) ...[
                    const SizedBox(height: 12),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: _sampleData,
                      onChanged: (on) => setState(() => _sampleData = on),
                      title: const Text('Add sample records'),
                      subtitle: Text(
                        widget.preview.sampleRecords > 0
                            ? '${widget.preview.sampleRecords} example '
                                  'records to try it with. Clear sample data '
                                  'removes them.'
                            : 'Example records to try it with. Clear sample '
                                  'data removes them.',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: Row(
                children: [
                  Expanded(
                    child: SizedBox(
                      height: 46,
                      child: OutlinedButton(
                        onPressed: () => Navigator.of(context).pop(),
                        child: const Text('Cancel'),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: SizedBox(
                      height: 46,
                      child: FilledButton(
                        style: _action == AppAction.remove
                            ? FilledButton.styleFrom(
                                backgroundColor: AppColors.danger600,
                              )
                            : null,
                        onPressed: () => Navigator.of(
                          context,
                        ).pop((sampleData: _offersSamples && _sampleData)),
                        child: Text(_confirm),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ItemRow extends StatelessWidget {
  const _ItemRow({required this.item});

  final AppPreviewItem item;

  Color get _tone => switch (item.effect) {
    'add' || 'turn_on' || 'restore' => AppColors.success600,
    'turn_off' ||
    'skipped_module' ||
    'add_by_hand' ||
    'conflict' => AppColors.warning700,
    _ => AppColors.gray500,
  };

  @override
  Widget build(BuildContext context) {
    final target = item.target;
    final note = appEffectNote(item.effect);
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item.label, style: AppTypography.body),
                if (note != null)
                  Text(
                    note,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.warning700,
                    ),
                  ),
                if (target != null)
                  Text(
                    appTargetLabel(target),
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          StatusBadge(label: appEffectLabel(item.effect), color: _tone),
        ],
      ),
    );
  }
}
