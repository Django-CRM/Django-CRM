import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../providers/duplicates_provider.dart';

/// "Possible duplicates" under a create form's fields, while they are typed.
///
/// Debounced so a normal typing speed asks once per pause, and sequenced so a
/// slow early answer cannot overwrite a later one. The API only ever answers
/// with records the person may open. Saving is never blocked: two people can
/// share a name, and the person typing is the one who knows.
///
/// A hit opens on top of the form, so checking one does not lose what was
/// typed.
class DuplicateNotice extends ConsumerStatefulWidget {
  const DuplicateNotice({
    super.key,
    required this.module,
    required this.fields,
    this.debounce = const Duration(milliseconds: 500),
  });

  final DuplicateModule module;

  /// The form's controllers, keyed by the API's query field name.
  final Map<String, TextEditingController> fields;
  final Duration debounce;

  @override
  ConsumerState<DuplicateNotice> createState() => _DuplicateNoticeState();
}

class _DuplicateNoticeState extends ConsumerState<DuplicateNotice> {
  Timer? _timer;
  int _latest = 0;
  String _lastQuery = '';
  List<DuplicateHit> _hits = const [];

  @override
  void initState() {
    super.initState();
    for (final controller in widget.fields.values) {
      controller.addListener(_schedule);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    for (final controller in widget.fields.values) {
      controller.removeListener(_schedule);
    }
    super.dispose();
  }

  /// Whether a value is worth asking about. The API ignores weaker ones
  /// anyway; this saves a round trip on every early keystroke.
  static bool _usable(String field, String value) => switch (field) {
    'email' => RegExp(r'@.+\.').hasMatch(value),
    'phone' => value.replaceAll(RegExp(r'\D'), '').length >= 7,
    'website' => value.contains('.'),
    _ => value.length >= 2,
  };

  Map<String, String> get _criteria => {
    for (final entry in widget.fields.entries)
      if (_usable(entry.key, entry.value.text.trim()))
        entry.key: entry.value.text.trim(),
  };

  void _schedule() {
    final criteria = _criteria;
    final key = criteria.entries.map((e) => '${e.key}=${e.value}').join('&');
    // A listener fires on focus and selection changes too, not only edits.
    if (key == _lastQuery) return;
    _lastQuery = key;
    _timer?.cancel();
    final ticket = ++_latest;
    if (criteria.isEmpty) {
      if (_hits.isNotEmpty) setState(() => _hits = const []);
      return;
    }
    _timer = Timer(widget.debounce, () async {
      final hits = await ref
          .read(duplicatesApiProvider)
          .check(widget.module, criteria);
      if (!mounted || ticket != _latest) return;
      setState(() => _hits = hits);
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_hits.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 8),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: AppColors.warning50,
        border: Border.all(color: AppColors.warning300),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Semantics(
        liveRegion: true,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  LucideIcons.alertTriangle,
                  size: 16,
                  color: AppColors.warning700,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    _hits.length == 1
                        ? 'Possible duplicate'
                        : 'Possible duplicates',
                    style: AppTypography.label.copyWith(
                      color: AppColors.warning700,
                    ),
                  ),
                ),
              ],
            ),
            for (final hit in _hits)
              InkWell(
                onTap: () => context.push('/${widget.module.path}/${hit.id}'),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 44),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text.rich(
                      TextSpan(
                        children: [
                          TextSpan(
                            text: hit.name,
                            style: AppTypography.bodySmall.copyWith(
                              fontWeight: FontWeight.w600,
                              color: AppColors.warning700,
                              decoration: TextDecoration.underline,
                            ),
                          ),
                          TextSpan(
                            text: '  same ${hit.matchedLabelText}',
                            style: AppTypography.caption.copyWith(
                              color: AppColors.textSecondary,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            Text(
              'You can still save. If it is the same one, open it instead, '
              'or merge the two afterwards.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
