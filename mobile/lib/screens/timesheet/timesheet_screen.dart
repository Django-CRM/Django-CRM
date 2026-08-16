import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/time_entry.dart';
import '../../data/models/timesheet.dart';
import '../../providers/timesheet_provider.dart';
import '../../routes/app_router.dart';

/// A week of your own logged time.
///
/// The web page draws seven columns side by side, which is the right shape on
/// a desktop and the wrong one on a phone: seven columns at 390px is either a
/// horizontal scroll or seven unreadable slivers. The same data is a vertical
/// list of days here, one card each, and the empty days are still drawn,
/// because an unlogged Wednesday is the thing this screen exists to make
/// visible.
///
/// One write: stopping a running timer. Starting one and logging manual time
/// both belong to a ticket and live on the ticket screen, because the API has
/// nowhere to put an entry with no ticket.
class TimesheetScreen extends ConsumerStatefulWidget {
  const TimesheetScreen({super.key});

  @override
  ConsumerState<TimesheetScreen> createState() => _TimesheetScreenState();
}

class _TimesheetScreenState extends ConsumerState<TimesheetScreen> {
  /// Minutes since this screen last loaded a week, added to the server's
  /// figure for a running timer. The device clock is not the source of truth
  /// for how long somebody has worked; it only measures how long this screen
  /// has been open.
  int _sinceLoad = 0;
  Timer? _ticker;
  DateTime _loadedAt = DateTime.now();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _ticker = Timer.periodic(const Duration(seconds: 30), (_) {
      if (!mounted) return;
      final elapsed = DateTime.now().difference(_loadedAt).inMinutes;
      if (elapsed != _sinceLoad) setState(() => _sinceLoad = elapsed);
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  void _resetTick() {
    _loadedAt = DateTime.now();
    _sinceLoad = 0;
  }

  @override
  Widget build(BuildContext context) {
    final range = ref.watch(timesheetRangeProvider);
    final weekAsync = ref.watch(timesheetProvider);

    // A fresh week means the server's live figures are fresh too, so the
    // locally accumulated minutes start again from zero. Without this a week
    // loaded after an hour on screen would open with an hour already added to
    // every running timer.
    ref.listen(timesheetProvider, (_, next) {
      if (next.hasValue) _resetTick();
    });

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Timesheet'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actions: [
          // This screen is one person's week. The report is every window and
          // every grouping of the same entries.
          IconButton(
            constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
            icon: const Icon(LucideIcons.chartColumn, size: 20),
            tooltip: 'Time report',
            onPressed: () => context.push(AppRoutes.timeReport),
          ),
        ],
      ),
      body: Column(
        children: [
          _weekBar(range),
          Expanded(
            child: weekAsync.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (error, _) => _ErrorState(
                message: '$error',
                onRetry: () => ref.read(timesheetProvider.notifier).refresh(),
              ),
              data: _body,
            ),
          ),
        ],
      ),
    );
  }

  /// Week navigation. Its own row rather than app-bar actions: three controls
  /// plus a date range do not fit beside a title at 390px, and the range is
  /// the label that tells you where you are.
  Widget _weekBar(TimesheetRange range) {
    final label =
        '${DateFormat('d MMM').format(range.start)} - '
        '${DateFormat('d MMM').format(range.end)}';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          IconButton(
            // 44px minimum, which is the smallest a thumb reliably hits.
            constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
            icon: const Icon(LucideIcons.chevronLeft, size: 20),
            tooltip: 'Previous week',
            onPressed: () => _shift(-7),
          ),
          Expanded(
            child: Column(
              children: [
                Text(
                  label,
                  textAlign: TextAlign.center,
                  style: AppTypography.label.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (!range.isCurrent)
                  GestureDetector(
                    onTap: _thisWeek,
                    child: Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        'Back to this week',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.primary600,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
            icon: const Icon(LucideIcons.chevronRight, size: 20),
            tooltip: 'Next week',
            onPressed: () => _shift(7),
          ),
        ],
      ),
    );
  }

  Widget _body(TimesheetWeek week) {
    final total = week.totalMinutesAt(_sinceLoad);
    final billable = week.billableMinutesAt(_sinceLoad);

    return RefreshIndicator(
      onRefresh: () => ref.read(timesheetProvider.notifier).refresh(),
      child: ListView(
        padding: const EdgeInsets.only(bottom: 96),
        children: [
          _summary(week, total, billable),
          ...week.runningEntries.map(_runningCard),
          ...week.days.map(_dayCard),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
            child: Text(
              'Time is logged against a ticket, so every hour here is attached '
              'to something a customer can be shown. Start and stop a timer on '
              'the ticket itself. Rates are saved on each entry when it is '
              'logged, so changing your rate does not rewrite what past weeks '
              'were worth.',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
                height: 1.5,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _summary(TimesheetWeek week, int total, int billable) {
    final unbilled = week.unbilledCount;
    final percent = total > 0 ? ((billable / total) * 100).round() : 0;
    final value = week.billableValueAt(_sinceLoad);
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            _hm(total),
            style: AppTypography.h1.copyWith(fontSize: 28, height: 1.1),
          ),
          const SizedBox(height: 2),
          Text(
            week.profileName.isEmpty
                ? 'logged this week'
                : 'logged this week by ${week.profileName}',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 16,
            runSpacing: 6,
            children: [
              _stat(_hm(billable), 'billable ($percent%)'),
              if (value != null)
                _stat(
                  NumberFormat.simpleCurrency(
                    name: value.currency,
                  ).format(value.amount),
                  'at the saved rates',
                ),
              if (unbilled > 0) _stat('$unbilled', 'not invoiced'),
            ],
          ),
        ],
      ),
    );
  }

  /// One figure and its label as a single Text, not a Row.
  ///
  /// A Row of two Texts inside the summary's Wrap cannot shrink: the Wrap
  /// offers it the full line width and the Row overflows once the system font
  /// is scaled up. Spans wrap.
  Widget _stat(String value, String label) {
    return Text.rich(
      TextSpan(
        children: [
          TextSpan(text: value, style: AppTypography.labelSmall),
          TextSpan(
            text: ' $label',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }

  /// A running timer, at the top, above the days. The one thing on this screen
  /// that changes while you look at it, and the only thing here you can act
  /// on, so it is not buried inside the day it started on.
  Widget _runningCard(TimeEntry entry) {
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.warning50,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.warning200),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(LucideIcons.timer, size: 15, color: AppColors.warning700),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  'Timer running',
                  style: AppTypography.labelSmall.copyWith(
                    color: AppColors.warning700,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                _hm(entry.minutesAt(_sinceLoad)),
                style: AppTypography.labelSmall.copyWith(
                  color: AppColors.warning700,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          GestureDetector(
            onTap: () => _openTicket(entry),
            child: Text(
              entry.caseName ?? 'a ticket',
              style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
            ),
          ),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            height: 44,
            child: FilledButton.icon(
              // Disabled while a stop is in flight. Two taps would send two
              // POSTs, and the second is answered "Timer is already stopped",
              // so a successful stop would read as a failure.
              onPressed: _busy ? null : () => _stop(entry),
              icon: const Icon(LucideIcons.square, size: 16),
              label: const Text('Stop timer'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _dayCard(TimesheetDay day) {
    final minutes = day.totalMinutesAt(_sinceLoad);
    final isToday = DateUtils.isSameDay(day.date, DateTime.now());
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(
          color: isToday ? AppColors.primary200 : AppColors.border,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 8),
            child: Row(
              children: [
                // Expanded, not a Spacer between fixed Texts: "Wednesday"
                // plus a date plus a total is wider than the card once the
                // system font is scaled up, and a Spacer cannot give way.
                Expanded(
                  child: Text.rich(
                    TextSpan(
                      children: [
                        TextSpan(
                          text: DateFormat('EEEE').format(day.date),
                          style: AppTypography.labelSmall.copyWith(
                            color: isToday
                                ? AppColors.primary600
                                : AppColors.textPrimary,
                          ),
                        ),
                        TextSpan(
                          text: '  ${DateFormat('d MMM').format(day.date)}',
                          style: AppTypography.caption.copyWith(
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ],
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (minutes > 0) ...[
                  const SizedBox(width: 8),
                  Text(_hm(minutes), style: AppTypography.labelSmall),
                ],
              ],
            ),
          ),
          if (day.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
              child: Text(
                'Nothing logged',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            )
          else
            ...day.entries.map(_entryRow),
        ],
      ),
    );
  }

  Widget _entryRow(TimeEntry entry) {
    return InkWell(
      onTap: () => _openTicket(entry),
      child: Container(
        constraints: const BoxConstraints(minHeight: 44),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: AppColors.gray100)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Wrap, not Row: "billed INV-0002" beside a duration is wider than
            // the row at a scaled-up font, and the tag is worth a second line
            // rather than a clipped one.
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                  _hm(entry.minutesAt(_sinceLoad)),
                  style: AppTypography.labelSmall,
                ),
                if (entry.isRunning)
                  _tag('running', AppColors.warning700, AppColors.warning50)
                else if (!entry.billable)
                  _tag('internal', AppColors.textSecondary, AppColors.gray100)
                else if (entry.isBilled)
                  // Already on an invoice. Said plainly rather than offered
                  // for billing again: double-billing an hour is a refund.
                  _tag(
                    entry.invoiceNumber == null
                        ? 'billed'
                        : 'billed ${entry.invoiceNumber}',
                    AppColors.success700,
                    AppColors.success50,
                  ),
              ],
            ),
            const SizedBox(height: 3),
            Text(
              entry.caseName ?? 'a ticket',
              style: AppTypography.body,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            if ((entry.description ?? '').isNotEmpty) ...[
              const SizedBox(height: 2),
              Text(
                entry.description!,
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                ),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _tag(String text, Color fg, Color bg) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        text,
        style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600, color: fg),
      ),
    );
  }

  void _shift(int days) =>
      ref.read(timesheetRangeProvider.notifier).shift(days);

  void _thisWeek() => ref.read(timesheetRangeProvider.notifier).thisWeek();

  void _openTicket(TimeEntry entry) {
    if (entry.caseId.isEmpty) return;
    context.push('/tickets/${entry.caseId}');
  }

  Future<void> _stop(TimeEntry entry) async {
    setState(() => _busy = true);
    final response = await ref
        .read(timesheetProvider.notifier)
        .stopTimer(entry.id);
    if (!mounted) return;
    setState(() => _busy = false);
    // Failure is said out loud. A timer that keeps accruing with nothing on
    // screen to say so is the exact problem this screen exists to surface.
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          response.success
              ? 'Timer stopped.'
              : response.message ?? 'Could not stop that timer.',
        ),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  /// Minutes as "1h 48m". Timesheets are read in hours, never in minutes.
  String _hm(int minutes) {
    final m = minutes < 0 ? 0 : minutes;
    final h = m ~/ 60;
    return h > 0 ? '${h}h ${m % 60}m' : '${m}m';
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              LucideIcons.circleAlert,
              size: 36,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: 12),
            Text(
              message,
              textAlign: TextAlign.center,
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ),
      ),
    );
  }
}
