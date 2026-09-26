import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/solution.dart';
import '../../providers/solutions_provider.dart';
import '../common/common.dart';

/// Linked + suggested solutions for a ticket. Tap a solution to view it,
/// tap "Link" to attach an existing one (picker), tap "x" to unlink.
class TicketSolutionsPanel extends ConsumerStatefulWidget {
  final String ticketId;
  final List<Solution> linked;
  final VoidCallback onChanged;

  /// Whether this person may link and unlink articles: the ticket's write
  /// rule, reported as `comment_permission`. The API answers anyone else 403.
  final bool canEdit;

  const TicketSolutionsPanel({
    super.key,
    required this.ticketId,
    required this.linked,
    required this.onChanged,
    required this.canEdit,
  });

  @override
  ConsumerState<TicketSolutionsPanel> createState() =>
      _TicketSolutionsPanelState();
}

class _TicketSolutionsPanelState extends ConsumerState<TicketSolutionsPanel> {
  List<Solution> _suggestions = const [];
  bool _isBusy = false;

  @override
  void initState() {
    super.initState();
    _loadSuggestions();
  }

  Future<void> _loadSuggestions() async {
    final s = await ref
        .read(solutionsProvider.notifier)
        .suggestionsFor(widget.ticketId);
    if (!mounted) return;
    setState(() => _suggestions = s);
  }

  Future<void> _link(Solution s) async {
    setState(() => _isBusy = true);
    final ok = await ref
        .read(solutionsProvider.notifier)
        .linkToTicket(widget.ticketId, s.id);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (ok) {
      widget.onChanged();
      await _loadSuggestions();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Failed to link solution'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _unlink(Solution s) async {
    setState(() => _isBusy = true);
    final ok = await ref
        .read(solutionsProvider.notifier)
        .unlinkFromTicket(widget.ticketId, s.id);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (ok) {
      widget.onChanged();
      await _loadSuggestions();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Failed to unlink solution'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _openPicker() async {
    final allState = ref.read(solutionsProvider);
    // Load if empty.
    if (allState.solutions.isEmpty && !allState.isLoading) {
      await ref.read(solutionsProvider.notifier).refresh();
    }
    if (!mounted) return;
    final linkedIds = widget.linked.map((s) => s.id).toSet();
    final candidates = ref
        .read(solutionsProvider)
        .solutions
        .where((s) => !linkedIds.contains(s.id))
        .toList();
    final picked = await showModalBottomSheet<Solution>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.7,
        minChildSize: 0.4,
        maxChildSize: 0.95,
        expand: false,
        builder: (ctx, controller) =>
            _SolutionPickerSheet(solutions: candidates, controller: controller),
      ),
    );
    if (picked != null) await _link(picked);
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(LucideIcons.bookOpen, size: 16, color: AppColors.gray600),
              const SizedBox(width: 8),
              Text(
                'SOLUTIONS',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              const Spacer(),
              if (widget.canEdit)
                IconButton(
                  tooltip: 'Link a solution',
                  icon: const Icon(LucideIcons.plus, size: 18),
                  onPressed: _isBusy ? null : _openPicker,
                ),
            ],
          ),
          if (widget.linked.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                'No solutions linked yet.',
                style: AppTypography.body.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
            )
          else
            ...widget.linked.map(
              (s) => _LinkedSolutionRow(
                solution: s,
                onUnlink: widget.canEdit ? () => _unlink(s) : null,
                isBusy: _isBusy,
              ),
            ),
          if (widget.canEdit && _suggestions.isNotEmpty) ...[
            const Divider(height: 24),
            Text(
              'Suggested',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            ..._suggestions
                .where((s) => !widget.linked.map((l) => l.id).contains(s.id))
                .take(3)
                .map(
                  (s) => _SuggestionRow(
                    solution: s,
                    onLink: () => _link(s),
                    isBusy: _isBusy,
                  ),
                ),
          ],
        ],
      ),
    );
  }
}

