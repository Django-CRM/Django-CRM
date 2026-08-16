import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/time_entry.dart';
import '../../providers/tickets_provider.dart';

/// Time tracking panel: start/stop timer, manual entry, recent entries.
///
/// Self-contained, owns its own loading state and refreshes on demand. The
/// parent passes the ticket id and (optionally) the embedded `TimeSummary`
/// from the case envelope so the totals render before this widget's own
/// fetch resolves.
class TicketTimePanel extends ConsumerStatefulWidget {
  final String ticketId;
  final TimeSummary? initialSummary;

  const TicketTimePanel({
    super.key,
    required this.ticketId,
    this.initialSummary,
  });

  @override
  ConsumerState<TicketTimePanel> createState() => _TicketTimePanelState();
}

class _TicketTimePanelState extends ConsumerState<TicketTimePanel> {
  List<TimeEntry> _entries = const [];
  TimeSummary? _summary;
  bool _isBusy = false;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _summary = widget.initialSummary;
    _load();
    // 30s ticker so the running-timer duration updates without rebuilding
    // the whole detail page.
    _tick = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted && _entries.any((e) => e.isRunning)) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final notifier = ref.read(ticketsProvider.notifier);
    final results = await Future.wait([
      notifier.fetchTimeEntries(widget.ticketId),
      notifier.fetchTimeSummary(widget.ticketId),
    ]);
    if (!mounted) return;
    setState(() {
      _entries = results[0] as List<TimeEntry>;
      _summary = results[1] as TimeSummary? ?? _summary;
    });
  }

  TimeEntry? get _running =>
      _entries.where((e) => e.isRunning).cast<TimeEntry?>().firstOrNull;

  Future<void> _startTimer() async {
    setState(() => _isBusy = true);
    final res = await ref
        .read(ticketsProvider.notifier)
        .startTimer(widget.ticketId);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
      return;
    }
    // 409: user has a running timer on another ticket. The backend includes
    // `running_case_id` so we can offer a one-tap jump to stop it there.
    final otherCaseId = res.data?['running_case_id']?.toString();
    if (res.statusCode == 409 &&
        otherCaseId != null &&
        otherCaseId.isNotEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text(
            'You already have a running timer on another ticket.',
          ),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 6),
          action: SnackBarAction(
            label: 'View',
            textColor: Colors.white,
            onPressed: () => context.push('/tickets/$otherCaseId'),
          ),
        ),
      );
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(res.message ?? 'Failed to start timer'),
        backgroundColor: AppColors.danger600,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _stopTimer(TimeEntry entry) async {
    setState(() => _isBusy = true);
    final res = await ref.read(ticketsProvider.notifier).stopTimer(entry.id);
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to stop timer'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  Future<void> _addManual() async {
    final result = await showModalBottomSheet<_ManualEntryResult>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
        child: const _ManualEntrySheet(),
      ),
    );
    if (result == null || !mounted) return;
    setState(() => _isBusy = true);
    final res = await ref
        .read(ticketsProvider.notifier)
        .addManualTimeEntry(
          widget.ticketId,
          startedAt: result.startedAt,
          endedAt: result.endedAt,
          billable: result.billable,
          description: result.description,
        );
    if (!mounted) return;
    setState(() => _isBusy = false);
    if (res.success) {
      await _load();
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? 'Failed to add entry'),
          backgroundColor: AppColors.danger600,
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final running = _running;
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
              Icon(LucideIcons.clock, size: 16, color: AppColors.gray600),
              const SizedBox(width: 8),
              Text(
                'TIME TRACKING',
                style: AppTypography.overline.copyWith(
                  color: AppColors.textSecondary,
                ),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'Add manual entry',
                icon: const Icon(LucideIcons.plus, size: 18),
                onPressed: _isBusy ? null : _addManual,
              ),
            ],
          ),
          if (_summary != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                _StatTile(
                  label: 'Total',
                  value: _formatMinutes(_summary!.totalMinutes),
                ),
                const SizedBox(width: 12),
                _StatTile(
                  label: 'Billable',
                  value: _formatMinutes(_summary!.billableMinutes),
                  color: AppColors.success600,
                ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          if (running != null)
            _RunningTimerCard(
              entry: running,
              isBusy: _isBusy,
              onStop: () => _stopTimer(running),
            )
          else
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _isBusy ? null : _startTimer,
                icon: const Icon(LucideIcons.play, size: 16),
                label: const Text('Start timer'),
              ),
            ),
          if (_entries.isNotEmpty) ...[
            const Divider(height: 24),
            ..._entries
                .where((e) => !e.isRunning)
                .take(5)
                .map((e) => _EntryRow(entry: e)),
            if (_entries.where((e) => !e.isRunning).length > 5)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '+ ${_entries.where((e) => !e.isRunning).length - 5} more',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.textTertiary,
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

String _formatMinutes(int minutes) {
  if (minutes <= 0) return '0m';
  final h = minutes ~/ 60;
  final m = minutes % 60;
  if (h == 0) return '${m}m';
  if (m == 0) return '${h}h';
  return '${h}h ${m}m';
}

class _StatTile extends StatelessWidget {
  final String label;
  final String value;
  final Color? color;
  const _StatTile({required this.label, required this.value, this.color});

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: AppColors.gray50,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: AppColors.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              value,
              style: AppTypography.h3.copyWith(
                color: color ?? AppColors.textPrimary,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RunningTimerCard extends StatelessWidget {
  final TimeEntry entry;
  final bool isBusy;
  final VoidCallback onStop;

  const _RunningTimerCard({
    required this.entry,
    required this.isBusy,
    required this.onStop,
  });

  @override
  Widget build(BuildContext context) {
    final d = entry.liveDuration;
    final running =
        '${d.inHours.toString().padLeft(2, '0')}:'
        '${(d.inMinutes % 60).toString().padLeft(2, '0')}:'
        '${(d.inSeconds % 60).toString().padLeft(2, '0')}';
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.primary50,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.primary200),
      ),
      child: Row(
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              color: AppColors.primary600,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Running',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.primary700,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                Text(
                  running,
                  style: AppTypography.h3.copyWith(
                    fontFeatures: const [FontFeature.tabularFigures()],
                    color: AppColors.primary700,
                  ),
                ),
              ],
            ),
          ),
          OutlinedButton.icon(
            onPressed: isBusy ? null : onStop,
            icon: const Icon(LucideIcons.square, size: 14),
            label: const Text('Stop'),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.danger600,
              side: BorderSide(color: AppColors.danger300),
              // Override the global theme's `minimumSize: Size.fromHeight(..)`
              // which is `Size(∞, X)`. Needed so this in-row button doesn't
              // try to layout at infinite width when there's no Expanded parent.
              minimumSize: const Size(0, 36),
            ),
          ),
        ],
      ),
    );
  }
}

