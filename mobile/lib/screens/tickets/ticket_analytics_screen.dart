import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../../core/theme/theme.dart';
import '../../data/models/ticket.dart';
import '../../providers/analytics_provider.dart';

/// Analytics dashboard for tickets (Tier 2). Read-only, no CSV export on
/// mobile; users go to the web for that.
class TicketAnalyticsScreen extends ConsumerStatefulWidget {
  const TicketAnalyticsScreen({super.key});

  @override
  ConsumerState<TicketAnalyticsScreen> createState() =>
      _TicketAnalyticsScreenState();
}

class _TicketAnalyticsScreenState extends ConsumerState<TicketAnalyticsScreen> {
  DateTimeRange _range = DateTimeRange(
    start: DateTime.now().subtract(const Duration(days: 30)),
    end: DateTime.now(),
  );
  TicketPriority? _priority;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _apply());
  }

  void _apply() {
    ref
        .read(analyticsProvider.notifier)
        .setQuery(
          AnalyticsQuery(
            from: _range.start,
            to: _range.end,
            priority: _priority?.value,
          ),
        );
  }

  Future<void> _pickRange() async {
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2000),
      lastDate: DateTime.now().add(const Duration(days: 1)),
      initialDateRange: _range,
    );
    if (picked != null) {
      setState(() => _range = picked);
      _apply();
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = ref.watch(analyticsProvider);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      appBar: AppBar(
        title: const Text('Analytics'),
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        leading: IconButton(
          icon: const Icon(LucideIcons.chevronLeft),
          onPressed: () => context.pop(),
        ),
      ),
      body: Column(
        children: [
          _filterBar(),
          Expanded(child: _body(data)),
        ],
      ),
    );
  }

  Widget _filterBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          GestureDetector(
            onTap: _pickRange,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: AppColors.gray100,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Icon(LucideIcons.calendar, size: 16),
                  const SizedBox(width: 8),
                  // Expanded so the range wraps at a large system font
                  // instead of overflowing the bar.
                  Expanded(
                    child: Text(
                      '${_fmt(_range.start)} to ${_fmt(_range.end)}',
                      style: AppTypography.body,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final p in [null, ...TicketPriority.values])
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      label: Text(p == null ? 'All priorities' : p.label),
                      selected: _priority == p,
                      onSelected: (_) {
                        setState(() => _priority = p);
                        _apply();
                      },
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _fmt(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Widget _body(AnalyticsDashboard data) {
    if (data.isLoading && data.frt == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (data.error != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(LucideIcons.alertCircle, size: 48, color: Colors.grey[400]),
            const SizedBox(height: 16),
            Text(data.error!, style: AppTypography.body),
            const SizedBox(height: 16),
            TextButton(onPressed: _apply, child: const Text('Retry')),
          ],
        ),
      );
    }

    final frt = data.frt;
    final nrt = data.nrt;
    final mttr = data.mttr;
    final backlog = data.backlog;
    final sla = data.sla;

    return RefreshIndicator(
      onRefresh: () async => _apply(),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 80),
        children: [
          // Rows of two that grow to their content, not a GridView with a
          // fixed aspect ratio: a tile's height is its text, and at a large
          // system font a fixed ratio clips the subtitle or overflows.
          _TileRow(
            children: [
              _MetricTile(
                title: 'First Response',
                value: _hours(frt?['median_hours']),
                subtitle:
                    'p90 ${_hours(frt?['p90_hours'])} · ${frt?['count'] ?? 0} tickets',
                breachLabel: '${frt?['breach_count'] ?? 0} breached',
                icon: LucideIcons.zap,
                color: AppColors.primary600,
              ),
              _MetricTile(
                title: 'Next Response',
                value: _hours(nrt?['median_hours']),
                subtitle:
                    'p90 ${_hours(nrt?['p90_hours'])} · ${nrt?['count'] ?? 0} answered',
                breachLabel: '${nrt?['breach_count'] ?? 0} breached',
                icon: LucideIcons.messageSquareReply,
                color: AppColors.teal600,
              ),
            ],
          ),
          const SizedBox(height: 12),
          _TileRow(
            children: [
              _MetricTile(
                title: 'Resolution',
                value: _hours(mttr?['median_hours']),
                subtitle:
                    'p90 ${_hours(mttr?['p90_hours'])} · ${mttr?['count'] ?? 0} resolved',
                icon: LucideIcons.checkCircle,
                color: AppColors.success600,
              ),
              _MetricTile(
                title: 'Backlog (today)',
                value: _backlogCurrent(backlog).toString(),
                subtitle:
                    'Peak urgent ${_backlogPeakUrgent(backlog)} in window',
                icon: LucideIcons.inbox,
                color: AppColors.warning600,
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Full width: three rates, and the headline one is first response.
          _MetricTile(
            title: 'SLA breach rate',
            value: _rate(sla?['frt_breach_rate']),
            subtitle:
                'First response above · Resolution '
                '${_rate(sla?['resolution_breach_rate'])} · Next response '
                '${_rate(sla?['nrt_breach_rate'])}',
            icon: LucideIcons.alertTriangle,
            color: AppColors.danger600,
          ),
          const SizedBox(height: 16),
          _CsatSection(csat: data.csat),
          const SizedBox(height: 16),
          _AgentsSection(agents: data.agents),
        ],
      ),
    );
  }

  String _hours(dynamic v) {
    if (v == null) return '—';
    final d = (v as num).toDouble();
    if (d < 1) return '${(d * 60).round()}m';
    if (d < 10) return '${d.toStringAsFixed(1)}h';
    return '${d.round()}h';
  }

  String _rate(dynamic v) {
    if (v == null) return '—';
    final d = (v as num).toDouble();
    return '${(d * 100).toStringAsFixed(1)}%';
  }

  int _backlogCurrent(Map<String, dynamic>? backlog) {
    if (backlog == null) return 0;
    final series = backlog['series'] as List<dynamic>? ?? const [];
    if (series.isEmpty) return 0;
    final last = series.last as Map<String, dynamic>;
    return last['open_count'] as int? ?? 0;
  }

  int _backlogPeakUrgent(Map<String, dynamic>? backlog) {
    if (backlog == null) return 0;
    final series = backlog['series'] as List<dynamic>? ?? const [];
    int peak = 0;
    for (final p in series) {
      if (p is Map<String, dynamic>) {
        final u = p['urgent_count'] as int? ?? 0;
        if (u > peak) peak = u;
      }
    }
    return peak;
  }
}

/// Two tiles side by side, as tall as the taller one's content.
class _TileRow extends StatelessWidget {
  final List<Widget> children;
  const _TileRow({required this.children});

  @override
  Widget build(BuildContext context) {
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const SizedBox(width: 12),
            Expanded(child: children[i]),
          ],
        ],
      ),
    );
  }
}