class _LinkedSolutionRow extends StatelessWidget {
  final Solution solution;

  /// Null when this person may not change the ticket: no Unlink button.
  final VoidCallback? onUnlink;
  final bool isBusy;

  const _LinkedSolutionRow({
    required this.solution,
    required this.onUnlink,
    required this.isBusy,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => context.push('/solutions/${solution.id}'),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Icon(LucideIcons.fileText, size: 14, color: AppColors.gray500),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                solution.title,
                style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            StatusBadge(
              label: solution.status.label,
              color: solution.status.color,
            ),
            if (onUnlink != null)
              IconButton(
                tooltip: 'Unlink',
                icon: const Icon(LucideIcons.x, size: 16),
                color: AppColors.gray500,
                onPressed: isBusy ? null : onUnlink,
              ),
          ],
        ),
      ),
    );
  }
}

class _SuggestionRow extends StatelessWidget {
  final Solution solution;
  final VoidCallback onLink;
  final bool isBusy;

  const _SuggestionRow({
    required this.solution,
    required this.onLink,
    required this.isBusy,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(LucideIcons.lightbulb, size: 14, color: AppColors.warning500),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              solution.title,
              style: AppTypography.body,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          TextButton(
            // Themed buttons carry an infinite minimum width, which a Row
            // does not bound. Without this the row fails to lay out and the
            // screen paints nothing. See AppLayout.buttonMinSizeInRow.
            style: TextButton.styleFrom(
              minimumSize: AppLayout.buttonMinSizeInRow,
            ),
            onPressed: isBusy ? null : onLink,
            child: const Text('Link'),
          ),
        ],
      ),
    );
  }
}

class _SolutionPickerSheet extends StatefulWidget {
  final List<Solution> solutions;
  final ScrollController controller;
  const _SolutionPickerSheet({
    required this.solutions,
    required this.controller,
  });

  @override
  State<_SolutionPickerSheet> createState() => _SolutionPickerSheetState();
}

class _SolutionPickerSheetState extends State<_SolutionPickerSheet> {
  String _q = '';

  @override
  Widget build(BuildContext context) {
    final filtered = _q.isEmpty
        ? widget.solutions
        : widget.solutions
              .where(
                (s) =>
                    s.title.toLowerCase().contains(_q.toLowerCase()) ||
                    s.description.toLowerCase().contains(_q.toLowerCase()),
              )
              .toList();
    return Column(
      children: [
        Container(
          margin: const EdgeInsets.only(top: 12),
          width: 40,
          height: 4,
          decoration: BoxDecoration(
            color: AppColors.gray300,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Text('Link solution', style: AppTypography.h3),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: TextField(
            decoration: InputDecoration(
              hintText: 'Search solutions…',
              prefixIcon: const Icon(LucideIcons.search, size: 18),
              filled: true,
              fillColor: AppColors.gray50,
              contentPadding: const EdgeInsets.symmetric(vertical: 8),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: AppColors.border),
              ),
            ),
            onChanged: (v) => setState(() => _q = v),
          ),
        ),
        Expanded(
          child: filtered.isEmpty
              ? Center(
                  child: Text(
                    'No matching solutions',
                    style: AppTypography.body.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                )
              : ListView.builder(
                  controller: widget.controller,
                  itemCount: filtered.length,
                  itemBuilder: (_, i) {
                    final s = filtered[i];
                    return InkWell(
                      onTap: () => Navigator.pop(context, s),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 12,
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    s.title,
                                    style: AppTypography.body.copyWith(
                                      fontWeight: FontWeight.w500,
                                    ),
                                  ),
                                  if (s.description.isNotEmpty)
                                    Text(
                                      s.description,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: AppTypography.caption.copyWith(
                                        color: AppColors.textSecondary,
                                      ),
                                    ),
                                ],
                              ),
                            ),
                            StatusBadge(
                              label: s.status.label,
                              color: s.status.color,
                            ),
                          ],
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
