import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/time_report.dart';
import '../../providers/time_report_provider.dart';

/// Where the time went: totals by agent, ticket or account over a window.
///
/// The timesheet screen is one person's week. This is every window and every
/// grouping of the same entries, and it is what someone opens when the
/// question is about an account's month rather than their own Tuesday.
///
/// Read-only, and no CSV export: the same call the ticket analytics screen
/// made. A phone has nowhere useful to put a spreadsheet, and the web page
/// carries the download.
class TimeReportScreen extends ConsumerWidget {
  const TimeReportScreen({super.key});

  static const _groups = [
    ('agent', 'Agent'),
    ('ticket', 'Ticket'),
    ('account', 'Account'),
  ];

  static const _billableFilters = [
    (null, 'All time'),
    ('true', 'Billable'),
    ('false', 'Non-billable'),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final filters = ref.watch(timeReportFiltersProvider);
    final reportAsync = ref.watch(timeReportProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Time report'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          tooltip: 'Back',
          onPressed: () => context.pop(),
        ),
      ),
      body: Column(
        children: [
          _FilterBar(filters: filters),
          Expanded(
            child: reportAsync.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (error, _) => _ErrorState(
                message: '$error',
                onRetry: () => ref.read(timeReportProvider.notifier).refresh(),
              ),
              data: (report) => RefreshIndicator(
                onRefresh: () =>
                    ref.read(timeReportProvider.notifier).refresh(),
                child: _Body(report: report),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// The window, the grouping and the billable filter.
///
/// Its own bar under the app bar rather than app-bar actions: three controls
/// and a date range do not fit beside a title at 390px, and the range is the
/// label that says which report is on screen.
class _FilterBar extends ConsumerWidget {
  const _FilterBar({required this.filters});

  final TimeReportFilters filters;

  Future<void> _pickWindow(BuildContext context, WidgetRef ref) async {
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2000),
      lastDate: DateTime.now().add(const Duration(days: 1)),
      initialDateRange: DateTimeRange(start: filters.start, end: filters.end),
    );
    if (picked == null) return;
    ref
        .read(timeReportFiltersProvider.notifier)
        .setWindow(picked.start, picked.end);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final label =
        '${DateFormat('d MMM').format(filters.start)} - '
        '${DateFormat('d MMM').format(filters.end)}';

    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 44px tall, which is the smallest a thumb reliably hits.
          SizedBox(
            height: 44,
            child: OutlinedButton.icon(
              onPressed: () => _pickWindow(context, ref),
              icon: const Icon(LucideIcons.calendar, size: 16),
              label: Text(label),
            ),
          ),
          const SizedBox(height: 8),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final (value, label) in TimeReportScreen._groups)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(label),
                      selected: filters.groupBy == value,
                      onSelected: (_) => ref
                          .read(timeReportFiltersProvider.notifier)
                          .setGroupBy(value),
                    ),
                  ),
                Container(
                  width: 1,
                  height: 24,
                  margin: const EdgeInsets.only(right: 8),
                  color: AppColors.border,
                ),
                for (final (value, label) in TimeReportScreen._billableFilters)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(label),
                      selected: filters.billable == value,
                      onSelected: (_) => ref
                          .read(timeReportFiltersProvider.notifier)
                          .setBillable(value),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({required this.report});

  final TimeReport report;

  @override
  Widget build(BuildContext context) {
    return ListView(
      // Always scrollable, so pull-to-refresh works on an empty report too.
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.only(bottom: 96),
      children: [
        _summary(),
        if (report.isEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
            child: Text(
              'No time logged in this window. Widen the dates, or clear the '
              'billable filter.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          )
        else
          ...report.rows.map((row) => _row(context, row)),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
          child: Text(
            'Value is what the billable time is worth at the rate saved on '
            'each entry, so changing a rate does not rewrite what past months '
            'were worth. To export these entries as a spreadsheet, open the '
            'report on the web.',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
              height: 1.5,
            ),
          ),
        ),
      ],
    );
  }

  Widget _summary() {
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
            _hm(report.totalMinutes),
            style: AppTypography.h1.copyWith(fontSize: 28, height: 1.1),
          ),
          const SizedBox(height: 2),
          Text(
            'logged, by ${report.groupBy}',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 16,
            runSpacing: 6,
            children: [
              _stat(
                _hm(report.billableMinutes),
                'billable (${report.billableShare}%)',
              ),
              // One symbol cannot label a sum of two currencies, so a mixed
              // window says which ones it holds instead of picking one.
              if (report.isMixedCurrency)
                _stat(report.currencies.join(', '), 'mixed currencies')
              else if (report.billableValue > 0)
                _stat(
                  NumberFormat.simpleCurrency(
                    name: report.currency,
                  ).format(report.billableValue),
                  'at the saved rates',
                ),
              _stat('${report.entryCount}', 'entries'),
            ],
          ),
        ],
      ),
    );
  }

  /// One figure and its label as a single Text, not a Row: a Row inside the
  /// Wrap cannot shrink and overflows once the system font is scaled up.
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

  /// One grouped row. Tappable through to the ticket or the account it names;
  /// an agent row and the unattributed bucket have nowhere to go.
  Widget _row(BuildContext context, TimeReportRow row) {
    final destination = _destination(row);

    return Container(
      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.border),
      ),
      child: InkWell(
        onTap: destination == null ? null : () => context.push(destination),
        borderRadius: AppLayout.borderRadiusMd,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      row.name,
                      style: AppTypography.labelSmall.copyWith(fontSize: 14),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(_hm(row.totalMinutes), style: AppTypography.labelSmall),
                ],
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 14,
                runSpacing: 4,
                children: [
                  _stat('${row.entryCount}', 'entries'),
                  if (row.billableMinutes > 0)
                    _stat(_hm(row.billableMinutes), 'billable'),
                  if (row.billableValue > 0 && !report.isMixedCurrency)
                    _stat(
                      NumberFormat.simpleCurrency(
                        name: report.currency,
                      ).format(row.billableValue),
                      'worth',
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Interpolated the way every other screen builds a detail path; the
  /// constants in `AppRoutes` carry the `:id` placeholder, not a value.
  String? _destination(TimeReportRow row) {
    if (row.key == null) return null;
    if (report.groupBy == 'ticket') return '/tickets/${row.key}';
    if (report.groupBy == 'account') return '/accounts/${row.key}';
    return null;
  }
}

/// Minutes as "1h 48m". Reports are read in hours, never in minutes.
String _hm(int minutes) {
  final m = minutes < 0 ? 0 : minutes;
  final h = m ~/ 60;
  return h > 0 ? '${h}h ${m % 60}m' : '${m}m';
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
