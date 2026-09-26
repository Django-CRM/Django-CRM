import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../providers/saved_views_provider.dart';

/// "Saved views", in the app bar of the six record lists (G29).
///
/// Opens a sheet listing the person's views for [list]: tap one to put its
/// filters back, rename or delete it from its menu, or save the filters the
/// list is showing now. The web's saved-views menu does the same over the
/// same API, so a view saved on one client opens on the other.
///
/// [keys] are the query parameters this screen can show and set, and [multi]
/// the ones it takes several values of. Only those are saved from it, and a
/// view carrying more (the web offers filters this screen has no control for,
/// and several owners where this screen picks one) still opens, with the
/// sheet saying how many values it left out rather than narrowing the list
/// by something invisible.
///
/// A view means what the API means: no ticket `status` is every status, so the
/// Open chip is saved as its three statuses, and the web opens both the same.
class SavedViewsButton extends StatelessWidget {
  const SavedViewsButton({
    super.key,
    required this.list,
    required this.keys,
    this.multi = const {},
    required this.currentQuery,
    required this.onApply,
  });

  final SavedViewList list;
  final Set<String> keys;
  final Set<String> multi;

  /// The list's filters now, as it sends them (`filterQuery`), read at the
  /// moment "Save" is tapped.
  final Future<Map<String, Object?>> Function() currentQuery;

  /// Replace the screen's filters with a view's. Parameters outside [keys]
  /// are ignored by the screen.
  final void Function(Map<String, List<String>> filters) onApply;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: 'Saved views',
      icon: const Icon(LucideIcons.bookmark),
      onPressed: () => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => SavedViewsSheet(
          list: list,
          keys: keys,
          multi: multi,
          currentQuery: currentQuery,
          onApply: (view) {
            onApply(view.filters);
            final left = view.dropped(keys, multi);
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text(
                  left == 0
                      ? 'Showing ${view.name}'
                      : 'Showing ${view.name}. '
                            '${left == 1 ? '1 filter value' : '$left filter values'} '
                            'in it cannot be shown here.',
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class SavedViewsSheet extends ConsumerStatefulWidget {
  const SavedViewsSheet({
    super.key,
    required this.list,
    required this.keys,
    this.multi = const {},
    required this.currentQuery,
    required this.onApply,
  });

  final SavedViewList list;
  final Set<String> keys;
  final Set<String> multi;
  final Future<Map<String, Object?>> Function() currentQuery;
  final void Function(SavedView view) onApply;

  @override
  ConsumerState<SavedViewsSheet> createState() => _SavedViewsSheetState();
}

class _SavedViewsSheetState extends ConsumerState<SavedViewsSheet> {
  bool _busy = false;
  String? _message;

  SavedViewsNotifier get _notifier =>
      ref.read(savedViewsProvider(widget.list).notifier);

  Future<void> _run(Future<String?> Function() write) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    final error = await write();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = error;
    });
  }

  Future<void> _save() async {
    final name = await _askName(context, title: 'Save current filters');
    if (name == null || !mounted) return;
    final filters = savedViewFilters(
      await widget.currentQuery(),
      widget.keys,
      multi: widget.multi,
    );
    await _run(() => _notifier.save(name, filters));
  }

  Future<void> _rename(SavedView view) async {
    final name = await _askName(
      context,
      title: 'Rename view',
      initial: view.name,
    );
    if (name == null || name == view.name || !mounted) return;
    await _run(() => _notifier.rename(view.id, name));
  }

  Future<void> _delete(SavedView view) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete view?'),
        content: Text(
          '${view.name} will be removed. The records it shows are not '
          'touched.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger700),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await _run(() => _notifier.remove(view.id));
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(savedViewsProvider(widget.list));
    final data = async.value;
    final secondary = AppTypography.bodySmall.copyWith(
      color: AppColors.textSecondary,
    );

    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.8,
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
                  LucideIcons.bookmark,
                  size: 18,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Saved views',
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
          if (async.isLoading || _busy)
            const LinearProgressIndicator(minHeight: 2),
          Flexible(
            child: switch (async) {
              AsyncError() when data == null => Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('Could not load your saved views.', style: secondary),
                    TextButton(
                      onPressed: _notifier.reload,
                      child: const Text('Retry'),
                    ),
                  ],
                ),
              ),
              _ when data == null => const SizedBox(height: 48),
              _ when data.views.isEmpty => Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Text(
                  'No saved views yet. Filter the list, then save it here.',
                  style: secondary,
                ),
              ),
              _ => ListView.separated(
                shrinkWrap: true,
                itemCount: data.views.length,
                separatorBuilder: (_, _) =>
                    const Divider(height: 1, color: AppColors.border),
                itemBuilder: (_, i) => _ViewRow(
                  view: data.views[i],
                  dropped: data.views[i].dropped(widget.keys, widget.multi),
                  enabled: !_busy,
                  onApply: () {
                    Navigator.of(context).pop();
                    widget.onApply(data.views[i]);
                  },
                  onRename: () => _rename(data.views[i]),
                  onDelete: () => _delete(data.views[i]),
                ),
              ),
            },
          ),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Text(
                _message!,
                style: AppTypography.bodySmall.copyWith(
                  color: AppColors.danger700,
                ),
              ),
            ),
          Container(
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: AppColors.border)),
            ),
            margin: const EdgeInsets.only(top: 8),
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: data != null && data.full
                ? Text(
                    'This list keeps at most ${data.limit} saved views. '
                    'Delete one to save another.',
                    style: secondary,
                  )
                : FilledButton.icon(
                    style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(
                        AppLayout.buttonHeightLarge,
                      ),
                      shape: RoundedRectangleBorder(
                        borderRadius: AppLayout.borderRadiusMd,
                      ),
                    ),
                    onPressed: _busy || data == null ? null : _save,
                    icon: const Icon(LucideIcons.bookmarkPlus, size: 18),
                    label: const Text('Save current filters'),
                  ),
          ),
        ],
      ),
    );
  }
}