class _EntryRow extends StatelessWidget {
  final TimeEntry entry;
  const _EntryRow({required this.entry});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          if (entry.billable)
            Icon(LucideIcons.dollarSign, size: 14, color: AppColors.success600)
          else
            Icon(LucideIcons.minus, size: 14, color: AppColors.gray400),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  entry.description?.isNotEmpty == true
                      ? entry.description!
                      : (entry.profileName ?? 'Time entry'),
                  style: AppTypography.body.copyWith(
                    fontWeight: FontWeight.w500,
                  ),
                ),
                if (entry.description?.isNotEmpty == true &&
                    entry.profileName != null)
                  Text(
                    entry.profileName!,
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
                  ),
              ],
            ),
          ),
          Text(
            _formatMinutes(entry.durationMinutes ?? 0),
            style: AppTypography.label.copyWith(
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }
}

class _ManualEntryResult {
  final DateTime startedAt;
  final DateTime endedAt;
  final bool billable;
  final String description;
  const _ManualEntryResult({
    required this.startedAt,
    required this.endedAt,
    required this.billable,
    required this.description,
  });
}

class _ManualEntrySheet extends StatefulWidget {
  const _ManualEntrySheet();

  @override
  State<_ManualEntrySheet> createState() => _ManualEntrySheetState();
}

class _ManualEntrySheetState extends State<_ManualEntrySheet> {
  final _descController = TextEditingController();
  int _minutes = 30;
  bool _billable = false;

  @override
  void dispose() {
    _descController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.gray300,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 16),
            Text('Add time entry', style: AppTypography.h3),
            const SizedBox(height: 16),
            Text(
              'Duration',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [15, 30, 45, 60, 90, 120].map((m) {
                final sel = _minutes == m;
                return ChoiceChip(
                  label: Text(_formatMinutes(m)),
                  selected: sel,
                  onSelected: (_) => setState(() => _minutes = m),
                );
              }).toList(),
            ),
            const SizedBox(height: 16),
            // Required, not optional: `TimeEntryCreateSerializer` rejects a
            // blank description on a manual entry. The hint used to say
            // optional, so leaving it empty got a 400 and a "Failed to add
            // entry" snackbar with nothing pointing at the empty box.
            TextField(
              controller: _descController,
              maxLines: 2,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: 'What did you work on?',
                filled: true,
                fillColor: AppColors.gray50,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide(color: AppColors.border),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Checkbox(
                  value: _billable,
                  onChanged: (v) => setState(() => _billable = v ?? false),
                ),
                const Text('Billable'),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Cancel'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    // Disabled until there is something to send, so the
                    // refusal happens under the empty field rather than as a
                    // snackbar after a round trip.
                    onPressed: _descController.text.trim().isEmpty
                        ? null
                        : () {
                            final end = DateTime.now();
                            final start = end.subtract(
                              Duration(minutes: _minutes),
                            );
                            Navigator.pop(
                              context,
                              _ManualEntryResult(
                                startedAt: start,
                                endedAt: end,
                                billable: _billable,
                                description: _descController.text.trim(),
                              ),
                            );
                          },
                    child: const Text('Add entry'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
