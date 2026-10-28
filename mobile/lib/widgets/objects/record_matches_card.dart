import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme/theme.dart';
import '../../data/models/record_matches.dart';
import '../../providers/custom_objects_provider.dart';
import '../../routes/app_router.dart';

/// The records on the other side that fit this one, with dismiss and undo.
/// A failed load shows a quiet line and never fails the record screen.
class RecordMatchesCard extends ConsumerStatefulWidget {
  const RecordMatchesCard({super.key, required this.recordId});

  final String recordId;

  @override
  ConsumerState<RecordMatchesCard> createState() => _RecordMatchesCardState();
}

class _RecordMatchesCardState extends ConsumerState<RecordMatchesCard> {
  RecordMatches? _answer;
  bool _loading = true;
  bool _failed = false;
  bool _showDismissed = false;
  final Set<String> _busy = {};
  int _loadToken = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(RecordMatchesCard old) {
    super.didUpdateWidget(old);
    if (old.recordId != widget.recordId) {
      _answer = null;
      _showDismissed = false;
      _load();
    }
  }

  Future<void> _load() async {
    final token = ++_loadToken;
    setState(() {
      _loading = true;
      _failed = false;
    });
    final response = await ref
        .read(customObjectsApiProvider)
        .fetchMatches(widget.recordId, dismissed: _showDismissed);
    if (!mounted || token != _loadToken) return;
    final data = response.data;
    final answer = response.success && data != null
        ? RecordMatches.fromJson(data)
        : null;
    // Undoing the last dismissed match leaves the dismissed list empty; the
    // restored one is only in the live list, so fetch that before drawing.
    if (_showDismissed && answer != null && answer.dismissedCount == 0) {
      _showDismissed = false;
      await _load();
      return;
    }
    setState(() {
      _loading = false;
      if (answer != null) {
        _answer = answer;
      } else {
        _failed = true;
      }
    });
  }

  Future<void> _change(MatchRow row, {required bool dismiss}) async {
    if (_busy.contains(row.id)) return;
    setState(() => _busy.add(row.id));
    final api = ref.read(customObjectsApiProvider);
    final response = dismiss
        ? await api.dismissMatch(widget.recordId, row.id)
        : await api.undismissMatch(widget.recordId, row.id);
    if (!mounted) return;
    setState(() => _busy.remove(row.id));
    if (!response.success) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            response.message ??
                (dismiss ? 'Could not dismiss that match' : 'Could not undo'),
          ),
          backgroundColor: AppColors.danger600,
        ),
      );
      return;
    }
    await _load();
  }

  Widget _quiet(String text) => Padding(
    padding: const EdgeInsets.only(bottom: 4),
    child: Text(
      text,
      style: AppTypography.body.copyWith(color: AppColors.textTertiary),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final answer = _answer;
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
          Text(answer?.heading ?? 'Matches', style: AppTypography.h3),
          const SizedBox(height: 10),
          if (answer == null && _loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Center(
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            )
          else if (answer == null)
            _failedRow()
          else ...[
            if (_failed) _failedRow(),
            if (answer.matches.isEmpty)
              _quiet(_showDismissed ? 'None dismissed.' : 'No matches yet.'),
            for (final row in answer.matches) _row(row),
            if (_showDismissed)
              _toggle('Hide dismissed')
            else if (answer.dismissedCount > 0)
              _toggle('${answer.dismissedCount} dismissed', action: 'Show'),
          ],
        ],
      ),
    );
  }

  Widget _failedRow() => Row(
    children: [
      Expanded(child: _quiet('Could not load matches')),
      TextButton(
        style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
        onPressed: _loading ? null : _load,
        child: const Text('Retry'),
      ),
    ],
  );

  Widget _toggle(String text, {String? action}) {
    void flip() {
      setState(() => _showDismissed = !_showDismissed);
      _load();
    }

    final button = TextButton(
      style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
      onPressed: _loading ? null : flip,
      child: Text(action ?? text),
    );
    if (action == null) {
      return Align(alignment: Alignment.centerLeft, child: button);
    }
    return Row(
      children: [
        Expanded(
          child: Text(
            text,
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
        ),
        button,
      ],
    );
  }

  Widget _row(MatchRow row) {
    final answer = _answer!;
    final busy = _busy.contains(row.id);
    final name = row.name.isEmpty ? 'Untitled' : row.name;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: InkWell(
              onTap: answer.objectKey.isEmpty
                  ? null
                  : () => context.push(
                      AppRoutes.objectRecord(answer.objectKey, row.id),
                    ),
              child: ConstrainedBox(
                constraints: const BoxConstraints(minHeight: 44),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(
                      name,
                      style: AppTypography.body.copyWith(
                        color: AppColors.primary600,
                      ),
                    ),
                    for (final f in row.facts)
                      Text(
                        '${f.label}: ${f.value}',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
            onPressed: busy || _loading
                ? null
                : () => _change(row, dismiss: !_showDismissed),
            child: Text(
              _showDismissed ? 'Undo' : 'Dismiss',
              semanticsLabel: '${_showDismissed ? 'Undo' : 'Dismiss'} $name',
            ),
          ),
        ],
      ),
    );
  }
}