class _ViewRow extends StatelessWidget {
  const _ViewRow({
    required this.view,
    required this.dropped,
    required this.enabled,
    required this.onApply,
    required this.onRename,
    required this.onDelete,
  });

  final SavedView view;
  final int dropped;
  final bool enabled;
  final VoidCallback onApply;
  final VoidCallback onRename;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      minTileHeight: 52,
      enabled: enabled,
      contentPadding: const EdgeInsets.only(left: 16, right: 4),
      title: Text(view.name, style: AppTypography.body),
      subtitle: dropped == 0
          ? null
          : Text(
              '${dropped == 1 ? '1 filter value' : '$dropped filter values'} '
              'this screen cannot show',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
      onTap: onApply,
      trailing: PopupMenuButton<String>(
        tooltip: 'More for ${view.name}',
        enabled: enabled,
        style: IconButton.styleFrom(minimumSize: const Size(48, 48)),
        icon: const Icon(LucideIcons.ellipsisVertical, size: 20),
        onSelected: (choice) => choice == 'rename' ? onRename() : onDelete(),
        itemBuilder: (_) => const [
          PopupMenuItem(value: 'rename', child: Text('Rename')),
          PopupMenuItem(value: 'delete', child: Text('Delete')),
        ],
      ),
    );
  }
}

/// A name for a view: required, at most 100 characters, the API's own limits.
Future<String?> _askName(
  BuildContext context, {
  required String title,
  String initial = '',
}) => showDialog<String>(
  context: context,
  builder: (_) => _NameDialog(title: title, initial: initial),
);

/// Owns its text controller, so the field is not disposed while the dialog
/// is still animating out.
class _NameDialog extends StatefulWidget {
  const _NameDialog({required this.title, required this.initial});

  final String title;
  final String initial;

  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final TextEditingController _controller = TextEditingController(
    text: widget.initial,
  );

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final name = _controller.text.trim();
    if (name.isNotEmpty) Navigator.pop(context, name);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLength: 100,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _submit(),
        decoration: const InputDecoration(hintText: 'View name'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(onPressed: _submit, child: const Text('Save')),
      ],
    );
  }
}