class _MetricTile extends StatelessWidget {
  final String title;
  final String value;
  final String subtitle;
  final String? breachLabel;
  final IconData icon;
  final Color color;

  const _MetricTile({
    required this.title,
    required this.value,
    required this.subtitle,
    required this.icon,
    required this.color,
    this.breachLabel,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
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
              Icon(icon, size: 16, color: color),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  title.toUpperCase(),
                  style: AppTypography.overline.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: AppTypography.h2.copyWith(
              color: color,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            subtitle,
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          if (breachLabel != null) ...[
            const SizedBox(height: 4),
            Text(
              breachLabel!,
              style: AppTypography.caption.copyWith(
                color: AppColors.danger600,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Customer satisfaction for the window: the average, how many answered, and
/// how the answers spread over 1 to 5.
class _CsatSection extends StatelessWidget {
  final Map<String, dynamic>? csat;
  const _CsatSection({required this.csat});

  @override
  Widget build(BuildContext context) {
    final count = csat?['count'] as int? ?? 0;
    final average = csat?['average'] as num?;
    final distribution = csat?['distribution'] is Map
        ? csat!['distribution'] as Map
        : const {};
    return Container(
      padding: const EdgeInsets.all(14),
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
              Icon(LucideIcons.smile, size: 16, color: AppColors.warning600),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  'CUSTOMER SATISFACTION',
                  style: AppTypography.overline.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // The backend sends a null average exactly when nobody answered, so
          // both read as "no ratings" rather than as a score of nothing.
          if (count == 0 || average == null)
            Text(
              'No ratings in this window. Scores appear once customers answer '
              'the satisfaction survey sent when a ticket closes.',
              style: AppTypography.body.copyWith(
                color: AppColors.textSecondary,
              ),
            )
          else ...[
            Wrap(
              crossAxisAlignment: WrapCrossAlignment.end,
              spacing: 8,
              children: [
                Text(
                  average.toStringAsFixed(1),
                  style: AppTypography.h2.copyWith(
                    color: AppColors.warning600,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    'out of 5 · $count ${count == 1 ? 'rating' : 'ratings'}',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            for (final rating in const [5, 4, 3, 2, 1])
              _ratingBar(rating, distribution['$rating'] as int? ?? 0, count),
          ],
        ],
      ),
    );
  }

  Widget _ratingBar(int rating, int n, int count) {
    final share = n / count;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Text(
            '$rating',
            style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(width: 2),
          Icon(LucideIcons.star, size: 12, color: AppColors.textTertiary),
          const SizedBox(width: 8),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: Container(
                height: 8,
                color: AppColors.gray100,
                alignment: Alignment.centerLeft,
                child: FractionallySizedBox(
                  widthFactor: share.clamp(0.0, 1.0),
                  child: Container(color: AppColors.warning500),
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            '$n (${(share * 100).round()}%)',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

class _AgentsSection extends StatelessWidget {
  final List<Map<String, dynamic>> agents;
  const _AgentsSection({required this.agents});

  @override
  Widget build(BuildContext context) {
    if (agents.isEmpty) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusLg,
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'PER-AGENT',
            style: AppTypography.overline.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 8),
          for (final a in agents.take(20)) _agentRow(a),
        ],
      ),
    );
  }

  Widget _agentRow(Map<String, dynamic> a) {
    final email = a['email'] as String? ?? a['name'] as String? ?? '—';
    final handled = a['handled'] as int? ?? 0;
    final avgFrt = a['avg_frt_hours'];
    final breachRate = a['breach_rate'];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Text(
              email,
              style: AppTypography.body.copyWith(fontWeight: FontWeight.w500),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          _stat('$handled', 'cases'),
          const SizedBox(width: 12),
          _stat(_formatFrt(avgFrt), 'FRT'),
          const SizedBox(width: 12),
          _stat(_formatRate(breachRate), 'breach'),
        ],
      ),
    );
  }

  Widget _stat(String value, String label) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text(
          value,
          style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600),
        ),
        Text(
          label,
          style: AppTypography.caption.copyWith(
            color: AppColors.textTertiary,
            fontSize: 10,
          ),
        ),
      ],
    );
  }

  String _formatFrt(dynamic v) {
    if (v == null) return '—';
    final d = (v as num).toDouble();
    if (d < 1) return '${(d * 60).round()}m';
    return '${d.toStringAsFixed(1)}h';
  }

  String _formatRate(dynamic v) {
    if (v == null) return '—';
    final d = (v as num).toDouble();
    return '${(d * 100).toStringAsFixed(0)}%';
  }
}
