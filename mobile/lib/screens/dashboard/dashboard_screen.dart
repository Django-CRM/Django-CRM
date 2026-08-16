import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';
import 'package:intl/intl.dart';
import '../../core/theme/theme.dart';
import '../../data/models/dashboard_data.dart';
import '../../data/models/deal.dart';
import '../../providers/auth_provider.dart';
import '../../providers/dashboard_provider.dart';
import '../../providers/notifications_provider.dart';
import '../../routes/app_router.dart';

/// Dashboard Screen
/// Main screen with KPIs, charts, pipeline, tasks, and activity feed
class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});

  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen> {
  @override
  Widget build(BuildContext context) {
    final dashboardAsync = ref.watch(dashboardProvider);
    final authState = ref.watch(authProvider);
    final user = authState.user;
    final greetingName = _friendlyFirstName(user?.name, user?.email);

    return Scaffold(
      backgroundColor: AppColors.surfaceDim,
      body: RefreshIndicator(
        onRefresh: () => ref.read(dashboardProvider.notifier).refresh(),
        child: CustomScrollView(
          slivers: [
            // App Bar
            SliverAppBar(
              toolbarHeight: 52,
              floating: false,
              pinned: true,
              backgroundColor: AppColors.surface,
              titleSpacing: 16,
              title: Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${_getGreeting()}  ·  ${DateFormat('EEE, MMM d').format(DateTime.now())}',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                        fontSize: 11,
                        height: 1.1,
                      ),
                    ),
                    Text(
                      greetingName,
                      style: AppTypography.h3.copyWith(
                        fontSize: 18,
                        height: 1.2,
                      ),
                    ),
                  ],
                ),
              ),
              actions: const [_NotificationBell()],
            ),

            // Content
            SliverToBoxAdapter(child: _buildContent(dashboardAsync)),
          ],
        ),
      ),
      floatingActionButton: _buildExpandableFAB(context),
    );
  }

  Widget _buildContent(AsyncValue<DashboardData> dashboardAsync) {
    // Stale-while-loading: keep showing prior data during refresh.
    final data = dashboardAsync.value;

    if (data == null && dashboardAsync.isLoading) {
      return const SizedBox(
        height: 400,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    if (data == null && dashboardAsync.hasError) {
      return SizedBox(
        height: 400,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(LucideIcons.alertCircle, size: 48, color: Colors.grey[400]),
              const SizedBox(height: 16),
              Text(
                'Failed to load dashboard',
                style: AppTypography.label.copyWith(color: Colors.grey[600]),
              ),
              const SizedBox(height: 8),
              TextButton(
                onPressed: () => ref.read(dashboardProvider.notifier).refresh(),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
      );
    }

    final resolved = data ?? const DashboardData();
    return _buildBody(resolved);
  }

  Widget _buildBody(DashboardData data) {
    // The currency belongs to the payload, not to the stored org record: no
    // endpoint returns `currency_symbol`, so reading it there gave null and
    // printed every amount with a dollar sign. `/api/dashboard/` reports the
    // currency it priced these totals in, alongside them.
    final currencyFormat = NumberFormat.compactCurrency(
      symbol: Currency.fromString(data.revenueMetrics.currency).symbol,
      decimalDigits: 1,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // KPI Cards
        _buildKpiSection(data, currencyFormat),

        const SizedBox(height: 16),

        // Urgent Metrics
        if (_hasUrgentItems(data.urgentCounts)) ...[
          _buildUrgentSection(data.urgentCounts),
          const SizedBox(height: 16),
        ],

        // Pipeline Overview
        if (data.pipelineByStage.isNotEmpty) ...[
          _buildPipelineSection(
            data.pipelineByStage,
            currencyFormat,
            data.revenueMetrics.otherCurrencyCount,
          ),
          const SizedBox(height: 16),
        ],

        // Where you stand: the goals running right now.
        //
        // The backend has sent `goal_summary` on this endpoint all along and
        // nothing rendered it, so these numbers were being computed and thrown
        // away on every dashboard load. Hidden entirely when there are no
        // current goals, which is the common case for a member in an org that
        // does not set them.
        if (data.goalSummary.isNotEmpty) ...[
          _buildGoalSection(data.goalSummary, currencyFormat),
          const SizedBox(height: 16),
        ],

        // Hot Leads
        if (data.hotLeads.isNotEmpty) ...[
          _buildHotLeadsSection(data.hotLeads),
          const SizedBox(height: 16),
        ],

        // Today's Tasks
        _buildTasksSection(data.tasks),

        // Bottom spacer so the floating action button doesn't cover content.
        const SizedBox(height: 96),
      ],
    );
  }

  bool _hasUrgentItems(UrgentCounts counts) {
    return counts.overdueTasks > 0 ||
        counts.tasksDueToday > 0 ||
        counts.followupsToday > 0 ||
        counts.hotLeads > 0;
  }

  String _getGreeting() {
    final hour = DateTime.now().hour;
    if (hour < 5) return 'Working late';
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    if (hour < 22) return 'Good evening';
    return 'Working late';
  }

  /// Derive a friendly first name. Backends often default `name` to the email
  /// local-part ("aswin.1231"), so we prettify any value that looks like a
  /// username (contains dots, underscores, or digits, and no spaces).
  String _friendlyFirstName(String? name, String? email) {
    final raw = (name != null && name.trim().isNotEmpty)
        ? name.trim()
        : (email?.split('@').first ?? '');
    if (raw.isEmpty) return 'there';

    final looksLikeUsername =
        !raw.contains(' ') && RegExp(r'[._\-\d]').hasMatch(raw);
    final source = looksLikeUsername ? raw : raw.split(' ').first;

    final segments = source
        .split(RegExp(r'[._\-+]'))
        .map((s) => s.replaceAll(RegExp(r'\d+'), ''))
        .where((s) => s.isNotEmpty);
    if (segments.isEmpty) return raw.split(' ').first;
    final first = segments.first;
    return first[0].toUpperCase() + first.substring(1).toLowerCase();
  }

  Widget _buildKpiSection(DashboardData data, NumberFormat currencyFormat) {
    final cards = [
      _KpiCard(
        title: 'Pipeline',
        value: currencyFormat.format(data.revenueMetrics.pipelineValue),
        icon: LucideIcons.dollarSign,
        color: AppColors.success500,
        destination: AppRoutes.deals,
      ),
      _KpiCard(
        // Counted from the open stages, so this card agrees with the Pipeline
        // card beside it. `opportunitiesCount` counts closed deals too, which
        // is why this once read 4 next to a pipeline holding 2.
        title: 'Open Deals',
        value: data.revenueMetrics.openOpportunitiesCount.toString(),
        icon: LucideIcons.briefcase,
        color: AppColors.primary500,
        destination: AppRoutes.deals,
      ),
      _KpiCard(
        title: 'Leads',
        value: data.leadsCount.toString(),
        icon: LucideIcons.users,
        color: AppColors.warning500,
        destination: AppRoutes.leads,
      ),
      _KpiCard(
        title: 'Conversion',
        value: '${data.revenueMetrics.conversionRate.toStringAsFixed(0)}%',
        icon: LucideIcons.target,
        color: AppColors.purple500,
      ),
      // Neither of these two summarises a list the app can open: one is a
      // month-and-stage slice, the other a forecast. Like Conversion, they
      // stay plain rather than link somewhere that shows something else.
      _KpiCard(
        title: 'Won This Month',
        value: currencyFormat.format(data.revenueMetrics.wonThisMonth),
        icon: LucideIcons.trophy,
        color: AppColors.success500,
      ),
      _KpiCard(
        title: 'Weighted',
        value: currencyFormat.format(data.revenueMetrics.weightedPipeline),
        icon: LucideIcons.scale,
        color: AppColors.primary400,
      ),
    ];

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: GridView.count(
        crossAxisCount: 2,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 8,
        crossAxisSpacing: 8,
        childAspectRatio: 2.2,
        children: cards,
      ),
    );
  }

  Widget _buildUrgentSection(UrgentCounts counts) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Container(
        padding: const EdgeInsets.all(12),
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
                Icon(
                  LucideIcons.alertTriangle,
                  size: 16,
                  color: AppColors.warning600,
                ),
                const SizedBox(width: 6),
                Text(
                  'Needs Attention',
                  style: AppTypography.labelSmall.copyWith(
                    color: AppColors.warning700,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              runSpacing: 6,
              children: [
                if (counts.overdueTasks > 0)
                  _UrgentBadge(
                    label: 'Overdue',
                    count: counts.overdueTasks,
                    color: AppColors.danger500,
                    route: '${AppRoutes.tasks}?view=${AppRoutes.viewOverdue}',
                  ),
                if (counts.tasksDueToday > 0)
                  _UrgentBadge(
                    label: 'Due Today',
                    count: counts.tasksDueToday,
                    color: AppColors.warning500,
                    route: '${AppRoutes.tasks}?view=${AppRoutes.viewDueToday}',
                  ),
                if (counts.followupsToday > 0)
                  _UrgentBadge(
                    label: 'Follow-ups',
                    count: counts.followupsToday,
                    color: AppColors.primary500,
                    route: '${AppRoutes.leads}?view=${AppRoutes.viewFollowUps}',
                  ),
                if (counts.hotLeads > 0)
                  _UrgentBadge(
                    label: 'Hot Leads',
                    count: counts.hotLeads,
                    color: AppColors.success500,
                    route: '${AppRoutes.leads}?view=${AppRoutes.viewHot}',
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPipelineSection(
    List<PipelineStage> stages,
    NumberFormat currencyFormat,
    int otherCurrencyCount,
  ) {
    // Only show active pipeline stages (not closed). Selecting on the deal
    // count rather than the value, because the server prices a stage in the
    // org's currency alone: a stage whose deals are all in another currency
    // arrives worth zero and used to disappear from the chart entirely.
    final activeStages = stages
        .where((s) => !s.code.contains('CLOSED') && s.count > 0)
        .toList();

    if (activeStages.isEmpty) {
      return const SizedBox.shrink();
    }

    final maxValue = activeStages
        .map((s) => s.value)
        .reduce((a, b) => a > b ? a : b);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppLayout.borderRadiusMd,
          border: Border.all(color: AppColors.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Pipeline Overview', style: AppTypography.labelSmall),
            const SizedBox(height: 10),
            ...activeStages.map((stage) {
              final percentage = maxValue > 0 ? stage.value / maxValue : 0;
              final stageColor = _getStageColor(stage.code);

              return Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  children: [
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(
                        color: stageColor,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 6),
                    SizedBox(
                      width: 80,
                      child: Text(
                        stage.label,
                        style: AppTypography.bodySmall.copyWith(fontSize: 11),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    Expanded(
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(3),
                        child: LinearProgressIndicator(
                          value: percentage.toDouble(),
                          backgroundColor: AppColors.gray100,
                          valueColor: AlwaysStoppedAnimation(stageColor),
                          minHeight: 6,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    SizedBox(
                      width: 70,
                      child: Text(
                        // An unpriced stage reports what it does hold. The
                        // alternative was a confident zero next to deals that
                        // exist.
                        stage.value > 0
                            ? currencyFormat.format(stage.value)
                            : '${stage.count} '
                                  '${stage.count == 1 ? 'deal' : 'deals'}',
                        style: AppTypography.bodySmall.copyWith(
                          fontWeight: FontWeight.w600,
                          fontSize: 11,
                        ),
                        textAlign: TextAlign.right,
                      ),
                    ),
                  ],
                ),
              );
            }),
            if (otherCurrencyCount > 0) ...[
              const SizedBox(height: 4),
              Text(
                otherCurrencyCount == 1
                    ? '1 deal in another currency is not counted in these '
                          'totals'
                    : '$otherCurrencyCount deals in other currencies are not '
                          'counted in these totals',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textSecondary,
                  fontSize: 10,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Color _getStageColor(String code) {
    switch (code) {
      case 'PROSPECTING':
        return AppColors.gray400;
      case 'QUALIFICATION':
        return AppColors.primary400;
      case 'PROPOSAL':
        return AppColors.warning400;
      case 'NEGOTIATION':
        return AppColors.purple400;
      case 'CLOSED_WON':
        return AppColors.success500;
      case 'CLOSED_LOST':
        return AppColors.danger500;
      default:
        return AppColors.gray400;
    }
  }

  /// Progress on the goals running today, at most three.
  ///
  /// Every figure is the server's. `progress_percent` and `status` are computed
  /// over closed-won opportunities in the period, including ones assigned to
  /// people this app never fetches, so nothing here recomputes them. The colour
  /// follows `status` (pace) rather than the raw percentage, so the strip agrees
  /// with the goals screen instead of calling a goal green in week two of a
  /// quarter for being 40% of the way there.
  Widget _buildGoalSection(
    List<DashboardGoal> goals,
    NumberFormat currencyFormat,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Row(
            children: [
              const Expanded(
                child: Text(
                  'Where you stand',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                ),
              ),
              TextButton(
                onPressed: () => context.push(AppRoutes.goals),
                child: const Text('All goals'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        for (final goal in goals)
          _DashboardGoalRow(goal: goal, currencyFormat: currencyFormat),
      ],
    );
  }

  Widget _buildHotLeadsSection(List<HotLead> leads) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Hot Leads', style: AppTypography.labelSmall),
              GestureDetector(
                onTap: () => context.go(AppRoutes.leads),
                child: Text(
                  'See All',
                  style: AppTypography.bodySmall.copyWith(
                    color: AppColors.primary600,
                    fontSize: 11,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          height: 100,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            itemCount: leads.take(5).length,
            itemBuilder: (context, index) {
              final lead = leads[index];
              final initials = _initialsFor(lead.firstName, lead.lastName);
              final avatarColor = AppColors.getAvatarColor(lead.fullName);
              return GestureDetector(
                onTap: () => context.push('/leads/${lead.id}'),
                child: Container(
                  width: 200,
                  margin: const EdgeInsets.only(right: 8),
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: AppLayout.borderRadiusMd,
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 28,
                            height: 28,
                            decoration: BoxDecoration(
                              color: avatarColor.withValues(alpha: 0.12),
                              shape: BoxShape.circle,
                            ),
                            alignment: Alignment.center,
                            child: Text(
                              initials,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w700,
                                color: avatarColor,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  lead.fullName,
                                  style: AppTypography.labelSmall,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                                if (lead.company != null)
                                  Text(
                                    lead.company!,
                                    style: AppTypography.bodySmall.copyWith(
                                      color: AppColors.textSecondary,
                                      fontSize: 11,
                                    ),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                              ],
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 5,
                              vertical: 2,
                            ),
                            decoration: BoxDecoration(
                              color: AppColors.danger100,
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              'HOT',
                              style: TextStyle(
                                fontSize: 9,
                                fontWeight: FontWeight.w700,
                                color: AppColors.danger600,
                                letterSpacing: 0.5,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const Spacer(),
                      Row(
                        children: [
                          Icon(
                            LucideIcons.clock,
                            size: 11,
                            color: AppColors.textTertiary,
                          ),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              _lastContactedLabel(lead.lastContacted),
                              style: AppTypography.bodySmall.copyWith(
                                color: AppColors.textTertiary,
                                fontSize: 11,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
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

  String _initialsFor(String first, String last) {
    final f = first.isNotEmpty ? first[0] : '';
    final l = last.isNotEmpty ? last[0] : '';
    final combined = '$f$l'.toUpperCase();
    return combined.isEmpty ? '?' : combined;
  }

  String _lastContactedLabel(DateTime? when) {
    if (when == null) return 'Not contacted yet';
    final diff = DateTime.now().difference(when);
    if (diff.inMinutes < 1) return 'Contacted just now';
    if (diff.inHours < 1) return 'Contacted ${diff.inMinutes}m ago';
    if (diff.inDays < 1) return 'Contacted ${diff.inHours}h ago';
    if (diff.inDays < 7) return 'Contacted ${diff.inDays}d ago';
    if (diff.inDays < 30) {
      final weeks = (diff.inDays / 7).floor();
      return 'Contacted ${weeks}w ago';
    }
    return 'Contacted ${DateFormat.MMMd().format(when)}';
  }

  Widget _buildTasksSection(List<DashboardTask> tasks) {
    final upcomingTasks = tasks.where((t) => !t.isCompleted).take(5).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Upcoming Tasks', style: AppTypography.labelSmall),
              GestureDetector(
                onTap: () => context.go(AppRoutes.tasks),
                child: Text(
                  'See All',
                  style: AppTypography.bodySmall.copyWith(
                    color: AppColors.primary600,
                    fontSize: 11,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Container(
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: AppLayout.borderRadiusMd,
              border: Border.all(color: AppColors.border),
            ),
            child: upcomingTasks.isEmpty
                ? Padding(
                    padding: const EdgeInsets.all(16),
                    child: Center(
                      child: Column(
                        children: [
                          Icon(
                            LucideIcons.checkCircle,
                            size: 24,
                            color: Colors.grey[300],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            'No upcoming tasks',
                            style: AppTypography.bodySmall.copyWith(
                              color: AppColors.textTertiary,
                            ),
                          ),
                        ],
                      ),
                    ),
                  )
                : Column(
                    children: upcomingTasks.asMap().entries.map((entry) {
                      final index = entry.key;
                      final task = entry.value;
                      return _TaskItem(
                        task: task,
                        showDivider: index < upcomingTasks.length - 1,
                        onTap: () => context.push('/tasks/${task.id}'),
                      );
                    }).toList(),
                  ),
          ),
        ),
      ],
    );
  }

  Widget _buildExpandableFAB(BuildContext context) {
    return FloatingActionButton(
      onPressed: () {
        showModalBottomSheet(
          context: context,
          builder: (context) => Container(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                ListTile(
                  leading: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: AppColors.primary100,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(
                      LucideIcons.userPlus,
                      color: AppColors.primary600,
                      size: 20,
                    ),
                  ),
                  title: const Text('Add Lead'),
                  subtitle: const Text('Create a new lead'),
                  onTap: () {
                    context.pop();
                    context.push(AppRoutes.leadCreate);
                  },
                ),
                ListTile(
                  leading: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: AppColors.success100,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(
                      LucideIcons.plusCircle,
                      color: AppColors.success600,
                      size: 20,
                    ),
                  ),
                  title: const Text('Add Deal'),
                  subtitle: const Text('Create a new deal'),
                  onTap: () {
                    context.pop();
                    // Navigate to deal create
                  },
                ),
                ListTile(
                  leading: Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: AppColors.warning100,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(
                      LucideIcons.checkSquare,
                      color: AppColors.warning600,
                      size: 20,
                    ),
                  ),
                  title: const Text('Add Task'),
                  subtitle: const Text('Create a new task'),
                  onTap: () {
                    context.pop();
                    // Navigate to task create
                  },
                ),
              ],
            ),
          ),
        );
      },
      child: const Icon(LucideIcons.plus),
    );
  }
}

// KPI Card Widget
class _KpiCard extends StatelessWidget {
  final String title;
  final String value;
  final IconData icon;
  final Color color;

  /// Where this number lives in full. Cards that summarise a list link to it;
  /// Conversion has no list of its own, so it stays a plain card rather than
  /// being given an arbitrary destination.
  final String? destination;

  const _KpiCard({
    required this.title,
    required this.value,
    required this.icon,
    required this.color,
    this.destination,
  });

  @override
  Widget build(BuildContext context) {
    final card = Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppLayout.borderRadiusMd,
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(icon, size: 16, color: color),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  value,
                  style: AppTypography.label.copyWith(
                    fontWeight: FontWeight.w700,
                    fontSize: 16,
                    letterSpacing: -0.3,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  title,
                  style: AppTypography.bodySmall.copyWith(
                    color: AppColors.textSecondary,
                    fontSize: 11,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ],
      ),
    );

    if (destination == null) return card;
    return InkWell(
      onTap: () => GoRouter.of(context).go(destination!),
      borderRadius: AppLayout.borderRadiusMd,
      child: card,
    );
  }
}

// Urgent Badge Widget
class _UrgentBadge extends StatelessWidget {
  final String label;
  final int count;
  final Color color;

  /// Where tapping the badge goes. Each destination opens its list already
  /// narrowed to the same set this number counted, so the list length agrees
  /// with the badge. A badge with nowhere honest to land would be worse than
  /// one that does nothing, so this is required rather than optional.
  final String route;

  const _UrgentBadge({
    required this.label,
    required this.count,
    required this.color,
    required this.route,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        InkWell(
          onTap: () => context.go(route),
          borderRadius: BorderRadius.circular(4),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  count.toString(),
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    color: color,
                  ),
                ),
                const SizedBox(width: 3),
                Text(label, style: TextStyle(fontSize: 10, color: color)),
                const SizedBox(width: 2),
                Icon(LucideIcons.chevronRight, size: 11, color: color),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

// Task Item
class _TaskItem extends StatelessWidget {
  final DashboardTask task;
  final bool showDivider;
  final VoidCallback? onTap;

  const _TaskItem({required this.task, this.showDivider = true, this.onTap});

  Color _getPriorityColor() {
    switch (task.priority.toLowerCase()) {
      case 'urgent':
        return AppColors.purple500;
      case 'high':
        return AppColors.danger500;
      case 'medium':
        return AppColors.warning500;
      case 'low':
      default:
        return AppColors.success500;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        InkWell(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: Row(
              children: [
                Container(
                  width: 3,
                  height: 32,
                  decoration: BoxDecoration(
                    color: _getPriorityColor(),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        task.title,
                        style: AppTypography.body.copyWith(
                          fontWeight: FontWeight.w500,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (task.relatedTo != null || task.dueDate != null)
                        Row(
                          children: [
                            if (task.relatedTo != null)
                              Flexible(
                                child: Text(
                                  task.relatedTo!,
                                  style: AppTypography.caption.copyWith(
                                    color: AppColors.textTertiary,
                                  ),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            if (task.relatedTo != null && task.dueDate != null)
                              Text(
                                ' • ',
                                style: TextStyle(
                                  color: AppColors.gray300,
                                  fontSize: 12,
                                ),
                              ),
                            if (task.dueDate != null)
                              Text(
                                DateFormat.MMMd().format(task.dueDate!),
                                style: AppTypography.caption.copyWith(
                                  color: AppColors.textTertiary,
                                ),
                              ),
                          ],
                        ),
                    ],
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: _getPriorityColor().withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    task.priority,
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: _getPriorityColor(),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        if (showDivider) const Divider(height: 1, indent: 24),
      ],
    );
  }
}

/// The bell, with the unread count on it.
///
/// It lives on the dashboard because that is the screen people open first and
/// the one they return to; a notification nobody is told about is a
/// notification that did not happen. The count is the server's figure over the
/// whole feed, not over the page of rows fetched, so it does not under-report
/// once there are more than fifty.
class _NotificationBell extends ConsumerWidget {
  const _NotificationBell();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadNotificationCountProvider);
    return IconButton(
      constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
      tooltip: unread == 0
          ? 'Notifications'
          : '$unread unread ${unread == 1 ? 'notification' : 'notifications'}',
      onPressed: () => context.push(AppRoutes.notifications),
      icon: Stack(
        clipBehavior: Clip.none,
        children: [
          Icon(LucideIcons.bell, size: 21, color: AppColors.textSecondary),
          if (unread > 0)
            Positioned(
              top: -3,
              right: -4,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                constraints: const BoxConstraints(minWidth: 15),
                decoration: BoxDecoration(
                  color: AppColors.danger600,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  // Past 99 the exact number stops being the point and the
                  // badge stops fitting.
                  unread > 99 ? '99+' : '$unread',
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w700,
                    color: Colors.white,
                    height: 1.3,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// One goal on the dashboard strip.
///
/// A revenue target is money and a deals or activities target is a count, so
/// the unit follows the goal type rather than defaulting to currency: an
/// activities quota rendered as "$40" is what a `!= 'DEALS_CLOSED'` test
/// produced elsewhere in this app.
class _DashboardGoalRow extends StatelessWidget {
  const _DashboardGoalRow({required this.goal, required this.currencyFormat});

  final DashboardGoal goal;
  final NumberFormat currencyFormat;

  Color get _colour {
    switch (goal.status) {
      case 'completed':
        return AppColors.success600;
      case 'at_risk':
        return AppColors.warning600;
      case 'behind':
        return AppColors.danger600;
      default:
        return AppColors.primary600;
    }
  }

  String _value(double value) {
    switch (goal.goalType) {
      case 'DEALS_CLOSED':
        final deals = value.round();
        return '$deals ${deals == 1 ? 'deal' : 'deals'}';
      case 'ACTIVITIES':
        final logged = value.round();
        return '$logged ${logged == 1 ? 'activity' : 'activities'}';
      default:
        return currencyFormat.format(value);
    }
  }

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => context.push(AppRoutes.goals),
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border.all(color: Colors.grey.shade200),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    goal.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '${goal.progressPercent}%',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: _colour,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: (goal.progressPercent / 100).clamp(0.0, 1.0),
                minHeight: 6,
                backgroundColor: Colors.grey.shade200,
                valueColor: AlwaysStoppedAnimation(_colour),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '${_value(goal.progressValue)} of ${_value(goal.targetValue)}',
              style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            ),
          ],
        ),
      ),
    );
  }
}
